"""Lumina Studio Marketplace — FastAPI: order UI + escrow API (Firestore) + in-process agent run.

The same agent is also published as an A2A service (see marketplace/a2a_server.py) so other
agents can discover and hire it via its AgentCard.

Run:  .venv/bin/uvicorn marketplace.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import threading
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from google.cloud import storage
from google.genai import types
from google.adk.runners import InMemoryRunner

from lumina.agent import root_agent
from lumina.config import settings
from lumina.tools.delivery import mime_for_uri, upload_bytes

from . import escrow

app = FastAPI(title="Lumina Studio Marketplace")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return INDEX_HTML


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


@app.get("/api/asset")
def asset(uri: str):
    """Proxy a gs:// asset through ADC so the browser needn't have GCS access."""
    if not uri.startswith("gs://"):
        raise HTTPException(400, "bad uri")
    bucket_name, _, blob_path = uri[5:].partition("/")
    blob = storage.Client(project=settings.project).bucket(bucket_name).blob(blob_path)
    if not blob.exists():
        raise HTTPException(404, "asset not found")
    data = blob.download_as_bytes()
    ctype = "video/mp4" if blob_path.lower().endswith(".mp4") else "image/png"
    return Response(content=data, media_type=ctype)


async def _run_job(jid: str, brief: str, product_uri: str) -> None:
    """Background: run the full agent graph for this order and record the result + escrow state."""
    try:
        runner = InMemoryRunner(agent=root_agent, app_name="lumina_mkt")
        session = await runner.session_service.create_session(
            app_name="lumina_mkt", user_id=jid, state={"product_image_uri": product_uri}
        )
        msg = types.Content(role="user", parts=[types.Part(text=brief)])
        seen: set[str] = set()
        async for ev in runner.run_async(user_id=jid, session_id=session.id, new_message=msg):
            a = getattr(ev, "author", None)
            if a and a not in seen:
                seen.add(a)
                escrow.add_event(jid, f"agent stage: {a}")
        s = await runner.session_service.get_session(
            app_name="lumina_mkt", user_id=jid, session_id=session.id
        )
        st = dict(s.state)
        package = {
            "assets": escrow.extract_assets(st),
            "copy": st.get("copy_doc"),
            "qa_report": st.get("qa_report"),
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
    <ol id="events" class="mt-3 text-xs text-stone-500 space-y-1"></ol>
    <div id="copy" class="mt-4 text-sm hidden"></div>
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
  if(j.package){
    const a = j.package.assets || [];
    $('#gallery').innerHTML = a.map(x => x.type==='video'
      ? `<video controls class="rounded-lg w-full" src="/api/asset?uri=${encodeURIComponent(x.uri)}"></video>`
      : `<img class="rounded-lg w-full" src="/api/asset?uri=${encodeURIComponent(x.uri)}">`).join('');
    if(j.package.copy){ $('#copy').classList.remove('hidden'); $('#copy').innerHTML = '<div class="font-medium">Ad copy</div><pre class="whitespace-pre-wrap text-xs text-stone-600 mt-1">'+ (j.package.copy||'') +'</pre>'; }
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
