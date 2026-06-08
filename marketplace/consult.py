"""Shared, content-aware consultant engine.

Drives a real DIALOGUE with the customer that STUDIES their content live — vision-analyses the
uploaded product photo and fetches/reads any brand or design link they share (web page via the
``url_context`` tool, or an image URL via vision) — then gathers wishes, proactively invites the
customer to share more (photo / branding / a link to their site or design) and proposes a concrete,
priced production plan.

The same engine powers BOTH surfaces:
  • the Studio UI chat (``/api/consult`` in marketplace.app)
  • the external marketplace consult webhook (``consult.message`` in marketplace.app)

Design split: this module does the *dialogue + live study* and returns a decision
(``text`` / ``propose`` / ``spec``). Pricing, proposal-shaping and order kickoff stay in
``marketplace.app`` (single source of truth for money + escrow).
"""
from __future__ import annotations

import concurrent.futures
import json
import re

import httpx
from google.genai import types

from lumina.clients import gemini_client
from lumina.config import settings
from lumina.tools.delivery import mime_for_uri

_URL_RE = re.compile(r"https?://[^\s)>\]}\"'»]+", re.I)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")

# Studied content is cached by source URL/URI so a multi-turn chat (especially the stateless
# external webhook, which replays full history every turn) never re-analyses the same photo/link.
_STUDY_CACHE: dict[str, dict] = {}


def find_links(text: str) -> list[str]:
    """All http(s) URLs in a string, de-duplicated, order-preserving."""
    out, seen = [], set()
    for m in _URL_RE.findall(text or ""):
        u = m.rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _looks_like_image(url: str, content_type: str = "") -> bool:
    path = url.split("?", 1)[0].lower()
    return content_type.lower().startswith("image/") or path.endswith(_IMG_EXTS)


def _parse_json(text: str) -> dict:
    """Tolerant JSON parse: strips ```json fences / prose and grabs the first object."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        t = t[4:] if t[:4].lower() == "json" else t
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else {}
    except Exception:
        s, e = t.find("{"), t.rfind("}")
        if 0 <= s < e:
            try:
                v = json.loads(t[s : e + 1])
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
        return {}


def _image_part(url_or_uri: str) -> types.Part | None:
    """A genai image Part from a gs:// URI (Vertex fetches it) or an http(s) URL (we download it)."""
    if url_or_uri.startswith("gs://"):
        return types.Part.from_uri(file_uri=url_or_uri, mime_type=mime_for_uri(url_or_uri))
    try:
        r = httpx.get(url_or_uri, timeout=20, follow_redirects=True)
        r.raise_for_status()
        mime = (r.headers.get("content-type") or "image/png").split(";", 1)[0].strip()
        return types.Part.from_bytes(data=r.content, mime_type=mime or "image/png")
    except Exception:
        return None


_IMG_STUDY_PROMPT = (
    "You are a creative director studying a customer's image before a marketing shoot. The image is "
    "either their PRODUCT photo or a brand/design reference (moodboard, site screenshot, palette).\n"
    "Look closely and return STRICT JSON:\n"
    '  "kind": "product" or "reference",\n'
    '  "category": one of [apparel, jewelry, cosmetics, beverage, electronics, footwear, accessory, '
    'home, food, other] (best guess; use "other" for non-product references),\n'
    '  "description": 2-4 factual sentences on exactly what you see (product type, materials, colors, '
    "finish, distinctive details, any visible text/logos) — describe only what is really there,\n"
    '  "palette": 2-4 dominant colors as plain names or hex,\n'
    '  "tone": 3-5 mood/style words (e.g. "calm, clinical, premium, minimal"),\n'
    '  "style": one short phrase on the visual style.'
)


def study_image(url_or_uri: str) -> dict:
    """Vision-analyse a product photo or design/reference image. Cached by source."""
    if url_or_uri in _STUDY_CACHE:
        return _STUDY_CACHE[url_or_uri]
    part = _image_part(url_or_uri)
    if part is None:
        res = {"ok": False, "url": url_or_uri, "error": "could not load image"}
        _STUDY_CACHE[url_or_uri] = res
        return res
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=[part, types.Part(text=_IMG_STUDY_PROMPT)],
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=1024),
        )
        data = _parse_json(resp.text)
        res = {
            "ok": bool(data),
            "url": url_or_uri,
            "media": "image",
            "kind": str(data.get("kind") or "product"),
            "category": str(data.get("category") or "other").lower(),
            "description": str(data.get("description") or "").strip(),
            "palette": data.get("palette") or "",
            "tone": data.get("tone") or "",
            "style": str(data.get("style") or "").strip(),
        }
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "url": url_or_uri, "error": str(e)[:160]}
    _STUDY_CACHE[url_or_uri] = res
    return res


_PAGE_STUDY_PROMPT = (
    "Read the web page at this URL and extract the brand's identity for a marketing shoot. "
    "If it is a brand/product/design page, capture concrete cues; if you cannot access it, say so.\n"
    "URL: {url}\n\n"
    "Return STRICT JSON (no prose, no code fences):\n"
    '  "summary": 1-2 sentences on what the brand/site is and its positioning,\n'
    '  "palette": dominant colors (names or hex codes if visible),\n'
    '  "tone": 3-6 brand voice / mood words,\n'
    '  "style": short phrase on the visual style (typography, imagery direction),\n'
    '  "accessible": true if you could actually read the page, false otherwise.'
)


def study_page(url: str) -> dict:
    """Read a web page with Gemini's url_context tool and extract brand cues. Cached by URL."""
    if url in _STUDY_CACHE:
        return _STUDY_CACHE[url]
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=_PAGE_STUDY_PROMPT.format(url=url),
            config=types.GenerateContentConfig(
                tools=[types.Tool(url_context=types.UrlContext())],
                max_output_tokens=1024,
            ),
        )
        data = _parse_json(resp.text)
        res = {
            "ok": bool(data) and data.get("accessible", True) is not False,
            "url": url,
            "media": "page",
            "summary": str(data.get("summary") or "").strip(),
            "palette": data.get("palette") or "",
            "tone": data.get("tone") or "",
            "style": str(data.get("style") or "").strip(),
        }
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "url": url, "media": "page", "error": str(e)[:160]}
    _STUDY_CACHE[url] = res
    return res


def study_link(url: str) -> dict:
    """Study one shared link: an image URL -> vision; otherwise a web page -> url_context."""
    if _looks_like_image(url):
        return study_image(url)
    # Quick content-type probe so a bare image URL (no extension) is still vision-analysed.
    try:
        head = httpx.head(url, timeout=8, follow_redirects=True)
        if _looks_like_image(url, head.headers.get("content-type", "")):
            return study_image(url)
    except Exception:
        pass
    return study_page(url)


def _fmt(label: str, val) -> str:
    if isinstance(val, (list, tuple)):
        val = ", ".join(str(v) for v in val if v)
    val = str(val or "").strip()
    return f"{label}: {val}" if val else ""


def study_all(photo_url: str | None, message: str, history: list[dict], deadline_s: float = 12.0) -> dict:
    """Live-study the product photo + any links shared in the latest message, best-effort in parallel.

    Returns a dict with the studied facts plus a compact human ``chip`` and a prompt-ready
    ``facts`` block the dialogue model conditions on. Bounded by ``deadline_s`` so a slow page never
    stalls the reply (the production pipeline reads links again anyway).
    """
    # Collect links from the latest message AND recent customer turns (cache makes repeats cheap).
    texts = [message or ""]
    for h in (history or [])[-6:]:
        if h.get("sender", "user") != "agent":
            texts.append(h.get("text", ""))
    links, seen = [], set()
    for t in texts:
        for u in find_links(t):
            if u not in seen:
                seen.add(u)
                links.append(u)
    jobs: list[tuple[str, str]] = []  # (role, url)
    if photo_url:
        jobs.append(("product", photo_url))
    for u in links[:3]:  # bound fan-out / latency
        jobs.append(("link", u))
    if not jobs:
        return {"product": None, "references": [], "chip": "", "facts": ""}

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        fut = {ex.submit(study_image if role == "product" else study_link, url): (role, url) for role, url in jobs}
        done, _ = concurrent.futures.wait(fut, timeout=deadline_s)
        for f in done:
            role, url = fut[f]
            try:
                results[url] = {**f.result(), "_role": role}
            except Exception:
                pass

    product = None
    references: list[dict] = []
    for role, url in jobs:
        r = results.get(url)
        if not r or not r.get("ok"):
            continue
        if role == "product" or r.get("kind") == "product":
            if product is None:
                product = r
        else:
            references.append(r)

    # Compact chip for the UI + a facts block for the model.
    chip_bits, fact_lines = [], []
    if product:
        chip_bits.append("📸 studied your photo")
        fact_lines.append(
            "PRODUCT PHOTO (you can SEE it): "
            + "; ".join(
                x for x in (
                    _fmt("type", product.get("description")),
                    _fmt("palette", product.get("palette")),
                    _fmt("tone", product.get("tone")),
                ) if x
            )
        )
    for r in references:
        host = re.sub(r"^https?://(www\.)?", "", r["url"]).split("/", 1)[0]
        chip_bits.append(("🌐 read " if r.get("media") == "page" else "🖼️ studied ") + host)
        fact_lines.append(
            f"SHARED {'LINK' if r.get('media') == 'page' else 'REFERENCE'} ({r['url']}): "
            + "; ".join(
                x for x in (
                    _fmt("summary", r.get("summary") or r.get("description")),
                    _fmt("palette", r.get("palette")),
                    _fmt("tone", r.get("tone")),
                    _fmt("style", r.get("style")),
                ) if x
            )
        )
    # Note links we tried but couldn't read, so the model can say so honestly.
    for role, url in jobs:
        r = results.get(url)
        if role == "link" and (not r or not r.get("ok")):
            fact_lines.append(f"SHARED LINK ({url}): could NOT be read — ask the customer to describe it or share another.")

    return {
        "product": product,
        "references": references,
        "chip": " · ".join(chip_bits),
        "facts": "\n".join(fact_lines),
    }


_SYSTEM = (
    'You are the friendly, expert order consultant for "Lumina — Product Content Studio", an AI '
    "studio that turns a product PHOTO + brief into a complete on-brand content package: "
    "photorealistic product images (hero/macro/lifestyle/flat-lay/e-commerce), short videos "
    "(360° spin, voiceover ad, UGC, macro), designed product cards, and multi-variant marketing copy "
    "— faithful to the real product and the brand.\n"
    "You CHAT with the customer to understand their wishes and agree the SCOPE + PRICE.\n\n"
    "YOU CAN PERCEIVE THE CUSTOMER'S CONTENT: the photo they uploaded is vision-analysed and any "
    "link they paste is fetched and read for you (see STUDIED CONTENT below). When you have studied "
    "something, OPEN by reflecting one concrete observation back (a product detail, the site's "
    "palette/tone) so the customer feels understood — never claim to see things that aren't in "
    "STUDIED CONTENT.\n\n"
    "RULES:\n"
    "- Reply in the SAME LANGUAGE as the customer's latest message.\n"
    "- ONE warm, concise, expert reply (≈ ≤ 700 chars). Be a consultant, not a form.\n"
    "- PROACTIVELY invite richer inputs when missing: their product PHOTO (we NEED it to deliver), "
    "their BRANDING / brand guidelines, and a LINK to their website or design (site, Figma, Behance, "
    "Instagram) so you can match their exact style. Ask for these naturally, not all at once.\n"
    "- Gather target PLATFORM(s) (Instagram/TikTok/Amazon/web…) and rough VOLUME (images, videos, "
    "cards) and any mood/must-haves. Infer sensible defaults from the platform; don't over-ask "
    "(≤ ~3 short questions total, ideally 1–2).\n"
    "- PRICING: base $7 + $1/image + $2/video + $1/card. Use this exact formula when proposing.\n"
    "- DISCUSS FIRST. Set propose=true ONLY once you actually KNOW the product (the customer shared "
    "a photo or clearly described it) AND have a rough scope (at least a platform + how much "
    "content). If they are just greeting, asking what you do, or haven't shared their product yet, "
    "set propose=false: warmly explain what you do, invite their product photo + a link to their "
    "site/design, and ask 1–2 focused questions. NOTE: 'price' mode only means the buyer cares about "
    "cost — you MAY give a rough ballpark range in your text, but NEVER attach a plan/offer until you "
    "have the product and the scope.\n\n"
    "Return STRICT JSON (no code fences):\n"
    '  "text": "<your reply to the customer, in their language; reference what you studied; if '
    'proposing, summarize the scope + total price and ask them to confirm>",\n'
    '  "propose": <true only when proposing a concrete plan now, else false>,\n'
    '  "spec": null OR {"platforms":[..],"images":<int>,"videos":<int>,"cards":<int>,'
    '"image_aspect_ratios":[..],"video_kinds":[..],"brief":"<one rich line grounded in what you '
    'studied>","product_image_url":"<url or empty>","brand_link":"<the customer\'s site/design URL '
    'or empty>","language":"<customer language>","mood":"<optional>"},\n'
    '  "etaMinutes": <int 10-40>'
)


def run_consult(
    message: str,
    history: list[dict],
    photo_url: str | None = None,
    mode: str = "interview",
    deadline_s: float = 12.0,
    studied: dict | None = None,
) -> dict:
    """One consultation turn. Studies any new content, then decides reply / proposal.

    Returns: {"text", "propose": bool, "spec": dict|None, "etaMinutes": int, "studied": dict}.
    The caller (marketplace.app) prices the spec and shapes the proposal / order.
    """
    history = history or []
    if studied is None:
        studied = study_all(photo_url, message, history, deadline_s=deadline_s)

    lines = [f"{h.get('sender', 'user')}: {h.get('text', '')}" for h in history[-12:]]
    if message and (not history or history[-1].get("text") != message):
        lines.append(f"user: {message}")
    convo = "\n".join(lines) or "(no messages yet)"

    facts = studied.get("facts") or ""
    studied_block = f"\n\nSTUDIED CONTENT (what you have perceived):\n{facts}" if facts else (
        "\n\nSTUDIED CONTENT: (nothing shared yet — invite the customer to upload their product photo "
        "and share a link to their site/branding/design)."
    )

    fallback = "Tell me about your product and where you'll publish it — share your product photo and a link to your site or design, and I'll put together a plan."
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=f"{_SYSTEM}{studied_block}\n\nConversation so far:\n{convo}\n\nCurrent consult mode: {mode}",
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=2048),
        )
        data = _parse_json(resp.text)
    except Exception:
        data = {}

    text = (data.get("text") or "").strip()[:6000] or fallback
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
    # DISCUSS FIRST: only propose (→ escrow offer) when there's real substance — a product photo, a
    # studied product, or an actual back-and-forth. The platform sends mode=="price" on EVERY turn
    # (dynamic pricing); that must never, by itself, trigger an offer on a bare first greeting.
    has_substance = bool(photo_url) or bool(studied.get("product")) or len(history) >= 2
    propose = bool(data.get("propose")) and has_substance
    eta = min(40, max(10, int(data.get("etaMinutes") or 30))) if data.get("etaMinutes") else 30
    # Carry a studied brand/design link into the spec so the funded order grounds on it.
    if spec is not None and not spec.get("brand_link"):
        for r in studied.get("references", []):
            if r.get("media") == "page":
                spec["brand_link"] = r["url"]
                break
    return {"text": text, "propose": propose and spec is not None, "spec": spec, "etaMinutes": eta, "studied": studied}
