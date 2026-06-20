"""Firestore-backed A2A TaskStore so task state survives restarts and is shared across instances.

ADK's ``to_a2a()`` defaults to an in-memory TaskStore. Under Cloud Run autoscaling that breaks the
A2A polling contract: ``message/send`` creates a task on one instance, but the client's later
``tasks/get`` can be load-balanced to a *different* instance that never saw it → "task not found".
Any instance restart/scale-in also loses every in-flight task. Persisting tasks in Firestore (the
DB we already use for escrow) makes the A2A surface correct under more than one instance.

The a2a ``TaskStore`` interface is three async methods (save/get/delete); ``Task`` is a pydantic
model, so we round-trip via ``model_dump(mode="json")`` / ``Task.model_validate``. Blocking
Firestore I/O is pushed to a thread so it never stalls the web event loop. Firestore docs cap at
~1 MiB, which is ample here: our A2A artifacts are a delivery summary + manifest URL (text), not
the binary assets themselves.
"""
from __future__ import annotations

import asyncio

from a2a.server.tasks import TaskStore
from a2a.types import Task

from . import escrow

_TASKS = "a2a_tasks"


class FirestoreTaskStore(TaskStore):
    """Persist A2A tasks in Firestore so they outlive a single process/instance."""

    def _doc(self, task_id: str):
        return escrow.db().collection(_TASKS).document(task_id)

    async def save(self, task: Task, context: "ServerCallContext | None" = None) -> None:
        data = task.model_dump(mode="json")
        await asyncio.to_thread(self._doc(task.id).set, data)

    async def get(self, task_id: str, context: "ServerCallContext | None" = None) -> "Task | None":
        snap = await asyncio.to_thread(self._doc(task_id).get)
        if not snap.exists:
            return None
        return Task.model_validate(snap.to_dict())

    async def delete(self, task_id: str, context: "ServerCallContext | None" = None) -> None:
        await asyncio.to_thread(self._doc(task_id).delete)
