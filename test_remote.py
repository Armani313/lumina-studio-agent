"""Query the deployed Agent Engine instance to verify the cloud graph end-to-end.

    .venv/bin/python test_remote.py
"""
import vertexai
from vertexai import agent_engines

RESOURCE = (
    "projects/587790795280/locations/us-central1/reasoningEngines/4329993888170246144"
)
BRIEF = (
    "Brand: 'Aurelia' — minimalist premium skincare; calm, clinical tone; earthy neutral "
    "palette. Product: 'Aurelia Glow Serum', a vitamin-C serum in a frosted glass dropper "
    "bottle. Features: brightening, lightweight, fragrance-free. Channel: instagram."
)

vertexai.init(project="aifreelance-hackathon", location="us-central1")
app = agent_engines.get(RESOURCE)
print("operation methods:", [s.get("name") for s in app.operation_schemas()])

session = app.create_session(user_id="remote-demo")
sid = session["id"] if isinstance(session, dict) else session.id
print("session id:", sid)
print("--- streaming remote run ---")

try:
    for event in app.stream_query(user_id="remote-demo", session_id=sid, message=BRIEF):
        if isinstance(event, dict) and event.get("error_message"):
            print("!! ERROR EVENT:", event.get("error_message"))
        author = event.get("author") if isinstance(event, dict) else getattr(event, "author", "?")
        content = event.get("content") if isinstance(event, dict) else None
        if not content:
            continue
        for part in content.get("parts", []):
            if part.get("text"):
                print(f"[{author}] {part['text'][:280]}")
            if part.get("function_call"):
                print(f"[{author}] -> tool: {part['function_call'].get('name')}")
            if part.get("function_response"):
                resp = part["function_response"].get("response", {})
                print(f"[{author}] <- {part['function_response'].get('name')}: {str(resp)[:240]}")
    print("--- stream ended ---")
except Exception:
    import traceback
    traceback.print_exc()
