"""ADK function tools for Waterline.

Every tool reads the resolved route from SESSION STATE (written once by
resolve_route), never from model-supplied coordinates — so the model can trigger
and narrate the geometry but can never place a wrong point on the map. Each tool
does its spatial work in PostGIS off the event loop, emits the resulting geometry
to the map, and returns only a compact scalar summary to the model.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext

from .. import db
from ..emit import emit_layer, emit_step, emit_panel
from ..metar import infer_station_less, load_airport_index, stations_from_metar_dump
from ..navcanada import fetch_metars, fetch_notams
from ..config import DEFAULT_CORRIDOR_NM, DEFAULT_CRUISE_FL_LOWER
from ..embed import embed_destination
from ..gemma_ranker import rank_notams


_REFERENCE = Path(__file__).resolve().parents[3] / "data" / "reference"
_AIRPORTS = load_airport_index(_REFERENCE / "airports_ca.csv")
MIN_MEMORY_SURFACED = 23
GEMINI_NOTAM_BUDGET = 14


def _fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _route(tool_context: ToolContext) -> dict[str, Any]:
    r = tool_context.state.get("route")
    if not r:
        raise ValueError("no route in session state — RouteAgent must resolve it first")
    return r


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text


def _serializable_notam(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "start_valid": _iso(item.get("start_valid")),
            "end_valid": _iso(item.get("end_valid"))}


def _hazard(item: dict[str, Any], idx: int) -> dict[str, Any]:
    return {
        "idx": idx,
        "location": item.get("location"),
        "qcode": item.get("qcode"),
        "dist_nm": round(float(item.get("dist_nm", 0)), 1),
        "line": (item.get("raw", "").splitlines()[-1] if item.get("raw") else "")[:120],
    }


def _notam_feature(item: dict[str, Any], idx: int, *, changed: bool = False) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "idx": idx, "location": item.get("location"), "qcode": item.get("qcode"),
            "radius_nm": item.get("radius_nm"),
            "dist_nm": round(float(item.get("dist_nm", 0)), 1),
            "fir_wide": item.get("fir_wide"), "raw": item.get("raw"),
            "memory_changed": changed,
        },
        "geometry": {"type": "Point", "coordinates": [item["lon"], item["lat"]]},
    }


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
    indexed = [{**_serializable_notam(item), "idx": index}
               for index, item in enumerate(out["on_route"])]
    notam_features = [_notam_feature(item, item["idx"]) for item in indexed]
    emit_layer("notams", f"{out['kept']} NOTAMs on route", _fc(notam_features), out["kept"])
    reduction = round(100 * out["dropped"] / max(out["total"], 1), 1)
    emit_step("CorridorAgent", "filtered",
              f"{out['total']} → {out['kept']} on-route ({reduction}% of the FIR dropped).")

    hazards = [_hazard(item, item["idx"]) for item in indexed]
    summary = {"total": out["total"], "kept": out["kept"], "dropped": out["dropped"],
               "reduction_pct": reduction, "hazards": hazards}
    tool_context.state["_route_notams"] = indexed
    tool_context.state["corridor"] = summary
    return summary


async def recall_destination_memory(tool_context: ToolContext) -> dict[str, Any]:
    """Suppress only exact previously acknowledged NOTAMs for this owner/destination.

    Embedding failure disables recall and surfaces the complete route set. Even
    with a full exact match, the nearest 23 hazards remain visible as a safety
    floor. Gemma can reorder that visible set, but every candidate remains in
    state and on the raw-source map layer.
    """
    route = _route(tool_context)
    owner_ref = tool_context.state.get("mission_owner_ref")
    rows = tool_context.state.get("_route_notams")
    if not isinstance(owner_ref, str) or not owner_ref:
        raise ValueError("owner-bound mission state is required for memory recall")
    if not isinstance(rows, list):
        raise ValueError("corridor rows are unavailable for memory recall")

    emit_step("RecallAgent", "recall", f"Checking prior briefings for {route['dst_name']}…")
    acknowledgements: list[dict[str, Any]] = []
    embedding_failed = False
    try:
        embedding = await asyncio.to_thread(
            embed_destination, route["dst_name"], task_type="RETRIEVAL_QUERY",
        )
        acknowledgements = await asyncio.to_thread(
            db.recall_notam_acknowledgements, owner_ref, embedding,
        )
    except Exception:
        embedding_failed = True
        emit_step(
            "RecallAgent", "degraded",
            "Destination embedding unavailable; memory disabled and every NOTAM surfaced.",
        )

    by_pk: dict[str, list[dict[str, Any]]] = {}
    for ack in acknowledgements:
        if isinstance(ack.get("notam_pk"), str):
            by_pk.setdefault(ack["notam_pk"], []).append(ack)

    exact: list[dict[str, Any]] = []
    mandatory: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for item in rows:
        raw = item.get("raw")
        pk = item.get("pk")
        digest = hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else ""
        prior = by_pk.get(pk, []) if isinstance(pk, str) else []
        match = any(
            ack.get("raw_sha256") == digest
            and _iso(ack.get("end_valid")) == _iso(item.get("end_valid"))
            for ack in prior
        )
        if match:
            exact.append(item)
        else:
            mandatory.append(item)
            if prior:
                changed.append({
                    "idx": item["idx"], "pk": pk,
                    "reason": "digest_or_end_valid_mismatch",
                })

    keep_exact = max(0, MIN_MEMORY_SURFACED - len(mandatory))
    surfaced_ids = {item["idx"] for item in mandatory}
    surfaced_ids.update(item["idx"] for item in exact[:keep_exact])
    surfaced_rows = [item for item in rows if item["idx"] in surfaced_ids]
    if embedding_failed or not acknowledgements:
        surfaced_rows = list(rows)
    suppressed = len(rows) - len(surfaced_rows)

    visible_hazards = [_hazard(item, item["idx"]) for item in surfaced_rows]
    try:
        ranking = await asyncio.to_thread(rank_notams, visible_hazards)
        ranked = ranking.ordered
        ranking_mode = ranking.mode
        ranking_model = ranking.model_id
    except Exception:
        ranked = visible_hazards
        ranking_mode = "degraded_fallback"
        ranking_model = "google/gemma-4-26b-a4b-it-maas"
        emit_step(
            "RecallAgent", "ranker_degraded",
            "Gemma advisory ranking unavailable; deterministic order retained.",
        )

    # A changed source is a deterministic override: it resurfaces at full
    # weight even if the advisory model would have demoted it.
    changed_ids = {change["idx"] for change in changed}
    ranked = (
        [hazard for hazard in visible_hazards if hazard["idx"] in changed_ids]
        + [hazard for hazard in ranked if hazard["idx"] not in changed_ids]
    )

    gemini_hazards = ranked[:GEMINI_NOTAM_BUDGET]
    corridor = dict(tool_context.state.get("corridor") or {})
    corridor.update({
        "geometry_kept": len(rows),
        "kept": len(surfaced_rows),
        "memory_suppressed": suppressed,
        "hazards": gemini_hazards,
        "gemini_reads": len(gemini_hazards),
        "gemma_triaged": max(0, len(ranked) - len(gemini_hazards)),
        "ranking_mode": ranking_mode,
        "ranking_model": ranking_model,
    })
    tool_context.state["corridor"] = corridor
    tool_context.state["memory_recall"] = {
        "on_route": len(rows), "suppressed": suppressed,
        "surfaced": len(surfaced_rows), "changed": changed,
    }

    emit_layer(
        "notams", f"{len(surfaced_rows)} NOTAMs after owner memory",
        _fc([
            _notam_feature(
                item, item["idx"],
                changed=any(change["idx"] == item["idx"] for change in changed),
            )
            for item in surfaced_rows
        ]),
        len(surfaced_rows),
    )
    emit_step(
        "RecallAgent", "reduced",
        f"{len(rows)} → {len(surfaced_rows)} after owner-scoped memory "
        f"({suppressed} unchanged acknowledgements suppressed).",
    )
    emit_step(
        "RecallAgent", "routed",
        (
            f"Gemini reads {len(gemini_hazards)} of {len(surfaced_rows)}; "
            "Gemma triaged the rest without dropping the raw layer."
            if ranking_mode == "vertex"
            else f"Gemini reads {len(gemini_hazards)} of {len(surfaced_rows)}; "
            "Gemma unavailable, deterministic order retained."
        ),
    )
    for change in changed:
        emit_step(
            "RecallAgent", "changed",
            f"{change['pk']} resurfaced at full weight: source digest or validity changed.",
        )
    return {
        "on_route": len(rows),
        "suppressed": suppressed,
        "surfaced": len(surfaced_rows),
        "changed": changed,
        "notes": [],
    }


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
