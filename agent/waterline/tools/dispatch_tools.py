"""DispatchAgent tools — the real-world loop, human-gated.

file_and_notify files the itinerary and sends the flight-following notice to the
responsible person (the irreversible external action). It only fires when the
pilot has supplied a responsible person's email — the human gate.
"""
from __future__ import annotations

import asyncio
from typing import Any

from google.adk.tools import ToolContext

from ..dispatch import compose_following_notice, send_email
from ..emit import emit_step, emit_panel


async def file_and_notify(tool_context: ToolContext) -> dict[str, Any]:
    """File the flight itinerary and send the flight-following notice to the responsible person.

    Reads the responsible person's email, ETA, and grace window from session state
    (supplied by the pilot). If no responsible email was provided, files nothing
    and reports that — the send is gated on an explicit human contact.
    Returns {filed: bool, sent: bool, channel, to} or {filed: false, reason}.
    """
    st = tool_context.state
    to = st.get("responsible_email")
    if not to:
        emit_step("DispatchAgent", "skipped", "No responsible person provided; itinerary not filed.")
        return {"filed": False, "reason": "no responsible_email supplied"}

    route = st.get("route", {})
    corridor = st.get("corridor", {})
    weather = st.get("weather", {})
    briefing = st.get("briefing", "")
    eta = st.get("eta", "this afternoon")
    grace = int(st.get("grace_min", 60))

    subject, body = compose_following_notice(route, corridor, weather, briefing, eta, grace)
    emit_step("DispatchAgent", "filing", f"Filing itinerary and notifying {to}…")
    result = await asyncio.to_thread(send_email, to, subject, body)
    emit_panel("dispatch", {"to": to, "eta": eta, "grace_min": grace, **result})
    emit_step("DispatchAgent", "filed",
              f"Itinerary filed; flight-following notice sent to {to} via {result['channel']}.")
    return {"filed": True, **result}
