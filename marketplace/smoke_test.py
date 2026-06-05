"""Smoke test for the marketplace flow: order -> escrow Funded -> agent run -> Delivered ->
accept -> Released.

Local:  .venv/bin/python marketplace/smoke_test.py
Cloud:  AUTH_TOKEN=$(gcloud auth print-identity-token) \
        .venv/bin/python marketplace/smoke_test.py https://<cloud-run-url>
"""
import os
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("LUMINA_BASE", "http://127.0.0.1:8080")).rstrip("/")
_TOKEN = os.getenv("AUTH_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {_TOKEN}"} if _TOKEN else {}

PHOTO = os.getenv("PHOTO", "outputs/grounded_4x5.png")
DESCRIPTION = os.getenv("BRIEF") or (
    "Brand: 'Aurelia' — minimalist premium skincare; calm, clinical tone. Product (see uploaded "
    "photo): 'Aurelia Glow Serum', a vitamin-C serum in a frosted glass dropper bottle. Features: "
    "brightening, lightweight, fragrance-free. Channel: instagram."
)


def main() -> None:
    print("target:", BASE)
    with open(PHOTO, "rb") as f:
        r = httpx.post(
            f"{BASE}/api/jobs",
            data={"description": DESCRIPTION, "brand_link": ""},
            files={"product_photo": ("product.png", f, "image/png")},
            headers=HEADERS,
            timeout=60,
        )
    r.raise_for_status()
    jid = r.json()["job_id"]
    print("created job:", jid)

    last = 0
    for _ in range(80):
        time.sleep(8)
        try:
            j = httpx.get(f"{BASE}/api/jobs/{jid}", headers=HEADERS, timeout=30).json()
        except Exception as ex:  # tolerate transient connection resets during long polling
            print("   (poll retry:", type(ex).__name__, ")")
            continue
        for e in j.get("events", [])[last:]:
            print("   •", e["msg"])
        last = len(j.get("events", []))
        print(f"   [status={j['status']} escrow={j['escrow']}]")
        if j["status"] in ("Delivered", "Failed", "Completed"):
            if j["status"] == "Delivered":
                print("accept ->", httpx.post(f"{BASE}/api/jobs/{jid}/accept", headers=HEADERS, timeout=30).json())
                j2 = httpx.get(f"{BASE}/api/jobs/{jid}", headers=HEADERS, timeout=30).json()
                pkg = j2.get("package") or {}
                print(f"FINAL status={j2['status']} escrow={j2['escrow']}")
                print("assets:", [(a["type"], a["uri"].split("/")[-1]) for a in pkg.get("assets", [])])
            break


if __name__ == "__main__":
    main()
