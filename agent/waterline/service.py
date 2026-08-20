"""FastAPI service: the briefing as a Server-Sent Events stream.

POST /brief streams the merged agent/layer/step/panel events as they happen, so
the browser paints the map incrementally — the "map builds itself" effect. The
whole thing runs behind one Cloud Run service; PostGIS is Cloud SQL.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .run import run_briefing, make_session_service

app = FastAPI(title="Waterline", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# One durable session service for the process, so a killed briefing can resume
# under the same session_id (crash-recovery beat).
_SESSIONS = make_session_service()


class BriefRequest(BaseModel):
    text: str
    session_id: str = "demo"
    responsible_email: str | None = None   # enables the real-world loop (flight-following)
    eta: str | None = None
    grace_min: int = 60


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "waterline"}


@app.post("/brief")
async def brief(req: BriefRequest) -> StreamingResponse:
    async def gen():
        # opening comment defeats proxy buffering; keeps the stream flowing
        yield ":ok\n\n"
        async for ev in run_briefing(
            req.text, session_id=req.session_id, session_service=_SESSIONS,
            responsible_email=req.responsible_email, eta=req.eta, grace_min=req.grace_min,
        ):
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
