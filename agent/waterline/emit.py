"""Streaming layer emission — "the map builds itself" mechanic, framework-neutral.

A single choke point turns geometry into an SSE event. Each layer has a stable
id, so re-emitting the same layer replaces it in place on the client; a fresh
`rev` per emission tells the client to repaint (row counts repeat, revs never do).
Tools push layers here; the FastAPI stream drains them alongside the agent's own
step events. The MODEL never sees the geometry — a tool emits the layer and
returns only a scalar summary, so the LLM can't hallucinate coordinates onto the
map.
"""
from __future__ import annotations

import asyncio
import contextvars
import uuid
from typing import Any, Optional

# The per-request event queue. Tools reach it through this contextvar; because
# ADK runs tools inside the same async task tree as the request, the var
# propagates without threading it through every function signature.
_queue: contextvars.ContextVar[Optional["asyncio.Queue[dict[str, Any] | None]"]] = \
    contextvars.ContextVar("waterline_event_queue", default=None)


def bind_queue(q: "asyncio.Queue[dict[str, Any] | None]") -> contextvars.Token:
    return _queue.set(q)


def unbind_queue(token: contextvars.Token) -> None:
    _queue.reset(token)


def _put(event: dict[str, Any]) -> None:
    q = _queue.get()
    if q is not None:
        q.put_nowait(event)


def emit_layer(layer: str, label: str, geojson: dict[str, Any],
               row_count: int, bbox: Optional[list[float]] = None,
               scale: Optional[dict[str, Any]] = None) -> None:
    """Emit one map layer. Replaces any prior emission of the same `layer` id."""
    _put({
        "type": "layer",
        "layer": layer,
        "label": label,
        "rev": str(uuid.uuid4()),
        "status": "ready",
        "rowCount": row_count,
        "bbox": bbox,
        "scale": scale,
        "geojson": geojson,
    })


def emit_loading(layer: str, label: str) -> None:
    """Emit a placeholder so the client can show the layer as pending."""
    _put({"type": "layer", "layer": layer, "label": label,
          "rev": str(uuid.uuid4()), "status": "loading", "rowCount": 0})


def emit_step(agent: str, kind: str, detail: str) -> None:
    """Emit an agent-progress line for the roster panel (Continuous Action feed)."""
    _put({"type": "step", "agent": agent, "kind": kind, "detail": detail})


def emit_panel(key: str, value: Any) -> None:
    """Emit a structured briefing fragment for the side panel (not the map)."""
    _put({"type": "panel", "key": key, "value": value})
