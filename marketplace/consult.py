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
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

import httpx
from google.genai import types

from lumina.clients import gemini_client
from lumina.config import settings
from lumina.pricing import FREE_REVISIONS
from lumina.tools.delivery import mime_for_uri

_URL_RE = re.compile(r"https?://[^\s)>\]}\"'»]+", re.I)
# Customers usually paste links WITHOUT a scheme ("instagram.com/myshop", "www.brand.de"). Accepted
# only with a www. prefix, a path, or a common TLD, so filenames/version strings don't become links.
_BARE_URL_RE = re.compile(
    r"(?<![\w@.\-/])(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s)>\]}\"'»]*)?",
    re.I,
)
_COMMON_TLDS = {
    "com", "net", "org", "io", "co", "ai", "app", "dev", "me", "shop", "store", "site", "online",
    "biz", "info", "xyz", "ru", "kz", "ua", "by", "uk", "de", "fr", "es", "it", "nl", "pl", "tr",
    "us", "ca", "br", "mx", "in", "cn", "jp", "kr", "au",
}
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
# Login-walled platforms: their pages can't be fetched by url_context (or anything unauthenticated),
# so we go straight to Google Search and, failing that, ask the customer for screenshots.
_WALLED_HOSTS = ("instagram.com", "facebook.com", "fb.com", "tiktok.com", "x.com", "twitter.com", "threads.net")
# Many CDNs / shops 403 the default python-httpx user agent; fetch like a regular browser.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.9,*/*;q=0.8",
}

# Studied content is cached by source URL/URI so a multi-turn chat (especially the stateless
# external webhook, which replays full history every turn) never re-analyses the same photo/link.
_STUDY_CACHE: dict[str, dict] = {}


def is_public_http_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves exclusively to PUBLIC IP addresses.

    SSRF guard for every server-side fetch of a user-supplied URL (buyer product photo, brand
    link, consult message). Blocks loopback, private (RFC1918), link-local — including the cloud
    metadata server 169.254.169.254 — and other non-global ranges, plus *.internal/*.local and
    bare 'localhost'/'metadata' hostnames. Fails closed: anything we can't positively confirm as
    public is rejected.
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    if host in ("localhost", "metadata", "metadata.google.internal") or host.endswith((".internal", ".local")):
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # unwrap ::ffff:169.254.169.254 and friends
        if (not ip.is_global) or ip.is_private or ip.is_loopback or ip.is_link_local \
                or ip.is_reserved or ip.is_multicast:
            return False
    return True


def guarded_request(method: str, url: str, *, timeout: float, headers: dict | None = None,
                    max_redirects: int = 4):
    """httpx request that re-validates EVERY hop with is_public_http_url.

    Auto-redirects are followed manually so a public URL can't 302-redirect us onto an internal
    address (SSRF-via-redirect). Returns the final httpx.Response, or None if any target is
    non-public, it redirects too many times, or the request errors. Replaces direct httpx.get/head
    for all user-supplied URLs.
    """
    cur = url
    try:
        for _ in range(max_redirects + 1):
            if not is_public_http_url(cur):
                return None
            r = httpx.request(method, cur, timeout=timeout, headers=headers, follow_redirects=False)
            loc = r.headers.get("location")
            if r.is_redirect and loc:
                cur = str(r.url.join(loc))
                continue
            return r
    except Exception:
        return None
    return None


def find_links(text: str) -> list[str]:
    """All http(s) URLs in a string — scheme-less ones ("instagram.com/myshop") normalized to
    https:// — de-duplicated, order-preserving."""
    out, seen = [], set()

    def _add(u: str) -> None:
        u = u.rstrip(".,;")
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    t = text or ""
    for m in _URL_RE.findall(t):
        _add(m)
    for m in _BARE_URL_RE.findall(_URL_RE.sub(" ", t)):  # sub: don't re-match inside found URLs
        host = m.split("/", 1)[0].lower()
        if host.startswith("www.") or "/" in m or host.rsplit(".", 1)[-1] in _COMMON_TLDS:
            _add("https://" + m)
    return out


def _is_walled(url: str) -> bool:
    host = re.sub(r"^https?://(?:www\.)?", "", url, flags=re.I).split("/", 1)[0].lower()
    return any(host == w or host.endswith("." + w) for w in _WALLED_HOSTS)


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


def _image_part(url_or_uri: str) -> tuple[types.Part | None, str]:
    """A genai image Part from a gs:// URI (Vertex fetches it) or an http(s) URL (we download it).

    Returns (part, fail_reason): reason is "" on success, "bot_blocked" when the host clearly
    refuses automated access (401/403/429, a Cloudflare challenge, or an HTML page where an image
    should be), "error" for anything else.
    """
    if url_or_uri.startswith("gs://"):
        return types.Part.from_uri(file_uri=url_or_uri, mime_type=mime_for_uri(url_or_uri)), ""
    try:
        r = guarded_request("GET", url_or_uri, timeout=20, headers=BROWSER_HEADERS)
        if r is None:  # blocked by the SSRF guard (non-public target) or too many redirects
            return None, "error"
        if r.status_code in (401, 403, 429) or (
            r.status_code == 503 and ("cf-ray" in r.headers or "cloudflare" in (r.headers.get("server") or "").lower())
        ):
            return None, "bot_blocked"
        r.raise_for_status()
        mime = (r.headers.get("content-type") or "image/png").split(";", 1)[0].strip().lower()
        if mime.startswith("text/"):  # expected an image, got an HTML challenge/login page
            return None, "bot_blocked"
        return types.Part.from_bytes(data=r.content, mime_type=mime or "image/png"), ""
    except Exception:
        return None, "error"


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
    part, fail = _image_part(url_or_uri)
    if part is None:
        res = {"ok": False, "url": url_or_uri, "media": "image", "error": "could not load image"}
        if fail == "bot_blocked":
            res["bot_blocked"] = True
        _STUDY_CACHE[url_or_uri] = res
        return res
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=[part, types.Part(text=_IMG_STUDY_PROMPT)],
            # gemini-3.5-flash is a THINKING model — thinking tokens are drawn from this budget, so a
            # tight cap can make it emit EMPTY text (all budget spent thinking) and look like the photo
            # "couldn't be read". Give the same headroom vision.py uses for image analysis.
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=3072),
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


_SEARCH_STUDY_PROMPT = (
    "The page at this URL cannot be opened directly: {url}\n"
    "Use Google Search to find out what this site/shop/account is about. Report ONLY what search "
    "actually returns about THIS exact brand/page — do not guess or invent.\n"
    "Return STRICT JSON (no prose, no code fences):\n"
    '  "found": true ONLY if search returned reliable info about this exact page/brand,\n'
    '  "summary": 1-2 sentences on what it is and its positioning (empty if not found),\n'
    '  "palette": dominant brand colors if known,\n'
    '  "tone": 3-6 brand voice / mood words,\n'
    '  "style": short phrase on the visual style.'
)


def study_page_via_search(url: str) -> dict:
    """Fallback when a page can't be fetched (login wall / bot-blocked): learn about it from Google Search."""
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=_SEARCH_STUDY_PROMPT.format(url=url),
            # google_search grounding can't be combined with response_mime_type=json — parse leniently.
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=4096,
            ),
        )
        data = _parse_json(resp.text)
        if not data or data.get("found") is not True:
            return {"ok": False, "url": url, "media": "page"}
        return {
            "ok": True,
            "url": url,
            "media": "page",
            "via": "search",
            "summary": str(data.get("summary") or "").strip(),
            "palette": data.get("palette") or "",
            "tone": data.get("tone") or "",
            "style": str(data.get("style") or "").strip(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url": url, "media": "page", "error": str(e)[:160]}


def study_page(url: str) -> dict:
    """Read a web page with Gemini's url_context tool and extract brand cues. Cached by URL.

    Login-walled platforms (Instagram & co) skip url_context — it always fails there — and go
    straight to the Google-Search fallback; the ``walled`` flag lets the dialogue ask for
    screenshots instead when even search comes up empty.
    """
    if url in _STUDY_CACHE:
        return _STUDY_CACHE[url]
    if _is_walled(url):
        res = {**study_page_via_search(url), "walled": True}
        _STUDY_CACHE[url] = res
        return res
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=_PAGE_STUDY_PROMPT.format(url=url),
            config=types.GenerateContentConfig(
                tools=[types.Tool(url_context=types.UrlContext())],
                # Thinking model + url_context retrieval + JSON all draw on this budget; a tight cap
                # makes the model return EMPTY and report the page as unreadable even when it loaded.
                max_output_tokens=4096,
            ),
        )
        data = _parse_json(resp.text)
        # Ground truth from the tool itself: if retrieval ERRORed, the model never saw the page —
        # whatever it "extracted" is training-data hallucination. Distrust it and flag the block.
        fetch_failed = False
        try:
            metas = (resp.candidates[0].url_context_metadata.url_metadata or []) if resp.candidates else []
            fetch_failed = bool(metas) and all("ERROR" in str(m.url_retrieval_status) for m in metas)
        except Exception:
            pass
        res = {
            "ok": bool(data) and data.get("accessible", True) is not False and not fetch_failed,
            "url": url,
            "media": "page",
            "summary": str(data.get("summary") or "").strip(),
            "palette": data.get("palette") or "",
            "tone": data.get("tone") or "",
            "style": str(data.get("style") or "").strip(),
        }
        if fetch_failed:
            res["bot_blocked"] = True  # site refused the fetcher (anti-bot / wall), not just thin content
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "url": url, "media": "page", "error": str(e)[:160]}
    if not res["ok"]:  # fetch refused/failed (bot-blocked site etc.) — try learning via Search
        fb = study_page_via_search(url)
        if fb.get("ok"):
            res = fb
    _STUDY_CACHE[url] = res
    return res


def study_link(url: str) -> dict:
    """Study one shared link: an image URL -> vision; otherwise a web page -> url_context."""
    if _looks_like_image(url):
        return study_image(url)
    if _is_walled(url):  # don't probe login-walled hosts — study_page routes them via Search
        return study_page(url)
    # Quick content-type probe so a bare image URL (no extension) is still vision-analysed.
    head = guarded_request("HEAD", url, timeout=8, headers=BROWSER_HEADERS)
    if head is not None and _looks_like_image(url, head.headers.get("content-type", "")):
        return study_image(url)
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
        icon = "🔎 researched " if r.get("via") == "search" else ("🌐 read " if r.get("media") == "page" else "🖼️ studied ")
        chip_bits.append(icon + host)
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
            if r and r.get("walled"):
                fact_lines.append(
                    f"SHARED LINK ({url}): this platform is login-walled — you CANNOT open it, and web "
                    "search found nothing about this account. Say so honestly and ask for a SCREENSHOT "
                    "of the profile/page or 2-3 product photos instead (you can study images)."
                )
            elif r and r.get("bot_blocked"):
                what = "image" if r.get("media") == "image" else "site"
                fact_lines.append(
                    f"SHARED LINK ({url}): this {what} has BOT PROTECTION — it actively refused automated "
                    "access. Tell the customer EXPLICITLY that their link is protected from bots so you "
                    "could not open it, and ask them to upload the photo/screenshot directly in the chat."
                )
            else:
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
    "link they paste is fetched and read for you — web pages are read, direct image links (a product "
    "photo as a URL) are vision-analysed (see STUDIED CONTENT below). When you have studied "
    "something, OPEN by reflecting one concrete observation back (a product detail, the site's "
    "palette/tone) so the customer feels understood — never claim to see things that aren't in "
    "STUDIED CONTENT. If STUDIED CONTENT says a link is bot-protected or login-walled, tell the "
    "customer EXPLICITLY that the site blocks automated access, and ask for a direct upload "
    "(photo/screenshot) instead — never pretend you saw it.\n\n"
    "RULES:\n"
    "- Reply in the SAME LANGUAGE as the customer's latest message.\n"
    "- ONE warm, concise, expert reply (≈ ≤ 700 chars). Be a consultant, not a form.\n"
    "- PROACTIVELY invite richer inputs when missing: their product PHOTO (we NEED it to deliver), "
    "their BRANDING / brand guidelines, and a LINK to their website or design (site, Figma, Behance, "
    "Instagram) so you can match their exact style. Ask for these naturally, not all at once.\n"
    "- Gather target PLATFORM(s) (Instagram/TikTok/Amazon/web…) and rough VOLUME (images, videos, "
    "cards) and any mood/must-haves. Infer sensible defaults from the platform; don't over-ask "
    "(≤ ~3 short questions total, ideally 1–2). If the customer doesn't specify volume, propose the "
    "BASE PACKAGE: 16 varied images + 2 videos + 2 cards.\n"
    "- PRICING: a flat $10 BASE PACKAGE includes 16 images + 2 videos + 2 cards. Beyond the base: "
    "+$1 per extra image, +$3 per extra video, +$2 per extra card. Below the base lowers the price: "
    "−$3 per dropped video and −$2 per dropped card (all 16 images are always included), with a $5 "
    "minimum. Use exactly this when proposing (e.g. 16 img + 3 videos + 2 cards = $13; 16 img + 1 "
    "video + 2 cards = $7).\n"
    f"- REVISION POLICY: every order includes up to {FREE_REVISIONS} free revisions; each revision "
    "redoes ONLY what the customer asks to change and never more than the paid scope (no free "
    "top-ups); questions are always free. Mention this briefly when proposing a plan, and refer "
    "to it if the customer expects unlimited re-rolls.\n"
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


def _graceful_fallback(message: str, history: list[dict]) -> str:
    """Last-resort reply when the model returns nothing usable.

    Stays in the customer's language and, mid-conversation, does NOT reset to the cold-open greeting
    (which reads as the agent forgetting everything that was discussed). Only a true first contact
    gets the "tell me about your product" invite.
    """
    ru = sum(1 for c in (message or "") if "Ѐ" <= c <= "ӿ") >= 3
    if len(history) < 2:  # genuine first contact — invite the product photo + a link
        return (
            "Расскажите о вашем продукте и где будете его публиковать — пришлите фото товара и ссылку "
            "на ваш сайт или дизайн, и я соберу план."
            if ru else
            "Tell me about your product and where you'll publish it — share your product photo and a "
            "link to your site or design, and I'll put together a plan."
        )
    return (  # mid-chat: keep context, ask them to restate the last message rather than starting over
        "Секунду — кажется, я потерял нить. Повторите, пожалуйста, последнее сообщение, "
        "и я сразу обновлю расчёт."
        if ru else
        "Sorry — I lost the thread for a second. Could you repeat that last bit, and I'll update the "
        "plan right away?"
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

    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=f"{_SYSTEM}{studied_block}\n\nConversation so far:\n{convo}\n\nCurrent consult mode: {mode}",
            # gemini-3.5-flash is a THINKING model: thinking tokens are drawn from this same budget, so
            # a tight cap makes a harder turn (e.g. re-scoping an agreed plan — "drop the videos") spend
            # everything on thinking and emit EMPTY text. That silently collapsed to the cold-open
            # fallback, making the agent look like it forgot the whole conversation. In test mode we
            # give the discussion generous headroom so a reply never gets starved mid-thought.
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=16384),
        )
        data = _parse_json(resp.text)
    except Exception as e:  # noqa: BLE001
        print(f"[consult] dialogue model error: {e!r}", flush=True)
        data = {}

    text = (data.get("text") or "").strip()[:6000]
    if not text:  # empty/garbled model output — degrade gracefully WITHOUT dumping the cold greeting
        print(f"[consult] empty dialogue text -> graceful fallback (history_turns={len(history)})", flush=True)
        text = _graceful_fallback(message, history)
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
    # Likewise the product photo when it arrived as a LINK in chat (no uploaded attachment):
    # the studied image URL becomes the order's product image.
    if spec is not None and not spec.get("product_image_url"):
        p = studied.get("product")
        if p and p.get("media") == "image" and str(p.get("url") or "").startswith(("http://", "https://", "gs://")):
            spec["product_image_url"] = p["url"]
    return {"text": text, "propose": propose and spec is not None, "spec": spec, "etaMinutes": eta, "studied": studied}


def classify_revision(revision_text: str, spec: dict | None, prior_assets: list[dict]) -> dict | None:
    """Scope a buyer's revision: which delivered asset kinds actually need regeneration.

    A revision like "make the cards in English" must redo ONLY the cards — not re-shoot every
    image and video the buyer already approved. Returns
    {"regenerate": {"images","videos","cards": bool}, "language", "image_count", "card_count",
    "video_kinds", "summary"} — or None when the model gives nothing usable (the caller then
    falls back to a full re-run, the safe default).
    """
    have: dict[str, int] = {}
    for a in prior_assets or []:
        k = str(a.get("type") or "?")
        have[k] = have.get(k, 0) + 1
    prompt = (
        "You scope a REVISION request for an already-delivered product-content package on a "
        "freelance marketplace. Decide the MINIMAL set of asset kinds that must be regenerated to "
        "satisfy the buyer; every other kind is kept untouched from the previous delivery.\n\n"
        f"Buyer's revision message:\n{revision_text}\n\n"
        f"Original production spec of the paid order:\n{json.dumps(spec or {}, ensure_ascii=False)}\n\n"
        f"Previously delivered assets (kind: count): {json.dumps(have, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "• Mark a kind true ONLY if satisfying the message requires changing that kind: 'product "
        "cards in English' -> cards only; 'the photos feel dark' -> images only; 'redo everything "
        "in a luxury style' -> every kind the order contains.\n"
        "• Marketing copy/text is always refreshed automatically — never regenerate images merely "
        "because wording changes.\n"
        "• If the message is ONLY a question / pure information with NO content change implied, set "
        "all kinds false and answer the question directly in 'summary'.\n"
        "• 'language': a short code ('en', 'ru', …) ONLY when the buyer asks for a language change, "
        "else \"\".\n"
        "• 'image_count'/'card_count'/'video_kinds': fill ONLY when the buyer explicitly asks for a "
        "different quantity or video type, else null.\n"
        "• The PAID scope is a hard cap: a revision can never produce MORE items of a kind than the "
        "original order paid for, nor a kind the order didn't include — never promise that in "
        "'summary' (suggest a separate order instead).\n"
        "• 'summary': ONE short sentence in the buyer's language saying what you will do.\n\n"
        'Reply with EXACTLY this JSON object: {"regenerate": {"images": <bool>, "videos": <bool>, '
        '"cards": <bool>}, "language": "<code or empty>", "image_count": <int or null>, '
        '"card_count": <int or null>, "video_kinds": <["360"|"voiceover"|"ugc"|"macro", ...] or null>, '
        '"summary": "<one line>"}'
    )
    try:
        resp = gemini_client().models.generate_content(
            model=settings.model_reasoning,
            contents=prompt,
            # Thinking model: generous output budget so the JSON never starves mid-thought
            # (same failure class as run_consult's empty-reply fallback).
            config=types.GenerateContentConfig(response_mime_type="application/json", max_output_tokens=8192),
        )
        data = _parse_json(resp.text)
    except Exception as e:  # noqa: BLE001
        print(f"[revision] classifier error: {e!r}", flush=True)
        return None
    regen = data.get("regenerate")
    if not isinstance(regen, dict):
        return None
    data["regenerate"] = {k: bool(regen.get(k)) for k in ("images", "videos", "cards")}
    return data
