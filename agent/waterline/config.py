"""Runtime configuration for Waterline."""
from __future__ import annotations

import os

APP_NAME = "waterline"

# Model fallback chain. 3.7-flash is preferred (newest, GA 2026-08-13) but has
# been intermittently 503-throttled under launch load; 3.5-flash is the reliable
# floor. Every model here clears the hackathon's "Gemini 3.5 or newer" bar, so
# whichever answers first is compliant — the demo never stalls on a capacity spike.
MODEL_CHAIN = [
    m.strip() for m in os.environ.get(
        "WATERLINE_MODEL_CHAIN", "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash"
    ).split(",") if m.strip()
]

# A small, fast model for the sub-agents that only classify/rank (keeps latency
# and cost down; Flash-lite is plenty for ranking already-filtered rows).
RANKER_MODEL_CHAIN = [
    m.strip() for m in os.environ.get(
        "WATERLINE_RANKER_CHAIN", "gemini-3.5-flash-lite,gemini-3.5-flash"
    ).split(",") if m.strip()
]

# A float plane rarely climbs above ~9,500 ft; the default altitude band.
DEFAULT_CRUISE_FL_LOWER = 0
DEFAULT_CRUISE_FL_UPPER = 95
DEFAULT_CORRIDOR_NM = 10.0

NAVCANADA_ALPHA = "https://plan.navcanada.ca/weather/api/alpha/"
