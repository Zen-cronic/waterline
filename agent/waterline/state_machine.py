"""Deterministic mission lifecycle shared by API and persistence layers.

Agents may propose text, but only this graph can move durable mission state.
Every successful edge is committed with an append-only evidence event.
"""
from __future__ import annotations

from typing import Literal


MissionStatus = Literal[
    "proposed",
    "rejected",
    "awaiting_attestation",
    "corrected",
    "accepted",
    "dispatched",
]

MISSION_STATUSES: tuple[MissionStatus, ...] = (
    "proposed",
    "rejected",
    "awaiting_attestation",
    "corrected",
    "accepted",
    "dispatched",
)

_ALLOWED_EDGES: frozenset[tuple[MissionStatus, MissionStatus]] = frozenset({
    ("proposed", "rejected"),
    ("rejected", "awaiting_attestation"),
    ("rejected", "proposed"),  # bounded recovery of the same mission/session
    ("awaiting_attestation", "corrected"),
    ("corrected", "accepted"),
    ("accepted", "dispatched"),
})


def require_transition(current: str, target: str) -> tuple[MissionStatus, MissionStatus]:
    """Return a typed allowed edge or fail before persistence is touched."""
    edge = (current, target)
    if edge not in _ALLOWED_EDGES:
        raise ValueError(f"mission transition is not allowed: {current} -> {target}")
    return edge  # type: ignore[return-value]
