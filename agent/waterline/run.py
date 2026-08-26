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
    session_id: str,
    user_id: str,
    session_service: Optional[BaseSessionService] = None,
    initial_state: Optional[dict[str, Any]] = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield merged {type: layer|step|panel|agent|error|done} events for one briefing.

    The API owns both ids and seeds authenticated mission identity. Consequential
    contact/attestation state is never accepted by this initial-run function.
    """
    q: "asyncio.Queue[dict[str, Any] | None]" = asyncio.Queue()
    token = bind_queue(q)
    svc = session_service or make_session_service()
    existing = await svc.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    if existing is not None:
        raise ValueError("server-generated session id already exists")
    await svc.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id,
        state=initial_state or {},
    )
    runner = Runner(app_name=APP_NAME, agent=build_pipeline(), session_service=svc)

    async def drive() -> None:
        try:
            msg = types.Content(role="user", parts=[types.Part(text=user_text)])
            async for ev in runner.run_async(user_id=user_id, session_id=session_id,
                                              new_message=msg):
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
