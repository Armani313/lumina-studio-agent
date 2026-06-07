"""Firestore-backed jobs + escrow state machine for the Lumina marketplace.

Escrow lifecycle:  Funded -> InProgress -> Delivered -> Released   (or -> Refunded on failure)
"""
from __future__ import annotations

import datetime as _dt
import re
import uuid

from google.cloud import firestore

from lumina.config import settings

JOBS = "marketplace_jobs"
INBOUND = "marketplace_inbound"

FUNDED = "Funded"
IN_PROGRESS = "InProgress"
DELIVERED = "Delivered"
RELEASED = "Released"
REFUNDED = "Refunded"

_db = None


def db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=settings.project)
    return _db


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def create_job(brief: str, product_image_uri: str, brand_link: str, price: int) -> str:
    jid = uuid.uuid4().hex[:12]
    db().collection(JOBS).document(jid).set(
        {
            "id": jid,
            "brief": brief,
            "product_image_uri": product_image_uri,
            "brand_link": brand_link,
            "price": price,
            "status": IN_PROGRESS,
            "escrow": FUNDED,
            "package": None,
            "created_at": _now(),
            "events": [{"t": _now(), "msg": "Order funded; escrow held; dispatched to agent"}],
        }
    )
    return jid


def get_job(jid: str) -> dict | None:
    snap = db().collection(JOBS).document(jid).get()
    return snap.to_dict() if snap.exists else None


def update_job(jid: str, **fields) -> None:
    db().collection(JOBS).document(jid).update(fields)


def add_event(jid: str, msg: str) -> None:
    db().collection(JOBS).document(jid).update(
        {"events": firestore.ArrayUnion([{"t": _now(), "msg": msg}])}
    )


def log_inbound(payload: dict) -> str:
    """Persist a raw inbound marketplace payload so we can map its contract."""
    rid = uuid.uuid4().hex[:12]
    db().collection(INBOUND).document(rid).set({"id": rid, "at": _now(), "payload": payload})
    return rid


def recent_inbound(limit: int = 5) -> list[dict]:
    docs = (
        db().collection(INBOUND).order_by("at", direction=firestore.Query.DESCENDING)
        .limit(limit).stream()
    )
    return [d.to_dict() for d in docs]


def set_delivered(jid: str, package: dict) -> None:
    update_job(jid, status=DELIVERED, package=package)
    add_event(jid, "Package delivered; awaiting client acceptance")


def set_failed(jid: str, err: str) -> None:
    update_job(jid, status="Failed", escrow=REFUNDED)
    add_event(jid, f"Failed; escrow refunded ({err[:120]})")


def accept_job(jid: str) -> bool:
    j = get_job(jid)
    if not j or j.get("status") != DELIVERED:
        return False
    update_job(jid, status="Completed", escrow=RELEASED)
    add_event(jid, "Client accepted; escrow released to agent")
    return True


_ASSET_RE = re.compile(r"gs://[^\s\"')]+\.(?:png|jpg|jpeg|webp|mp4)", re.I)


def extract_assets(state: dict) -> list[dict]:
    """Pull asset gs:// URIs out of the agents' output strings, categorised by kind."""
    assets: list[dict] = []
    seen: set[str] = set()
    for key, kind in (("images", "image"), ("cards", "card"), ("videos", "video")):
        for uri in _ASSET_RE.findall(str(state.get(key) or "")):
            if uri not in seen:
                seen.add(uri)
                assets.append({"type": kind, "uri": uri})
    return assets
