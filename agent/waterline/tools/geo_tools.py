"""ADK function tools for Waterline.

Every tool reads the resolved route from SESSION STATE (written once by
resolve_route), never from model-supplied coordinates — so the model can trigger
and narrate the geometry but can never place a wrong point on the map. Each tool
does its spatial work in PostGIS off the event loop, emits the resulting geometry
to the map, and returns only a compact scalar summary to the model.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext

from .. import db
from ..emit import emit_layer, emit_step, emit_panel
from ..metar import infer_station_less, load_airport_index, stations_from_metar_dump
from ..navcanada import fetch_metars, fetch_notams
from ..config import DEFAULT_CORRIDOR_NM, DEFAULT_CRUISE_FL_LOWER


_REFERENCE = Path(__file__).resolve().parents[3] / "data" / "reference"
_AIRPORTS = load_airport_index(_REFERENCE / "airports_ca.csv")


def _fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _route(tool_context: ToolContext) -> dict[str, Any]:
    r = tool_context.state.get("route")
    if not r:
        raise ValueError("no route in session state — RouteAgent must resolve it first")
    return r


async def fetch_and_load_sources(tool_context: ToolContext) -> dict[str, Any]:
    """Fetch and load current NOTAM and METAR inputs for the route's FIR.

    Reads the FIR site from the resolved route in session state. Returns a
    structured provenance receipt for both products. Local developer captures
    are labelled explicitly and never enter the deployed image.
    """
    fir_site = _route(tool_context)["fir_site"]
    emit_step("IngestAgent", "fetch", f"Pulling current NOTAM and METAR inputs for {fir_site}…")
    (notam_payload, notam_source), (metar_payload, metar_source) = await asyncio.gather(
        asyncio.to_thread(fetch_notams, fir_site),
        asyncio.to_thread(fetch_metars, fir_site),
    )
    stations = stations_from_metar_dump(metar_payload, _AIRPORTS)
    notam_res = await asyncio.to_thread(
        db.load_notams, fir_site, notam_payload, notam_source,
    )
    metar_res = await asyncio.to_thread(
        db.load_stations, fir_site, stations, len(metar_payload.get("data", [])), metar_source,
    )
    provenance = {
        "site": fir_site,
        "notam": {**notam_source, **notam_res},
        "metar": {**metar_source, **metar_res},
    }
    emit_panel("provenance", provenance)
    emit_step("IngestAgent", "loaded",
              f"NOTAM {notam_res['parsed']}/{notam_res['records']} parsed; "
              f"METAR {metar_res['parsed']}/{metar_res['records']} stations located.")
    tool_context.state["ingest"] = provenance
    return provenance


async def filter_route_corridor(tool_context: ToolContext,
                                corridor_nm: float = DEFAULT_CORRIDOR_NM) -> dict[str, Any]:
    """Reduce the whole-FIR NOTAM set to only those that touch this route.

    Draws the route, the buffered corridor, and the surviving NOTAMs on the map.
    Args:
        corridor_nm: half-width of the route corridor in nautical miles (default 10).
    Returns {total, kept, dropped, reduction_pct, hazards:[{idx,location,qcode,dist_nm,line}]}.
    `idx` is the stable handle the model must use when referring to a NOTAM.
    """
    r = _route(tool_context)
    dep, dst = (r["dep_lon"], r["dep_lat"]), (r["dst_lon"], r["dst_lat"])
    out = await asyncio.to_thread(
        db.corridor_filter, dep, dst, corridor_nm, DEFAULT_CRUISE_FL_LOWER, r["cruise_fl_upper"])
    geom = await asyncio.to_thread(db.route_geometry, dep, dst, corridor_nm)

    emit_layer("corridor", f"route corridor ±{corridor_nm:g} NM",
               _fc([{"type": "Feature", "properties": {}, "geometry": geom["corridor"]}]), 1)
    emit_layer("route", f"{r['dep_id']} → {r['dst_name']}",
               _fc([{"type": "Feature", "properties": {"dst": r["dst_name"]}, "geometry": geom["route"]}]), 1)
    notam_features = [
        {"type": "Feature",
         "properties": {"idx": i, "location": n["location"], "qcode": n["qcode"],
                        "radius_nm": n["radius_nm"], "dist_nm": round(n["dist_nm"], 1),
                        "fir_wide": n["fir_wide"], "raw": n["raw"]},
         "geometry": {"type": "Point", "coordinates": [n["lon"], n["lat"]]}}
        for i, n in enumerate(out["on_route"])
    ]
    emit_layer("notams", f"{out['kept']} NOTAMs on route", _fc(notam_features), out["kept"])
    reduction = round(100 * out["dropped"] / max(out["total"], 1), 1)
    emit_step("CorridorAgent", "filtered",
              f"{out['total']} → {out['kept']} on-route ({reduction}% of the FIR dropped).")

    hazards = [
        {"idx": i, "location": n["location"], "qcode": n["qcode"],
         "dist_nm": round(n["dist_nm"], 1),
         "line": (n["raw"].splitlines()[-1] if n["raw"] else "")[:120]}
        for i, n in enumerate(out["on_route"])
    ]
    summary = {"total": out["total"], "kept": out["kept"], "dropped": out["dropped"],
               "reduction_pct": reduction, "hazards": hazards}
    tool_context.state["corridor"] = summary
    return summary


async def infer_destination_weather(tool_context: ToolContext) -> dict[str, Any]:
    """Infer a weather read for the route's destination, which has no station of its own.

    Ranks the nearest real METAR stations, reports the nearest as the primary
    read, and states a confidence that falls with distance and rises with
    agreement. Draws the source stations on the map. Never invents a value.
    Returns the inference {available, reach_nm, confidence, confidence_note, inferred, sources}.
    """
    r = _route(tool_context)
    near = await asyncio.to_thread(db.nearest_stations, r["dst_lon"], r["dst_lat"], 5)
    inf = infer_station_less({"name": r["dst_name"], "lon": r["dst_lon"], "lat": r["dst_lat"]}, near)

    station_features = []
    for s in near:
        lon, lat = await asyncio.to_thread(_station_lonlat, s["station_id"])
        station_features.append({
            "type": "Feature",
            "properties": {"station_id": s["station_id"], "name": s["name"],
                           "dist_nm": round(s["dist_nm"], 1), "metar_raw": s["metar_raw"]},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    emit_layer("stations", f"{len(near)} source stations", _fc(station_features), len(near))
    emit_panel("inference", inf)
    if inf.get("available"):
        emit_step("WeatherAgent", "inferred",
                  f"No station at {r['dst_name']}; nearest is {inf['reach_nm']} NM. "
                  f"Confidence {inf['confidence']}.")
    tool_context.state["weather"] = inf
    return inf


def _station_lonlat(station_id: str) -> tuple[float, float]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ST_X(point::geometry) AS lon, ST_Y(point::geometry) AS lat "
                    "FROM stations WHERE station_id=%s", (station_id,))
        row = cur.fetchone()
        return (row["lon"], row["lat"]) if row else (0.0, 0.0)
