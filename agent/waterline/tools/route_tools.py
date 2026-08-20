"""Route resolution. Turns a pilot's plain request into coordinates.

Departures are resolved from the OurAirports identifier index (real ICAO codes).
Destinations are the whole point of Waterline: float bases with NO identifier, so
they can't be looked up in any aviation database. We resolve them from a small
gazetteer of named waterbodies (a stand-in for the live Canadian Geographical
Names DB) — precisely because no station/identifier exists for them.

The resolved route is written into session state so every downstream agent reads
one authoritative copy; coordinates are never threaded back through the model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext

from ..metar import load_airport_index
from ..emit import emit_step, emit_panel

# route_tools.py lives in waterline/tools/, so the project root is parents[3].
_REFERENCE = Path(__file__).resolve().parents[3] / "data" / "reference"
_AIRPORTS = load_airport_index(_REFERENCE / "airports_ca.csv")

# Named station-less float destinations (lon, lat). No identifier, no station —
# which is exactly why every identifier-keyed briefing tool goes blank here.
_LAKES: dict[str, dict[str, Any]] = {
    "lady evelyn lake": {"name": "Lady Evelyn Lake", "lon": -80.30, "lat": 47.35},
    "temagami": {"name": "Lake Temagami", "lon": -80.07, "lat": 46.95},
    "biscotasi lake": {"name": "Biscotasi Lake", "lon": -81.80, "lat": 47.30},
    "wabikon lake": {"name": "Wabikon Lake", "lon": -80.55, "lat": 47.10},
    "smoothwater lake": {"name": "Smoothwater Lake", "lon": -80.55, "lat": 47.47},
}


def _find_lake(name: str) -> dict[str, Any] | None:
    key = name.strip().lower()
    if key in _LAKES:
        return _LAKES[key]
    for k, v in _LAKES.items():
        if key in k or k in key:
            return v
    return None


def resolve_route(departure: str, destination: str, cruise_alt_ft: int,
                  tool_context: ToolContext) -> dict[str, Any]:
    """Resolve a departure identifier and a station-less destination to coordinates.

    Args:
        departure: departure aerodrome identifier (ICAO), e.g. "CYYZ".
        destination: destination waterbody name (has no identifier), e.g. "Lady Evelyn Lake".
        cruise_alt_ft: planned cruise altitude in feet.
    Returns the resolved route, and writes it to session state under "route".
    """
    dep = _AIRPORTS.get(departure.strip().upper())
    lake = _find_lake(destination)
    if not dep:
        return {"error": f"unknown departure identifier '{departure}'"}
    if not lake:
        return {"error": f"no waterbody matching '{destination}' in the gazetteer",
                "known": list(_LAKES)}
    route = {
        "dep_id": departure.strip().upper(),
        "dep_lat": dep["lat"], "dep_lon": dep["lon"], "dep_name": dep["name"],
        "dst_name": lake["name"], "dst_lat": lake["lat"], "dst_lon": lake["lon"],
        "cruise_fl_upper": max(5, round(cruise_alt_ft / 100)),  # ft -> flight level
        "fir_site": "CZYZ",  # Toronto FIR (MVP: single FIR)
    }
    tool_context.state["route"] = route
    emit_step("RouteAgent", "resolved",
              f"{route['dep_id']} → {route['dst_name']} "
              f"(no identifier; resolved from gazetteer, not an aviation DB).")
    emit_panel("route", route)
    return route
