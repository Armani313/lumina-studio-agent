"""Standalone A2A server: publishes the Lumina agent as an A2A service with a discoverable
AgentCard at /.well-known/agent-card.json.

Run:  .venv/bin/uvicorn marketplace.a2a_server:a2a_app --host 0.0.0.0 --port 8081
"""
from google.adk.a2a.utils.agent_to_a2a import to_a2a

from lumina.agent import root_agent

# Returns a Starlette app exposing the A2A protocol (message/send, tasks) + AgentCard.
a2a_app = to_a2a(root_agent, host="0.0.0.0", port=8081)
