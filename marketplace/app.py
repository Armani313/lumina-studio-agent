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
from lumina.pricing import BASE_PRICE, PER_CARD, PER_IMAGE, PER_VIDEO, default_spec, price_for_spec
from lumina.tools.delivery import mime_for_uri, upload_bytes

from . import consult as consult_engine
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


def _build_outputs(state: dict, live_url: str = "") -> dict:
    """Map the agent's delivered package into marketplace outputs (viewable proxy URLs + copy)."""
    assets = escrow.extract_assets(state)
    images = [_proxy_url(a["uri"]) for a in assets if a["type"] == "image"]
    videos = [_proxy_url(a["uri"]) for a in assets if a["type"] == "video"]
    cards = [_proxy_url(a["uri"]) for a in assets if a["type"] == "card"]
    copy = state.get("copy_full") or state.get("copy_doc") or {}
    cp = copy if isinstance(copy, dict) else {}
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    ru = "rus" in (brief.get("language") or "").lower() or "рус" in (brief.get("language") or "").lower()
    lbl_desc = "Описание товара" if ru else "Product description"
    lbl_feat = "Ключевые характеристики" if ru else "Key features"
    lbl_kw = "SEO-ключи" if ru else "SEO keywords"
    title = cp.get("title") or ""
    md = [f"## {title}" if title else "## Your on-brand content package"]
    if cp.get("short"):
        md.append(cp["short"])
    # Ready-to-paste marketplace listing copy (SEO description, features, keywords). These were
    # generated all along but previously omitted from the rendered markdown, so the buyer's storefront
    # only ever showed the title + tagline.
    if cp.get("long"):
        md.append(f"**{lbl_desc}**\n\n{cp['long']}")
    if isinstance(cp.get("bullets"), list) and cp["bullets"]:
        md.append(f"**{lbl_feat}**\n" + "\n".join(f"- {b}" for b in cp["bullets"]))
    if isinstance(cp.get("keywords"), list) and cp["keywords"]:
        md.append(f"**{lbl_kw}:** " + ", ".join(str(k) for k in cp["keywords"]))
    if images:
        md.append("**Images**\n" + "\n".join(f"![image]({u})" for u in images))
    if cards:
        md.append("**Product cards**\n" + "\n".join(f"![card]({u})" for u in cards))
    if videos:
        md.append("**Videos**\n" + "\n".join(f"- [video]({u})" for u in videos))
    if live_url:
        lbl = ("🧠 Как создавался ваш пакет — журнал мыслей и работы агента" if ru
               else "🧠 How it was made — the agent's live thinking log")
        md.append(f"---\n\n[{lbl}]({live_url})")
    out = {"markdown": "\n\n".join(md), "images": images, "cards": cards, "videos": videos, "copy": copy}
    if live_url:
        out["liveUrl"] = live_url
    return out


# --- Background agent jobs share ONE long-lived event loop ------------------
# A dedicated daemon thread owns a single event loop that is never closed; every order/consult job
# is submitted to it. ADK caches its async genai client on the process-wide singleton agent model
# (Gemini.api_client is a @cached_property bound to the loop it was first used on). Running each job
# via asyncio.run(...) created a fresh loop per job and CLOSED it on completion, leaving that cached
# client bound to a dead loop — so the second order onward failed with "Event loop is closed". A
# persistent loop keeps the client valid for the life of the process; blocking tool calls
# (image/video gen) still run off the web event loop, so the UI/polling never freezes.
_job_loop: asyncio.AbstractEventLoop | None = None
_job_loop_lock = threading.Lock()


def _get_job_loop() -> asyncio.AbstractEventLoop:
    global _job_loop
    with _job_loop_lock:
        if _job_loop is None:
            loop = asyncio.new_event_loop()

            def _serve() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            threading.Thread(target=_serve, name="lumina-jobs", daemon=True).start()
            _job_loop = loop
    return _job_loop


def _dispatch(coro) -> None:
    """Run a background agent job on the shared loop (keeps blocking work off the web loop).

    _run_job / _run_marketplace_job handle their own exceptions; the done-callback only fires for
    unexpected errors outside those guards, which we log instead of swallowing silently.
    """
    fut = asyncio.run_coroutine_threadsafe(coro, _get_job_loop())

    def _log_unhandled(f) -> None:
        try:
            exc = f.exception()
        except Exception:  # noqa: BLE001 — cancelled future, etc.
            return
        if exc is not None:
            import traceback
            print("[job] unhandled exception:\n"
                  + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), flush=True)

    fut.add_done_callback(_log_unhandled)


async def _run_marketplace_job(inputs: dict, brief: str, image_url: str, brand_link: str,
                               cb_url: str, cb_token: str, delivery_id: str = "",
                               jid: str = "") -> None:
    """Background: run the full agent for an external-marketplace task, then POST the result.

    Always finishes with exactly one callback: {status: "completed", outputs} once real assets
    exist, otherwise {status: "failed", error}. Never reports a hollow completion.

    The run is also narrated into escrow job `jid` (same feed as the local UI), which powers the
    public buyer-facing live page /live/{jid} — escrow writes are best-effort and must never
    break the callback contract.
    """
    tag = f"[mkt-job {delivery_id or '-'}]"
    live_url = f"{SERVICE_URL}/live/{jid}" if jid else ""

    def _cb(body: dict) -> None:
        if delivery_id:
            body.setdefault("deliveryId", delivery_id)
        if live_url:
            body.setdefault("liveUrl", live_url)
        extra = f" error={body['error']!r}" if body.get("status") == "failed" else ""
        print(f"{tag} -> callback status={body.get('status')}{extra}", flush=True)
        _post_callback(cb_url, cb_token, body)

    def _note(msg: str, kind: str = "system") -> None:
        if jid:
            try:
                escrow.add_event(jid, msg, kind=kind)
            except Exception:  # noqa: BLE001 — live feed is cosmetic; the job must go on
                pass

    def _fail(err: str) -> None:
        if jid:
            try:
                escrow.update_job(jid, status="Failed")
                escrow.add_event(jid, f"Failed: {err[:160]}")
            except Exception:  # noqa: BLE001
                pass
        _cb({"status": "failed", "error": err})

    print(f"{tag} start image_url={(image_url or '')[:90]!r} brief={(brief or '')[:80]!r}"
          f" live={live_url or '-'}", flush=True)
    try:
        if not image_url:
            _fail("no product image in task inputs or attachments")
            return
        product_uri = _fetch_image_to_gcs(image_url)
        if not product_uri:
            _fail("could not fetch product image")
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
        seen: set[str] = set()
        last: tuple[str, str] | None = None
        # Narrate the run into the live feed exactly like the local UI does (_run_job).
        async for ev in runner.run_async(user_id=uid, session_id=session.id, new_message=msg):
            for kind, line in _narrate_event(ev, seen):
                if (kind, line) == last:
                    continue  # collapse consecutive duplicates (model re-emits prose across chunks)
                last = (kind, line)
                _note(line, kind)
        st = dict((await runner.session_service.get_session(
            app_name="lumina_ext", user_id=uid, session_id=session.id)).state)
        outputs = _build_outputs(st, live_url=live_url)
        print(f"{tag} agent done; assets images={len(outputs.get('images') or [])} "
              f"cards={len(outputs.get('cards') or [])} videos={len(outputs.get('videos') or [])}", flush=True)
        # Guard: never report a hollow "completed" with no media (the original failure class).
        if not (outputs.get("images") or outputs.get("videos") or outputs.get("cards")):
            _fail("generation produced no deliverable assets")
            return
        if jid:
            try:
                package = {
                    "assets": escrow.extract_assets(st),
                    "copy": st.get("copy_full") or st.get("copy_doc"),
                    "qa_report": st.get("qa_report"),
                    "qa_scores": st.get("qa_scores") or [],
                }
                # Plain "Delivered" (not set_delivered): for external orders acceptance/escrow
                # release happens on the marketplace, so its wording doesn't apply here.
                escrow.update_job(jid, status="Delivered", package=package)
                escrow.add_event(jid, "Package delivered to the marketplace")
            except Exception:  # noqa: BLE001
                pass
        _cb({"status": "completed", "outputs": outputs})
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"{tag} EXCEPTION {e!r}\n{traceback.format_exc()}", flush=True)
        _fail(str(e)[:300])


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


def _consult_reply(consult: dict) -> dict:
    """One consultation turn for the EXTERNAL marketplace, via the content-aware engine.

    The engine studies the buyer's attached photo and any shared link live, then replies (+ an
    optional proposal). Live study is bounded so the synchronous reply stays within the platform's
    ~20s budget. Response shape is unchanged: {text, proposal?, suggestedPriceCents?}.
    """
    mode = consult.get("mode") or "interview"
    message = consult.get("message") or ""
    history = consult.get("history") or []
    min_cents = int((consult.get("pricing") or {}).get("minPriceCents") or 0)
    photo_url = _image_from_attachments(consult.get("attachments"))

    result = consult_engine.run_consult(message, history, photo_url=photo_url, mode=mode, deadline_s=11.0)

    out: dict = {"text": result["text"]}
    spec = result.get("spec")
    if result.get("propose") and spec:
        # Carry the attached photo into the proposal so the accepted task arrives with a usable image.
        if photo_url and not spec.get("product_image_url"):
            spec["product_image_url"] = photo_url
        cents = _quote_cents(spec, min_cents)
        out["proposal"] = {"spec": spec, "quoteCents": cents, "etaMinutes": result.get("etaMinutes") or 30}
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
            -> respond {status: "accepted", liveUrl} now; the finished package is POSTed to
               callback.url (Bearer callback.bearerToken) within the offer's ETA. liveUrl is
               a public read-only page streaming the agent's work (see docs/LIVE_VIEW_INTEGRATION.md).

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
    # Also register it as an escrow job BEFORE accepting, so the buyer-facing live page exists
    # the moment the marketplace renders our response: /live/{jid} (or the template-able
    # /live/d/{deliveryId}) streams the agent's thinking feed while the order is produced.
    spec = _spec_from_inputs(inputs)
    jid = escrow.create_job(
        brief or "Marketplace order",
        image_url,
        brand_link,
        price=(_quote_cents(spec) // 100) if spec else 0,
        extra={"delivery_id": str(delivery_id or ""), "source": "external"},
    )
    if delivery_id:
        escrow.map_delivery(str(delivery_id), jid)
    live_url = f"{SERVICE_URL}/live/{jid}"
    _dispatch(_run_marketplace_job(inputs, brief, image_url, brand_link, cb_url, cb_token,
                                   delivery_id, jid=jid))
    return {"status": "accepted", "liveUrl": live_url,
            "note": f"Watch the agent think & work live: {live_url}"}


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
    jid = escrow.create_job(brief, uri, brand_link, price=price_for_spec(default_spec()))
    # Run the agent on the shared background loop so its blocking tool calls (image/video gen)
    # never freeze the web event loop serving the UI/polling.
    _dispatch(_run_job(jid, brief, uri))
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


# --- Public live view ("watch the agent work") --------------------------------
# Read-only, no auth: the job id is an unguessable random token, so the link itself is the
# capability. Shared with the buyer on an external marketplace via the `liveUrl` field /
# the delivery markdown, or constructed by the platform as /live/d/{deliveryId}.


@app.get("/live/{jid}", response_class=HTMLResponse)
def live_view(jid: str) -> str:
    """Buyer-facing live page: streams the agent's thinking feed, QA verdicts and previews."""
    return LIVE_HTML


@app.get("/live/d/{delivery_id}", response_class=HTMLResponse)
def live_view_by_delivery(delivery_id: str) -> str:
    """Same live page, addressed by the marketplace's own deliveryId (template-able URL).

    The page resolves the deliveryId to a job client-side and waits politely if our webhook
    hasn't created the job yet — so the marketplace may render this link immediately.
    """
    return LIVE_HTML


@app.get("/api/jobs/by-delivery/{delivery_id}")
def job_by_delivery(delivery_id: str):
    jid = escrow.job_id_for_delivery(delivery_id)
    if not jid:
        raise HTTPException(404, "no job for this delivery yet")
    return {"job_id": jid}


@app.post("/api/consult")
async def consult_turn(
    message: str = Form(""),
    history: str = Form("[]"),
    photo: UploadFile = File(None),
    photo_uri: str = Form(""),
):
    """Studio-UI chat turn. Studies any shared photo/link LIVE, returns the consultant's reply and,
    when ready, a one-click proposal. The photo is uploaded once; the client resends photo_uri after.
    """
    try:
        hist = json.loads(history) if history else []
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    if photo is not None:
        data = await photo.read()
        if data:
            photo_uri = upload_bytes(
                data, f"inputs/{uuid.uuid4().hex}.png", mime_for_uri(photo.filename or ".png")
            )
    msg = message or ("(shared a product photo)" if photo is not None else message)
    # Our own UI — no 20s cap, so allow a fuller live study of photo + links.
    result = await asyncio.to_thread(
        consult_engine.run_consult, msg, hist, (photo_uri or None), "interview", 40.0
    )
    out: dict = {
        "text": result["text"],
        "photo_uri": photo_uri,
        "studied": (result.get("studied") or {}).get("chip", ""),
    }
    spec = result.get("spec")
    if result.get("propose") and spec:
        if photo_uri and not spec.get("product_image_url"):
            spec["product_image_url"] = photo_uri
        out["proposal"] = {
            "spec": spec,
            "price": _quote_cents(spec, 0) // 100,
            "etaMinutes": result.get("etaMinutes") or 30,
        }
    return out


@app.post("/api/consult/start")
async def consult_start(request: Request):
    """Lock the agreed plan into a funded job and start the agent (reuses the order pipeline)."""
    body = await request.json()
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else {}
    photo_uri = (body.get("photo_uri") or "").strip()
    if not photo_uri:
        raise HTTPException(400, "no product photo")
    brief = (spec.get("brief") or "").strip() or "On-brand content package for the uploaded product."
    brand_link = (spec.get("brand_link") or "").strip()
    if brand_link:
        brief = f"{brief}\nBrand link: {brand_link}"
    norm = _spec_from_inputs(spec)
    price = _quote_cents(spec, 0) // 100
    jid = escrow.create_job(brief, photo_uri, brand_link, price=price)
    _dispatch(_run_job(jid, brief, photo_uri, norm))
    return {"job_id": jid}


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


# --- "Agent thinking" narration ---------------------------------------------
# Turn raw ADK events (agent authors, tool calls, tool responses, reasoning text)
# into a human-readable live log so a watcher can follow HOW the studio reasons,
# not just opaque stage/tool identifiers.
_STAGE_LABELS = {
    "product_vision": "👁️ Studying your product photo",
    "intake": "📋 Reading your brief",
    "grounding": "🔎 Researching your brand",
    "brand_research": "🔎 Reading your site & searching the web",
    "brand_rag": "📚 Recalling brand guidelines",
    "shot_planner": "🎬 Art-directing the shoot",
    "production": "🎨 Producing your content",
    "image_production": "🖼️ Shooting product images",
    "copywriter": "✍️ Writing marketing copy",
    "video_production": "🎥 Filming product videos",
    "card_production": "🪧 Designing product cards",
    "qa_loop": "🔍 Quality control",
    "brand_qa": "🔍 Checking brand fit & fidelity",
    "delivery": "📦 Packaging your delivery",
}


def _stage_label(author: str) -> str:
    return _STAGE_LABELS.get(author) or ("🤖 " + author.replace("_", " ").title())


def _short(v: object, n: int = 80) -> str:
    s = " ".join(str(v).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _narrate_call(name: str, args: dict) -> str | None:
    """Plain-language line for a tool the agent just decided to use."""
    a = args or {}
    if name == "describe_product":
        return "inspecting the photo to identify the exact product"
    if name == "generate_lifestyle_image":
        shot = a.get("shot_type") or "lifestyle"
        ar = a.get("aspect_ratio") or ""
        return f"rendering a {shot} shot" + (f" · {ar}" if ar else "")
    if name == "generate_copy":
        ch = a.get("channel") or "social"
        lang = a.get("language") or ""
        return f"writing {ch} copy" + (f" in {lang}" if lang else "")
    if name == "generate_product_video":
        ar = a.get("aspect_ratio") or ""
        dur = a.get("duration_seconds")
        bits = " · ".join(x for x in (ar, f"{dur}s" if dur else "") if x)
        return "filming a product clip" + (f" ({bits})" if bits else "")
    if name == "make_product_card":
        h = a.get("headline")
        return "designing a product card" + (f": “{_short(h, 42)}”" if h else "")
    if name == "review_image_brand_fit":
        return "reviewing an image for brand fit & product fidelity"
    if name == "replace_failed_image":
        return "regenerating an image that needs work"
    if name == "exit_loop":
        return "approving — every image passed QA"
    if name == "write_manifest":
        return "writing the delivery manifest"
    if name in ("google_search", "google_search_retrieval"):
        return "searching the web"
    if name == "url_context":
        return "reading the brand’s website"
    return f"using {name.replace('_', ' ')}"


def _narrate_response(name: str, resp: object) -> tuple[str, str] | None:
    """(kind, line) for a tool RESULT — only the few high-signal ones worth surfacing."""
    r = resp if isinstance(resp, dict) else {}
    inner = r.get("result")
    if isinstance(inner, dict) and not (r.keys() & {"verdict", "old_uri", "status", "error"}):
        r = inner
    if name == "review_image_brand_fit":
        verdict = str(r.get("verdict") or "").lower()
        score = r.get("score")
        pct = f"{round(float(score) * 100)}%" if isinstance(score, (int, float)) else ""
        if verdict == "pass":
            return ("verdict", "✓ on-brand" + (f" · {pct}" if pct else ""))
        issues = r.get("issues") or []
        why = _short(issues[0], 70) if issues else _short(r.get("fix_suggestion") or "", 70)
        return ("verdict-fail", "✗ needs work" + (f" · {pct}" if pct else "") + (f" — {why}" if why else ""))
    if name == "replace_failed_image":
        if r.get("error"):
            return ("verdict-fail", "regeneration failed — retrying")
        return ("verdict", "✓ swapped in a corrected image")
    if name == "exit_loop":
        return ("verdict", "✓ package approved")
    return None


def _narrate_event(ev, seen: set[str]) -> list[tuple[str, str]]:
    """One ADK event -> zero or more (kind, message) lines for the live thinking log."""
    out: list[tuple[str, str]] = []
    author = getattr(ev, "author", None) or "agent"
    if author not in seen:
        seen.add(author)
        out.append(("stage", _stage_label(author)))
    content = getattr(ev, "content", None)
    for p in (getattr(content, "parts", None) or []):
        fc = getattr(p, "function_call", None)
        if fc and getattr(fc, "name", None):
            line = _narrate_call(fc.name, dict(getattr(fc, "args", None) or {}))
            if line:
                out.append(("act", line))
            continue
        fr = getattr(p, "function_response", None)
        if fr and getattr(fr, "name", None):
            r = _narrate_response(fr.name, getattr(fr, "response", None))
            if r:
                out.append(r)
            continue
        text = getattr(p, "text", None)
        if text and not getattr(ev, "partial", False):
            t = text.strip()
            if getattr(p, "thought", False):  # Gemini's own reasoning summary (BuiltInPlanner)
                if t:
                    out.append(("reason", _short(t, 300)))
            elif t and t[0] not in "{[":  # skip raw JSON echoes (structured agent outputs)
                out.append(("think", _short(t, 160)))
    return out


async def _run_job(jid: str, brief: str, product_uri: str, spec: dict | None = None) -> None:
    """Background: run the full agent graph for this order and record the result + escrow state."""
    try:
        runner = InMemoryRunner(agent=root_agent, app_name="lumina_mkt")
        state = {"product_image_uri": product_uri, "brief_text": brief}
        if spec:
            state["spec"] = spec  # honor the plan agreed in the consultation chat
        session = await runner.session_service.create_session(
            app_name="lumina_mkt", user_id=jid, state=state,
        )
        msg = types.Content(role="user", parts=[types.Part(text=brief)])
        seen: set[str] = set()
        last: tuple[str, str] | None = None
        # Narrate the agent's work as a human-readable "thinking" log: friendly stage headers,
        # the model's own reasoning summaries, plain-language tool actions and QA verdicts.
        async for ev in runner.run_async(user_id=jid, session_id=session.id, new_message=msg):
            for kind, line in _narrate_event(ev, seen):
                if (kind, line) == last:
                    continue  # collapse consecutive duplicates (model re-emits prose across chunks)
                last = (kind, line)
                escrow.add_event(jid, line, kind=kind)
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
  <p class="text-sm text-stone-500 mt-1">Chat with the studio: share your product photo and a link to your site or design — the agent studies them, agrees a plan, then delivers an on-brand content package.</p>

  <div id="chat" class="mt-6 bg-white rounded-xl shadow-sm p-5">
    <div id="messages" class="space-y-3 max-h-[28rem] overflow-auto pr-1"></div>
    <div id="studied" class="mt-2 text-[11px] text-stone-400"></div>
    <div id="proposal" class="mt-3 hidden"></div>
    <div id="attachRow" class="mt-3 hidden text-xs text-stone-500"></div>
    <form id="chatForm" class="mt-3 flex items-end gap-2">
      <label class="shrink-0 cursor-pointer rounded-lg border border-stone-300 px-3 py-2 text-sm hover:bg-stone-50" title="Attach product photo">
        📎<input id="photo" type="file" accept="image/*" class="hidden">
      </label>
      <textarea id="msg" rows="1" class="flex-1 resize-none rounded-lg border-stone-300 border p-2 text-sm" placeholder="Describe your product and where you'll publish it…"></textarea>
      <button id="sendBtn" class="shrink-0 bg-stone-800 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-stone-700">Send</button>
    </form>
  </div>

  <div id="job" class="mt-6 hidden bg-white rounded-xl shadow-sm p-5">
    <div class="flex items-center justify-between">
      <div class="text-sm">Job <span id="jid" class="font-mono"></span></div>
      <div class="space-x-2">
        <span id="status" class="text-xs px-2 py-1 rounded-full bg-stone-200"></span>
        <span id="escrow" class="text-xs px-2 py-1 rounded-full bg-amber-100 text-amber-800"></span>
      </div>
    </div>
    <div id="now" class="mt-3 text-sm text-stone-700 flex items-center gap-2 flex-wrap"></div>
    <div id="thinking" class="mt-3 hidden">
      <div class="text-[11px] uppercase tracking-wide text-stone-400 font-medium">Agent thinking</div>
      <ol id="events" class="mt-1 text-xs leading-relaxed space-y-0.5 max-h-64 overflow-auto pr-1"></ol>
    </div>
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

function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function evClass(k){ return ({
  stage: 'text-stone-800 font-medium mt-2',
  reason: 'text-violet-600 pl-4',
  act: 'text-stone-500 pl-4',
  verdict: 'text-emerald-600 pl-4',
  'verdict-fail': 'text-amber-600 pl-4',
  think: 'text-stone-500 italic pl-4',
})[k] || 'text-stone-400'; }
function evMark(k){
  if(k==='reason') return '<span class="text-violet-300">💭 </span>';
  if(k==='act') return '<span class="text-stone-300">↳ </span>';
  if(k==='stage'||k==='verdict'||k==='verdict-fail'||k==='think') return '';
  return '<span class="text-stone-300">• </span>';
}

// ---- Consultation chat ----
const history = [];
let photoUri = '';
let pendingPhoto = null;

function bubble(sender, text){
  const mine = sender === 'user';
  const w = document.createElement('div');
  w.className = 'flex ' + (mine ? 'justify-end' : 'justify-start');
  w.innerHTML = '<div class="max-w-[80%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap '+(mine?'bg-stone-800 text-white':'bg-stone-100 text-stone-800')+'">'+escapeHtml(text)+'</div>';
  $('#messages').appendChild(w);
  $('#messages').scrollTop = $('#messages').scrollHeight;
}
function typing(on, label){
  let t = document.getElementById('typing');
  if(on){
    if(!t){ t=document.createElement('div'); t.id='typing'; t.className='flex justify-start';
      $('#messages').appendChild(t); }
    // With a label (e.g. "studying your photo…") show a live status so the chat never sits empty
    // while we analyse the photo/link; without one, the plain typing dots.
    t.innerHTML='<div class="rounded-2xl px-3 py-2 text-sm bg-stone-100 '+(label?'text-stone-500':'text-stone-400')+'">'+
      (label?'<span class="animate-pulse">'+escapeHtml(label)+'</span>':'…')+'</div>';
    $('#messages').scrollTop=$('#messages').scrollHeight;
  } else if(t){ t.remove(); }
}

$('#photo').addEventListener('change', () => {
  pendingPhoto = $('#photo').files[0] || null;
  $('#attachRow').classList.toggle('hidden', !pendingPhoto);
  $('#attachRow').textContent = pendingPhoto ? ('📎 '+pendingPhoto.name+' — sent with your next message') : '';
});
$('#msg').addEventListener('keydown', (e) => {
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); $('#chatForm').requestSubmit(); }
});

$('#chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('#msg').value.trim();
  if(!text && !pendingPhoto) return;
  const shown = text || '📎 (product photo)';
  bubble('user', shown);
  $('#msg').value = '';
  $('#proposal').classList.add('hidden');
  // Acknowledge instantly so the chat never goes empty while we study the photo/link (study can take
  // several seconds). Content-aware + language-aware (Cyrillic in this or recent turns -> Russian).
  const ru = /[Ѐ-ӿ]/.test(text) || /[Ѐ-ӿ]/.test(history.map(h=>h.text||'').join(' '));
  const hasLink = new RegExp('https?://', 'i').test(text);
  let studyMsg = '';
  if(pendingPhoto && hasLink) studyMsg = ru ? '📸🔎 Смотрю ваше фото и открываю ссылку…' : '📸🔎 Looking at your photo and opening your link…';
  else if(pendingPhoto)       studyMsg = ru ? '📸 Секунду, рассматриваю ваше фото…'       : '📸 One sec — studying your photo…';
  else if(hasLink)            studyMsg = ru ? '🔎 Открываю ссылку, изучаю стиль…'          : '🔎 Opening your link, studying the style…';
  $('#sendBtn').disabled = true; typing(true, studyMsg);

  const fd = new FormData();
  fd.append('message', text);
  fd.append('history', JSON.stringify(history));
  if(pendingPhoto) fd.append('photo', pendingPhoto);
  if(photoUri) fd.append('photo_uri', photoUri);
  history.push({sender:'user', text: shown});

  let j;
  try { j = await (await fetch('/api/consult', {method:'POST', body: fd})).json(); }
  catch(err){ typing(false); $('#sendBtn').disabled=false; bubble('agent','(connection error — please try again)'); return; }
  typing(false); $('#sendBtn').disabled=false;
  pendingPhoto = null; $('#photo').value=''; $('#attachRow').classList.add('hidden');
  if(j.photo_uri) photoUri = j.photo_uri;
  bubble('agent', j.text || '…');
  history.push({sender:'agent', text: j.text || ''});
  $('#studied').textContent = j.studied ? ('🔎 '+j.studied) : '';
  if(j.proposal) renderProposal(j.proposal);
});

function renderProposal(p){
  const s = p.spec || {};
  const vids = Array.isArray(s.video_kinds) ? s.video_kinds.length : (s.videos||0);
  const parts = [];
  if(s.images) parts.push(s.images+' images');
  if(vids) parts.push(vids+' video'+(vids>1?'s':''));
  if(s.cards) parts.push(s.cards+' card'+(s.cards>1?'s':''));
  const plat = (s.platforms&&s.platforms.length) ? ' — '+escapeHtml(s.platforms.join(', ')) : '';
  $('#proposal').classList.remove('hidden');
  $('#proposal').innerHTML =
    '<div class="rounded-xl border border-stone-200 p-3">'+
      '<div class="text-sm font-medium text-stone-800">Proposed plan</div>'+
      '<div class="text-sm text-stone-600 mt-1">'+escapeHtml(parts.join(' · ')||'Custom package')+plat+'</div>'+
      '<div class="mt-2 flex items-center justify-between">'+
        '<div class="text-lg font-semibold">$'+(p.price||0)+'</div>'+
        '<button id="startBtn" class="bg-emerald-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-emerald-500">Start &amp; fund escrow</button>'+
      '</div></div>';
  $('#startBtn').addEventListener('click', () => startOrder(s));
}

async function startOrder(spec){
  if(!photoUri){ bubble('agent','Please attach your product photo first, then I can start.'); return; }
  const b = $('#startBtn'); b.disabled = true; b.textContent = 'Starting…';
  let j;
  try { j = await (await fetch('/api/consult/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({spec, photo_uri: photoUri})})).json(); }
  catch(err){ b.disabled=false; b.textContent='Start & fund escrow'; return; }
  if(!j.job_id){ b.disabled=false; b.textContent='Start & fund escrow'; return; }
  jid = j.job_id; window.__t0 = Date.now();
  $('#chat').classList.add('hidden');
  $('#job').classList.remove('hidden');
  $('#jid').textContent = jid;
  poll(); timer = setInterval(poll, 3000);
}

bubble('agent', "Hi! I'm your Lumina content consultant. Tell me about your product and where you'll publish it — attach your product photo and paste a link to your site or design, and I'll study them and put together a plan.");
history.push({sender:'agent', text:"Hi! I'm your Lumina content consultant."});

function badge(el, text, cls){ el.textContent = text; el.className = 'text-xs px-2 py-1 rounded-full ' + cls; }

async function poll(){
  if(!jid) return;
  const j = await (await fetch('/api/jobs/' + jid)).json();
  badge($('#status'), j.status, j.status==='Completed' ? 'bg-emerald-100 text-emerald-800' :
        j.status==='Delivered' ? 'bg-blue-100 text-blue-800' :
        j.status==='Failed' ? 'bg-red-100 text-red-700' : 'bg-stone-200 text-stone-700');
  badge($('#escrow'), 'escrow: ' + j.escrow, j.escrow==='Released' ? 'bg-emerald-100 text-emerald-800' :
        j.escrow==='Refunded' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800');
  const evs = j.events || [];
  $('#thinking').classList.toggle('hidden', evs.length === 0);
  $('#events').innerHTML = evs.map(e => '<li class="'+evClass(e.kind||'system')+'">'+evMark(e.kind||'system')+escapeHtml(e.msg)+'</li>').join('');
  const evBox = $('#events'); evBox.scrollTop = evBox.scrollHeight;  // keep newest in view
  const inProg = j.status==='InProgress';
  const lastEv = evs[evs.length-1];
  let lastStage = null; for(let i=evs.length-1;i>=0;i--){ if((evs[i].kind||'')==='stage'){ lastStage = evs[i]; break; } }
  const elapsed = Math.floor((Date.now()-(window.__t0||Date.now()))/1000);
  const spin = '<span class="inline-block h-3 w-3 border-2 border-stone-400 border-t-transparent rounded-full animate-spin"></span>';
  const head = lastStage ? lastStage.msg : (lastEv ? lastEv.msg : '');
  const subRaw = (lastEv && lastEv !== lastStage) ? lastEv.msg : '';
  const sub = subRaw.length > 100 ? subRaw.slice(0, 99) + '…' : subRaw;
  $('#now').innerHTML = (inProg ? spin : '') +
    '<span class="font-medium text-stone-800">'+escapeHtml(head)+'</span>' +
    (sub ? ' <span class="text-stone-400">— '+escapeHtml(sub)+'</span>' : '') +
    (inProg ? ' <span class="text-stone-400">· '+elapsed+'s</span>' : '');
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


# Public read-only "watch the agent work" page served at /live/{jid} and /live/d/{deliveryId}.
# Same thinking-feed rendering as INDEX_HTML's job panel, minus anything actionable (no accept
# button, no escrow controls) — safe to share with a buyer or embed in a marketplace iframe.
LIVE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumina Studio — agent at work</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-stone-100 text-stone-800">
<div class="max-w-3xl mx-auto p-4 sm:p-6">
  <div class="flex items-center justify-between gap-3">
    <h1 class="text-lg font-semibold tracking-tight">Lumina Studio <span class="text-stone-400">— agent at work</span></h1>
    <span id="status" class="text-xs px-2 py-1 rounded-full bg-stone-200"></span>
  </div>

  <div id="wait" class="mt-6 bg-white rounded-xl shadow-sm p-5 text-sm text-stone-500 flex items-center gap-2">
    <span class="inline-block h-3 w-3 border-2 border-stone-400 border-t-transparent rounded-full animate-spin"></span>
    <span id="waitMsg">Waiting for the agent to pick up this order…</span>
  </div>

  <div id="panel" class="mt-6 hidden bg-white rounded-xl shadow-sm p-5">
    <div id="now" class="text-sm text-stone-700 flex items-center gap-2 flex-wrap"></div>
    <div id="thinking" class="mt-3 hidden">
      <div class="text-[11px] uppercase tracking-wide text-stone-400 font-medium">Agent thinking — live</div>
      <ol id="events" class="mt-1 text-xs leading-relaxed space-y-0.5 max-h-80 overflow-auto pr-1"></ol>
    </div>
    <div id="copy" class="mt-4 text-sm hidden"></div>
    <div id="scorecard" class="mt-4 hidden"></div>
    <div id="gallery" class="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3"></div>
  </div>

  <p class="mt-3 text-[11px] text-stone-400">Read-only view. Your deliverables arrive in your marketplace order — this page shows how they're being made.</p>
</div>

<script>
const $ = (s) => document.querySelector(s);
const m = location.pathname.match(/\\/live\\/(?:(d)\\/)?([^\\/?#]+)/);
let jid = m && !m[1] ? m[2] : null;
const deliveryId = m && m[1] ? m[2] : null;
let timer = null;

function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function evClass(k){ return ({
  stage: 'text-stone-800 font-medium mt-2',
  reason: 'text-violet-600 pl-4',
  act: 'text-stone-500 pl-4',
  verdict: 'text-emerald-600 pl-4',
  'verdict-fail': 'text-amber-600 pl-4',
  think: 'text-stone-500 italic pl-4',
})[k] || 'text-stone-400'; }
function evMark(k){
  if(k==='reason') return '<span class="text-violet-300">💭 </span>';
  if(k==='act') return '<span class="text-stone-300">↳ </span>';
  if(k==='stage'||k==='verdict'||k==='verdict-fail'||k==='think') return '';
  return '<span class="text-stone-300">• </span>';
}
function badge(text, cls){ const el=$('#status'); el.textContent=text; el.className='text-xs px-2 py-1 rounded-full '+cls; }

async function resolveDelivery(){
  try{
    const r = await fetch('/api/jobs/by-delivery/' + encodeURIComponent(deliveryId));
    if(r.ok){ jid = (await r.json()).job_id; start(); return; }
  }catch(e){}
  setTimeout(resolveDelivery, 3000);
}

function start(){
  $('#wait').classList.add('hidden');
  $('#panel').classList.remove('hidden');
  poll(); timer = setInterval(poll, 2500);
}

async function poll(){
  let j;
  try{
    const r = await fetch('/api/jobs/' + jid);
    if(!r.ok) return;
    j = await r.json();
  }catch(e){ return; }

  badge(j.status, j.status==='Completed' || j.status==='Delivered' ? 'bg-emerald-100 text-emerald-800' :
        j.status==='Failed' ? 'bg-red-100 text-red-700' : 'bg-stone-200 text-stone-700');

  const evs = j.events || [];
  $('#thinking').classList.toggle('hidden', evs.length === 0);
  $('#events').innerHTML = evs.map(e => '<li class="'+evClass(e.kind||'system')+'">'+evMark(e.kind||'system')+escapeHtml(e.msg)+'</li>').join('');
  const evBox = $('#events'); evBox.scrollTop = evBox.scrollHeight;

  const inProg = j.status==='InProgress';
  const lastEv = evs[evs.length-1];
  let lastStage = null; for(let i=evs.length-1;i>=0;i--){ if((evs[i].kind||'')==='stage'){ lastStage = evs[i]; break; } }
  const t0 = j.created_at ? Date.parse(j.created_at) : Date.now();
  const elapsed = Math.max(0, Math.floor((Date.now()-t0)/1000));
  const mm = Math.floor(elapsed/60), ss = String(elapsed%60).padStart(2,'0');
  const spin = '<span class="inline-block h-3 w-3 border-2 border-stone-400 border-t-transparent rounded-full animate-spin"></span>';
  const head = lastStage ? lastStage.msg : (lastEv ? lastEv.msg : 'Starting…');
  const subRaw = (lastEv && lastEv !== lastStage) ? lastEv.msg : '';
  const sub = subRaw.length > 100 ? subRaw.slice(0, 99) + '…' : subRaw;
  $('#now').innerHTML = (inProg ? spin : '') +
    '<span class="font-medium text-stone-800">'+escapeHtml(head)+'</span>' +
    (sub ? ' <span class="text-stone-400">— '+escapeHtml(sub)+'</span>' : '') +
    (inProg ? ' <span class="text-stone-400">· '+mm+':'+ss+'</span>' : '');

  const sc = (j.package && j.package.qa_scores) || [];
  if(sc.length){ $('#scorecard').classList.remove('hidden'); $('#scorecard').innerHTML = '<div class="font-medium text-sm">Quality scorecard</div>' + sc.map((s,i)=>'<div class="text-xs flex justify-between border-b border-stone-100 py-1"><span>'+(s.verdict==='pass'?'✓':'✗')+' Asset '+(i+1)+'</span><span class="font-mono text-stone-500">'+Math.round((s.score||0)*100)+'%</span></div>').join(''); }

  if(j.package){
    const a = j.package.assets || [];
    $('#gallery').innerHTML = a.map((x,i) => {
      const src = '/api/asset?uri=' + encodeURIComponent(x.uri);
      return x.type==='video'
        ? `<video controls playsinline preload="metadata" class="rounded-lg w-full bg-black" src="${src}"></video>`
        : `<img class="rounded-lg w-full" src="${src}">`;
    }).join('');
    const cp = j.package.copy;
    if(cp && typeof cp === 'object'){
      $('#copy').classList.remove('hidden');
      let h = '<div class="font-medium text-sm">Marketing copy</div>';
      if(cp.title) h += '<div class="mt-2 font-semibold text-stone-800">'+escapeHtml(cp.title)+'</div>';
      if(cp.short) h += '<div class="text-sm text-stone-600">'+escapeHtml(cp.short)+'</div>';
      $('#copy').innerHTML = h;
    }
  }

  if(j.status==='Delivered' || j.status==='Completed' || j.status==='Failed'){ clearInterval(timer); }
}

if(jid){ start(); }
else if(deliveryId){ resolveDelivery(); }
else { $('#waitMsg').textContent = 'Invalid link.'; }
</script>
</body>
</html>"""
