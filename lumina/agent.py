"""Root agent entrypoint.

The full multi-agent pipeline is assembled in ``lumina.agents.pipeline``. ADK tooling
(``adk run`` / ``adk web`` / ``adk deploy``) and ``run_slice.py`` discover ``root_agent`` here.
"""
from .agents.pipeline import root_agent

__all__ = ["root_agent"]
