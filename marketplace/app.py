"""Lumina Studio Marketplace — FastAPI: order UI + escrow API (Firestore) + in-process agent run.

The same agent is also published as an A2A service (see marketplace/a2a_server.py) so other
agents can discover and hire it via its AgentCard.

Run:  .venv/bin/uvicorn marketplace.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
import urllib.parse
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from google.cloud import storage
from google.cloud.exceptions import NotFound
from google.genai import types
from google.adk.runners import InMemoryRunner

from lumina.agent import root_agent
from lumina.clients import gemini_client
from lumina.config import settings
from lumina.pricing import BASE_PRICE, PER_CARD, PER_IMAGE, PER_VIDEO
from lumina.tools.delivery import mime_for_uri, upload_bytes

from . import escrow
from .a2a_server import a2a_app


@asynccontextmanager
async def _lifespan(_app):
    # Propagate the mounted A2A app's lifespan so its discoverable AgentCard route is built.
    async with a2a_app.router.lifespan_context(a2a_app):
        yield


app = FastAPI(title="Lumina Studio Marketplace", lifespan=_lifespan)
# Publish the agent over A2A on the same service; AgentCard at /a2a/.well-known/agent-card.json
app.mount("/a2a", a2a_app)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return INDEX_HTML


# --- External marketplace integration (Simple Webhook v1: bearer-auth inbound + async callback) ---
# The shared connection token is held in an env var (NEVER in code/git): MARKETPLACE_TOKEN.
MARKETPLACE_TOKEN = os.getenv("MARKETPLACE_TOKEN", "")
SERVICE_URL = os.getenv("SERVICE_URL", "https://lumina-marketplace-587790795280.us-central1.run.app")


def _bearer_ok(authorization: str) -> bool:
    """Constant-time compare of the request's Bearer token against our connection token."""
    if not MARKETPLACE_TOKEN:
        return False
    presented = authorization[7:].strip() if authorization[:7].lower() == "bearer " else authorization.strip()
    return hmac.compare_digest(presented, MARKETPLACE_TOKEN)


def _first(d: dict, keys: list[str]) -> str:
    for k in keys:
        v = d.get(k)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""


_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _image_from_attachments(attachments: object) -> str:
    """Return the first image attachment's URL.

    The external marketplace delivers the buyer's product photo as an entry in
    `task.attachments[]` (orders) or `consult.attachments[]` (chat) — NOT in
    `inputs.product_image_url`, which it sends empty. We must read it from here.
    """
    if not isinstance(attachments, list):
        return ""
    for a in attachments:
        if not isinstance(a, dict):
            continue
        url = a.get("url") or a.get("href") or a.get("downloadUrl") or ""
        if not url or not isinstance(url, str):
            continue
        mime = (a.get("mimeType") or a.get("contentType") or a.get("type") or "").lower()
        name = (a.get("name") or a.get("filename") or "").lower()
        path = url.split("?", 1)[0].lower()
        if mime.startswith("image/") or name.endswith(_IMG_EXTS) or path.endswith(_IMG_EXTS):
            return url
    return ""


def _proxy_url(gs_uri: str) -> str:
    return f"{SERVICE_URL}/api/asset?uri=" + urllib.parse.quote(gs_uri, safe="")


def _fetch_image_to_gcs(url: str) -> str | None:
    """Download a buyer-provided product image URL into our bucket; return its gs:// URI."""
    try:
        r = httpx.get(url, timeout=45, follow_redirects=True)
        r.raise_for_status()
        ct = (r.headers.get("content-type") or "").lower()
        ext = "png" if "png" in ct else ("webp" if "webp" in ct else "jpg")
        return upload_bytes(r.content, f"inputs/mkt_{uuid.uuid4().hex}.{ext}", ct or "image/png")
    except Exception:
        return None


def _post_callback(url: str, token: str, body: dict) -> None:
    if not url:
        return
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        httpx.post(url, json=body, headers=headers, timeout=45)
    except Exception:
        pass


def _build_outputs(state: dict) -> dict:
    """Map the agent's delivered package into marketplace outputs (viewable proxy URLs + copy)."""
    assets = escrow.extract_assets(state)
    images = [_proxy_url(a["uri"]) for a in assets if a["type"] == "image"]
    videos = [_proxy_url(a["uri"]) for a in assets if a["type"] == "video"]
    cards = [_proxy_url(a["uri"]) for a in assets if a["type"] == "card"]
    copy = state.get("copy_full") or state.get("copy_doc") or {}
    title = copy.get("title") if isinstance(copy, dict) else ""
    md = [f"## {title}" if title else "## Your on-brand content package"]
    if isinstance(copy, dict) and copy.get("short"):
        md.append(copy["short"])
    if images:
        md.append("**Images**\n" + "\n".join(f"![image]({u})" for u in images))
    if cards:
        md.append("**Product cards**\n" + "\n".join(f"![card]({u})" for u in cards))
    if videos:
        md.append("**Videos**\n" + "\n".join(f"- [video]({u})" for u in videos))
    return {"markdown": "\n\n".join(md), "images": images, "cards": cards, "videos": videos, "copy": copy}


async def _run_marketplace_job(inputs: dict, brief: str, image_url: str, brand_link: str,
                               cb_url: str, cb_token: str, delivery_id: str = "") -> None:
    """Background: run the full agent for an external-marketplace task, then POST the result.

    Always finishes with exactly one callback: {status: "completed", outputs} once real assets
    exist, otherwise {status: "failed", error}. Never reports a hollow completion.
    """
    tag = f"[mkt-job {delivery_id or '-'}]"

    def _cb(body: dict) -> None:
        if delivery_id:
            body.setdefault("deliveryId", delivery_id)
        extra = f" error={body['error']!r}" if body.get("status") == "failed" else ""
        print(f"{tag} -> callback status={body.get('status')}{extra}", flush=True)
        _post_callback(cb_url, cb_token, body)

    print(f"{tag} start image_url={(image_url or '')[:90]!r} brief={(brief or '')[:80]!r}", flush=True)
    try:
        if not image_url:
            _cb({"status": "failed", "error": "no product image in task inputs or attachments"})
            return
        product_uri = _fetch_image_to_gcs(image_url)
        if not product_uri:
            _cb({"status": "failed", "error": "could not fetch product image"})
            return
        full_brief = brief if not brand_link else f"{brief}\nBrand link: {brand_link}"
        seed = {"product_image_uri": product_uri, "brief_text": full_brief, "brand_link": brand_link}
        spec = _spec_from_inputs(inputs)
        if spec:
            seed["spec"] = spec
        print(f"{tag} fetched product->gcs; running agent (spec={'yes' if spec else 'no'})", flush=True)
        runner = InMemoryRunner(agent=root_agent, app_name="lumina_ext")
        uid = uuid.uuid4().hex
        session = await runner.session_service.create_session(app_name="lumina_ext", user_id=uid, state=seed)
        msg = types.Content(role="user", parts=[types.Part(text=full_brief)])
        async for _ in runner.run_async(user_id=uid, session_id=session.id, new_message=msg):
            pass
        st = dict((await runner.session_service.get_session(
            app_name="lumina_ext", user_id=uid, session_id=session.id)).state)
        outputs = _build_outputs(st)
        print(f"{tag} agent done; assets images={len(outputs.get('images') or [])} "
              f"cards={len(outputs.get('cards') or [])} videos={len(outputs.get('videos') or [])}", flush=True)
        # Guard: never report a hollow "completed" with no media (the original failure class).
        if not (outputs.get("images") or outputs.get("videos") or outputs.get("cards")):
            _cb({"status": "failed", "error": "generation produced no deliverable assets"})
            return
        _cb({"status": "completed", "outputs": outputs})
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"{tag} EXCEPTION {e!r}\n{traceback.format_exc()}", flush=True)
        _cb({"status": "failed", "error": str(e)[:300]})


def _quote_cents(spec: dict, min_cents: int = 0) -> int:
    """Our per-item price for an agreed spec, in integer cents (>= the platform floor)."""
    imgs = int(spec.get("images") or spec.get("image_count") or 0)
    vids = spec.get("videos")
    vids = len(vids) if isinstance(vids, list) else int(vids or 0)
    cards = int(spec.get("cards") or spec.get("card_count") or 0)
    cents = (BASE_PRICE + PER_IMAGE * imgs + PER_VIDEO * vids + PER_CARD * cards) * 100
    return max(cents, min_cents, 50)


def _spec_from_inputs(inputs: dict) -> dict | None:
    """Map an accepted proposal's spec (which arrives as task.inputs) into a ProductionSpec dict."""
    if not any(k in inputs for k in ("images", "image_count", "videos", "cards", "card_count", "platforms", "video_kinds")):
        return None
    raw_v = inputs.get("videos")
    if isinstance(raw_v, list):
        kinds = [v.get("kind") if isinstance(v, dict) else str(v) for v in raw_v]
    elif inputs.get("video_kinds"):
        kinds = list(inputs["video_kinds"])
    else:
        kinds = ["360", "voiceover", "ugc", "macro"][: int(raw_v or 0)]
    videos = [{"kind": k, "aspect_ratio": "9:16", "duration_seconds": 8} for k in kinds if k]
    return {
        "platforms": inputs.get("platforms") or [],
        "image_count": max(1, min(int(inputs.get("images") or inputs.get("image_count") or 6), 20)),
        "image_aspect_ratios": inputs.get("image_aspect_ratios") or ["4:5", "1:1"],
        "videos": videos,
        "card_count": max(0, min(int(inputs.get("cards") or inputs.get("card_count") or 0), 5)),
        "card_aspect_ratio": "4:5",
        "copy_channels": inputs.get("platforms") or ["instagram"],
        "language": inputs.get("language") or "",
        "mood": inputs.get("mood") or "",
    }


_CONSULT_SYS = (
    'You are the order consultant for "Lumina — Product Content Studio", an AI agent that turns a '
    "product PHOTO + brief into a complete on-brand content package: photorealistic product images "
    "(hero/macro/lifestyle/flat-lay/e-commerce), short videos (360° spin, voiceover ad, UGC, macro), "
    "designed product cards, and multi-variant marketing copy — faithful to the real product and the "
    "brand.\n"
    "You chat with a potential buyer to agree the SCOPE and PRICE of their order.\n"
    "RULES:\n"
    "- Reply in the SAME LANGUAGE as the buyer's latest message.\n"
    "- ONE short, friendly, expert reply (<= ~600 chars).\n"
    "- To deliver we NEED the buyer's product PHOTO — if no product image URL was given/uploaded, ask "
    "for it (a direct image URL is ideal).\n"
    "- Gather target platform(s) (Instagram/TikTok/Amazon/web…) and rough volume (images, videos, "
    "cards). Infer sensible defaults from the platform; don't over-ask.\n"
    "- PRICING: base $29 + $4/image + $12/video + $6/card. Quote with this exact formula.\n"
    "- On the FIRST turn or when key info is missing, just CLARIFY (propose=false) UNLESS mode is "
    "'price'. When you have platform + rough scope, OR mode is 'price', PROPOSE a concrete plan.\n"
    "Return STRICT JSON: {\n"
    '  "text": "<reply to the buyer, in their language; if proposing, summarize scope + total price '
    'and ask them to confirm>",\n'
    '  "propose": <true only when proposing a concrete plan now, else false>,\n'
    '  "spec": null OR {"platforms":[..],"images":<int>,"videos":<int>,"cards":<int>,'
    '"image_aspect_ratios":[..],"video_kinds":[..],"brief":"<one line>",'
    '"product_image_url":"<url or empty>","language":"<buyer language>","mood":"<optional>"},\n'
    '  "etaMinutes": <int 10-40>\n}'
)


def _consult_reply(consult: dict) -> dict:
    """Run one consultation turn and return the marketplace consult reply JSON."""
    mode = consult.get("mode") or "interview"
    message = consult.get("message") or ""
    history = consult.get("history") or []
    pricing = consult.get("pricing") or {}
    min_cents = int(pricing.get("minPriceCents") or 0)
    photo_url = _image_from_attachments(consult.get("attachments"))

    lines = [f"{h.get('sender', 'user')}: {h.get('text', '')}" for h in history[-12:]]
    if message and (not history or history[-1].get("text") != message):
        lines.append(f"user: {message}")
    convo = "\n".join(lines) or "(no messages yet)"
    photo_note = (
        "\n\nNOTE: The buyer has ALREADY attached their product photo to this conversation. "
        "Treat the product image as PROVIDED — do NOT ask for a product image URL."
        if photo_url else ""
    )

    fallback = "Tell me about your product and where you'll publish it — I'll put together a package."
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=f"{_CONSULT_SYS}\n\nConversation so far:\n{convo}\n\nCurrent consult mode: {mode}{photo_note}",
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=2048),
        )
        data = json.loads(resp.text)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    text = (data.get("text") or "").strip()[:6000] or fallback
    out: dict = {"text": text}
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
    if (bool(data.get("propose")) or mode == "price") and spec:
        # Carry the attached photo into the proposal so the accepted task arrives with a usable image.
        if photo_url and not spec.get("product_image_url"):
            spec["product_image_url"] = photo_url
        cents = _quote_cents(spec, min_cents)
        eta = int(data.get("etaMinutes") or 30)
        eta = min(40, max(10, eta))
        out["proposal"] = {"spec": spec, "quoteCents": cents, "etaMinutes": eta}
        if mode == "price":
            out["suggestedPriceCents"] = cents
    return out


@app.get("/api/marketplace/webhook")
def marketplace_health():
    """Liveness/validation ping for the marketplace (no auth — reveals nothing sensitive)."""
    return {"status": "ok", "service": "lumina", "configured": bool(MARKETPLACE_TOKEN)}


@app.post("/api/marketplace/webhook")
async def marketplace_webhook(
    request: Request,
    authorization: str = Header(default=""),
    x_docs: str = Header(default=""),
):
    """Receive an event from the marketplace (Simple Webhook v1.1).

    Event routing (contract link arrives in the X-Docs request header):
      • consult.message                      -> synchronous text reply (+ optional proposal)
      • task.test / connection check         -> synchronous "connected" stub (the ONLY stub)
      • task.created / task.revision_requested (real orders)
            -> respond {status: "accepted"} now; the finished package is POSTed to
               callback.url (Bearer callback.bearerToken) within the offer's ETA.

    A real order is NEVER answered with the "connected" stub, and a synchronous
    {status: "completed"} is only ever returned for the test handshake.
    """
    if not _bearer_ok(authorization):
        raise HTTPException(401, "invalid token")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"_list": payload}
    # Persist the contract link (X-Docs) with the body so we can verify the exact schema later.
    escrow.log_inbound({**payload, "_xdocs": x_docs} if x_docs else payload)

    event = str(payload.get("event") or "").lower()

    # Consultation chat: reply synchronously with text (+ optional one-click proposal).
    # Run the blocking LLM call off the event loop; keep well under the platform's 20s limit.
    if event == "consult.message" or isinstance(payload.get("consult"), dict):
        return await asyncio.to_thread(_consult_reply, payload.get("consult") or {})

    # Connection test / handshake: the ONLY event answered synchronously as "completed".
    if event in ("task.test", "test", "connection.test", "ping") or bool(payload.get("test")):
        return {
            "status": "completed",
            "outputs": {
                "markdown": "Lumina is connected and ready. Place a real order with a product "
                            "photo to receive a full on-brand content package — photorealistic "
                            "images, short videos, product cards and marketing copy.",
            },
        }

    task = payload.get("task") or {}
    inputs = task.get("inputs") or {}
    callback = payload.get("callback") or {}
    cb_url, cb_token = callback.get("url") or "", callback.get("bearerToken") or ""
    delivery_id = payload.get("deliveryId") or task.get("id") or ""

    # Per the platform contract the buyer's photo is delivered as a task ATTACHMENT; inputs may
    # carry a placeholder (e.g. "user_uploaded_image") or nothing, so the attachment is the source
    # of truth. Only fall back to an inputs value when it's an actual http(s) URL.
    image_url = _image_from_attachments(task.get("attachments"))
    if not image_url:
        candidate = _first(inputs, ["product_image_url", "image_url", "image", "product_photo", "photo", "product_image"])
        if candidate.startswith(("http://", "https://")):
            image_url = candidate
    brief = _first(inputs, ["brief", "description", "prompt", "topic", "task", "instructions", "details"])
    brand_link = _first(inputs, ["brand_link", "brand_url", "website", "url"])
    # On a revision request, fold the buyer's feedback into the brief so the re-run addresses it.
    revision = _first(task, ["revisionInstructions", "revision_instructions", "feedback"])
    if revision:
        brief = f"{brief}\n\nRevision requested by buyer: {revision}".strip()

    # Anything with no order signal (no task/inputs/attachments) is acknowledged, never faked.
    if not (image_url or inputs or task.get("attachments")):
        return {"status": "ignored", "note": f"no actionable order in event '{event or 'unknown'}'"}

    # Real order: long-running -> accept now; deliver (or report failure) via async callback.
    threading.Thread(
        target=lambda: asyncio.run(
            _run_marketplace_job(inputs, brief, image_url, brand_link, cb_url, cb_token, delivery_id)),
        daemon=True,
    ).start()
    return {"status": "accepted"}


@app.get("/api/marketplace/inbound")
def marketplace_inbound(token: str = ""):
    """Token-gated: inspect the most recent captured payloads (to map the marketplace's contract)."""
    if not _bearer_ok("Bearer " + token):
        raise HTTPException(401, "unauthorized")
    return {"recent": escrow.recent_inbound(5)}


@app.post("/api/jobs")
async def create_job(
    description: str = Form(...),
    brand_link: str = Form(""),
    product_photo: UploadFile = File(...),
):
    data = await product_photo.read()
    if not data:
        raise HTTPException(400, "empty product photo")
    uri = upload_bytes(
        data, f"inputs/{uuid.uuid4().hex}.png", mime_for_uri(product_photo.filename or ".png")
    )
    brief = description if not brand_link else f"{description}\nBrand link: {brand_link}"
    jid = escrow.create_job(brief, uri, brand_link, price=49)
    # Run the agent in a separate thread+loop so its blocking tool calls (image/video gen)
    # never freeze the web event loop serving the UI/polling.
    threading.Thread(
        target=lambda: asyncio.run(_run_job(jid, brief, uri)), daemon=True
    ).start()
    return {"job_id": jid}


@app.get("/api/jobs/{jid}")
def read_job(jid: str):
    j = escrow.get_job(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.post("/api/jobs/{jid}/accept")
def accept(jid: str):
    if not escrow.accept_job(jid):
        raise HTTPException(400, "job not in Delivered state")
    return {"ok": True}


def _content_type(blob_path: str) -> str:
    low = blob_path.lower()
    if low.endswith(".mp4"):
        return "video/mp4"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "image/png"


@app.get("/api/asset")
def asset(uri: str, request: Request):
    """Proxy a gs:// asset through ADC so the browser needn't have GCS access.

    Supports HTTP Range requests (206 Partial Content) — required for <video> playback,
    seeking and download in browsers (Safari/iOS won't play a video without range support).
    """
    if not uri.startswith("gs://"):
        raise HTTPException(400, "bad uri")
    bucket_name, _, blob_path = uri[5:].partition("/")
    blob = storage.Client(project=settings.project).bucket(bucket_name).blob(blob_path)
    try:
        blob.reload()  # populate size; raises NotFound if the object is missing
    except NotFound:
        raise HTTPException(404, "asset not found")
    size = blob.size or 0
    ctype = _content_type(blob_path)
    base_headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes=") and size:
        spec = range_header[len("bytes="):].split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        try:
            if not start_s and end_s:  # suffix range: last N bytes
                start, end = max(0, size - int(end_s)), size - 1
            else:
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
        except ValueError:
            raise HTTPException(416, "invalid range")
        start, end = max(0, start), min(end, size - 1)
        if start > end:
            raise HTTPException(416, "range not satisfiable")
        chunk = blob.download_as_bytes(start=start, end=end)  # end is inclusive in GCS
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(len(chunk)),
        }
        return Response(content=chunk, status_code=206, media_type=ctype, headers=headers)

    data = blob.download_as_bytes()
    return Response(
        content=data, media_type=ctype,
        headers={**base_headers, "Content-Length": str(len(data))},
    )


async def _run_job(jid: str, brief: str, product_uri: str) -> None:
    """Background: run the full agent graph for this order and record the result + escrow state."""
    try:
        runner = InMemoryRunner(agent=root_agent, app_name="lumina_mkt")
        session = await runner.session_service.create_session(
            app_name="lumina_mkt", user_id=jid,
            state={"product_image_uri": product_uri, "brief_text": brief},
        )
        msg = types.Content(role="user", parts=[types.Part(text=brief)])
        seen: set[str] = set()
        async for ev in runner.run_async(user_id=jid, session_id=session.id, new_message=msg):
            a = getattr(ev, "author", None) or "agent"
            if a not in seen:
                seen.add(a)
                escrow.add_event(jid, f"stage: {a}")
            # Granular sub-progress (esp. during the long QA loop) so the UI never looks frozen.
            if ev.content and ev.content.parts:
                for p in ev.content.parts:
                    fc = getattr(p, "function_call", None)
                    if fc and getattr(fc, "name", None):
                        escrow.add_event(jid, f"  {a} → {fc.name}")
        s = await runner.session_service.get_session(
            app_name="lumina_mkt", user_id=jid, session_id=session.id
        )
        st = dict(s.state)
        package = {
            "assets": escrow.extract_assets(st),
            "copy": st.get("copy_full") or st.get("copy_doc"),
            "qa_report": st.get("qa_report"),
            "qa_scores": st.get("qa_scores") or [],
        }
        escrow.set_delivered(jid, package)
    except Exception as e:  # noqa: BLE001
        escrow.set_failed(jid, str(e))


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumina Studio — hire the agent</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-stone-100 text-stone-800">
<div class="max-w-3xl mx-auto p-6">
  <h1 class="text-2xl font-semibold tracking-tight">Lumina Studio <span class="text-stone-400">— hire the agent</span></h1>
  <p class="text-sm text-stone-500 mt-1">Upload a product photo, fund escrow, and the autonomous studio delivers an on-brand content package.</p>

  <form id="orderForm" class="mt-6 bg-white rounded-xl shadow-sm p-5 space-y-4">
    <div>
      <label class="block text-sm font-medium">Product photo</label>
      <input name="product_photo" type="file" accept="image/*" required class="mt-1 block w-full text-sm">
    </div>
    <div>
      <label class="block text-sm font-medium">Brief / description</label>
      <textarea name="description" rows="3" required class="mt-1 w-full rounded-lg border-stone-300 border p-2 text-sm"
        placeholder="Brand, product, key features, target channel…"></textarea>
    </div>
    <div>
      <label class="block text-sm font-medium">Brand link (optional)</label>
      <input name="brand_link" type="url" class="mt-1 w-full rounded-lg border-stone-300 border p-2 text-sm" placeholder="https://…">
    </div>
    <button class="bg-stone-800 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-stone-700">
      Order &amp; fund escrow ($49)
    </button>
  </form>

  <div id="job" class="mt-6 hidden bg-white rounded-xl shadow-sm p-5">
    <div class="flex items-center justify-between">
      <div class="text-sm">Job <span id="jid" class="font-mono"></span></div>
      <div class="space-x-2">
        <span id="status" class="text-xs px-2 py-1 rounded-full bg-stone-200"></span>
        <span id="escrow" class="text-xs px-2 py-1 rounded-full bg-amber-100 text-amber-800"></span>
      </div>
    </div>
    <div id="now" class="mt-3 text-sm text-stone-700 flex items-center gap-2"></div>
    <ol id="events" class="mt-2 text-xs text-stone-400 space-y-1 max-h-40 overflow-auto"></ol>
    <div id="copy" class="mt-4 text-sm hidden"></div>
    <div id="scorecard" class="mt-4 hidden"></div>
    <div id="gallery" class="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3"></div>
    <button id="acceptBtn" class="mt-4 hidden bg-emerald-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-emerald-500">
      Accept &amp; release escrow
    </button>
  </div>
</div>

<script>
const $ = (s) => document.querySelector(s);
let jid = null, timer = null;

$('#orderForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/api/jobs', { method: 'POST', body: new FormData(e.target) });
  const j = await r.json();
  jid = j.job_id;
  window.__t0 = Date.now();
  $('#job').classList.remove('hidden');
  $('#jid').textContent = jid;
  poll();
  timer = setInterval(poll, 3000);
});

function badge(el, text, cls){ el.textContent = text; el.className = 'text-xs px-2 py-1 rounded-full ' + cls; }

async function poll(){
  if(!jid) return;
  const j = await (await fetch('/api/jobs/' + jid)).json();
  badge($('#status'), j.status, j.status==='Completed' ? 'bg-emerald-100 text-emerald-800' :
        j.status==='Delivered' ? 'bg-blue-100 text-blue-800' :
        j.status==='Failed' ? 'bg-red-100 text-red-700' : 'bg-stone-200 text-stone-700');
  badge($('#escrow'), 'escrow: ' + j.escrow, j.escrow==='Released' ? 'bg-emerald-100 text-emerald-800' :
        j.escrow==='Refunded' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800');
  $('#events').innerHTML = (j.events||[]).map(e => '<li>• ' + e.msg + '</li>').join('');
  const inProg = j.status==='InProgress';
  const lastEv = (j.events||[]).slice(-1)[0];
  const elapsed = Math.floor((Date.now()-(window.__t0||Date.now()))/1000);
  $('#now').innerHTML = (inProg ? '<span class="inline-block h-3 w-3 border-2 border-stone-400 border-t-transparent rounded-full animate-spin"></span>' : '') + '<span>'+(lastEv?lastEv.msg:'')+'</span>' + (inProg?' <span class="text-stone-400">· '+elapsed+'s</span>':'');
  const sc = (j.package && j.package.qa_scores) || [];
  if(sc.length){ $('#scorecard').classList.remove('hidden'); $('#scorecard').innerHTML = '<div class="font-medium text-sm">Quality scorecard</div>' + sc.map((s,i)=>'<div class="text-xs flex justify-between border-b border-stone-100 py-1"><span>'+(s.verdict==='pass'?'✓':'✗')+' Asset '+(i+1)+'</span><span class="font-mono text-stone-500">'+Math.round((s.score||0)*100)+'%</span></div>').join(''); }
  if(j.package){
    const a = j.package.assets || [];
    $('#gallery').innerHTML = a.map((x,i) => {
      const src = '/api/asset?uri=' + encodeURIComponent(x.uri);
      return x.type==='video'
        ? `<div class="space-y-1"><video controls playsinline preload="metadata" class="rounded-lg w-full bg-black" src="${src}"></video><a href="${src}" download="lumina-video-${i+1}.mp4" class="block text-xs text-stone-500 underline">⬇ Download MP4</a></div>`
        : `<div class="space-y-1"><img class="rounded-lg w-full" src="${src}"><a href="${src}" download="lumina-${i+1}.png" class="block text-xs text-stone-500 underline">⬇ Download</a></div>`;
    }).join('');
    const cp = j.package.copy;
    if(cp){ $('#copy').classList.remove('hidden');
      let h = '<div class="font-medium text-sm">Marketing copy</div>';
      if(typeof cp === 'string'){ h += '<pre class="whitespace-pre-wrap text-xs text-stone-600 mt-1">'+cp+'</pre>'; }
      else {
        if(cp.title) h += '<div class="mt-2 font-semibold text-stone-800">'+cp.title+'</div>';
        if(cp.short) h += '<div class="text-sm text-stone-600">'+cp.short+'</div>';
        if(cp.long) h += '<div class="mt-2 text-xs text-stone-600"><span class="text-stone-400">Long: </span>'+cp.long+'</div>';
        if(cp.emotional) h += '<div class="mt-1 text-xs text-stone-600 italic">'+cp.emotional+'</div>';
        if(Array.isArray(cp.bullets)) h += '<ul class="mt-1 text-xs text-stone-600 list-disc ml-4">'+cp.bullets.map(b=>'<li>'+b+'</li>').join('')+'</ul>';
        if(Array.isArray(cp.reviews)) h += '<div class="mt-2 text-xs text-stone-600"><span class="text-stone-400">Reviews:</span>'+cp.reviews.map(r=>'<div class="mt-1">'+('★'.repeat(r.rating||5))+' '+(r.text||'')+' <span class="text-stone-400">— '+(r.author||'')+'</span></div>').join('')+'</div>';
      }
      $('#copy').innerHTML = h;
    }
  }
  if(j.status==='Delivered'){ $('#acceptBtn').classList.remove('hidden'); }
  if(j.status==='Completed' || j.status==='Failed'){ clearInterval(timer); }
}

$('#acceptBtn').addEventListener('click', async () => {
  await fetch('/api/jobs/' + jid + '/accept', { method: 'POST' });
  $('#acceptBtn').classList.add('hidden');
  poll();
});
</script>
</body>
</html>"""
