"""Drive the roster and merge two event sources into one stream.

Two things happen concurrently during a briefing: the agents emit their own
step/summary text (from run_async), and the tools emit map layers (onto the
per-request queue). This runner binds the queue, drives the pipeline in a
background task that pushes agent events onto the same queue, and yields the
merged stream. The map layers and the agent narration arrive interleaved, in the
order they actually happen — which is what makes the map look like it assembles
itself as the agents reason.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncGenerator, Optional

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, BaseSessionService
from google.genai import types

from .agents.pipeline import build_pipeline
from .config import APP_NAME
from .emit import bind_queue, unbind_queue


def make_session_service() -> BaseSessionService:
    """DatabaseSessionService (durable, survives a crash) when a URL is set;
    InMemory otherwise. Durable sessions are what let a killed briefing resume."""
    url = os.environ.get("WATERLINE_SESSION_DB")
    if url:
        from google.adk.sessions import DatabaseSessionService
        return DatabaseSessionService(db_url=url)
    return InMemorySessionService()


async def run_briefing(
    user_text: str,
    session_id: str = "demo",
    user_id: str = "pilot",
    session_service: Optional[BaseSessionService] = None,
    responsible_email: Optional[str] = None,
    eta: Optional[str] = None,
    grace_min: int = 60,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield merged {type: layer|step|panel|agent|error|done} events for one briefing.

    responsible_email/eta/grace_min seed session state for the DispatchAgent (the
    real-world loop). If responsible_email is None, dispatch is a no-op — the send
    is gated on an explicit human contact.
    """
    seed = {k: v for k, v in
            {"responsible_email": responsible_email, "eta": eta, "grace_min": grace_min}.items()
            if v is not None}
    q: "asyncio.Queue[dict[str, Any] | None]" = asyncio.Queue()
    token = bind_queue(q)
    svc = session_service or make_session_service()
    # create_session is idempotent-friendly: reuse an existing one (resume) if present.
    existing = await svc.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    if existing is None:
        await svc.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    runner = Runner(app_name=APP_NAME, agent=build_pipeline(), session_service=svc)

    async def drive() -> None:
        try:
            msg = types.Content(role="user", parts=[types.Part(text=user_text)])
            async for ev in runner.run_async(user_id=user_id, session_id=session_id,
                                              new_message=msg, state_delta=seed or None):
                if ev.content and ev.content.parts:
                    txt = "".join(p.text for p in ev.content.parts if getattr(p, "text", None))
                    if txt.strip():
                        q.put_nowait({"type": "agent", "author": ev.author,
                                      "final": ev.is_final_response(), "text": txt})
        except Exception as e:  # surface, don't swallow — the client shows it
            q.put_nowait({"type": "error", "detail": f"{type(e).__name__}: {e}"})
        finally:
            q.put_nowait(None)

    task = asyncio.create_task(drive())
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            yield item
        yield {"type": "done"}
    finally:
        unbind_queue(token)
        await task
