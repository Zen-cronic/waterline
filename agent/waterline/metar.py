"""METAR fetch, light decode, and the station-less inference.

The Twist lives here: a destination lake with no identifier has no weather
station, so no briefing tool keyed to identifiers can say anything about it.
Waterline never *invents* a value. It ranks the real observations that DO exist
nearby, reports the nearest as the primary read, measures how much the nearby
stations agree, and states a confidence that falls with distance and rises with
agreement. Every source station and its untouched raw METAR travel with the
answer, so a pilot always sees exactly what the inference was built from.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from math import exp
from pathlib import Path
from typing import Any, Optional

_ICAO = re.compile(r"\b(C[A-Z]{3})\b")
_WIND = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b")
_VIS_SM = re.compile(r"\b(\d{1,2})(?:\s*(\d/\d))?SM\b")
_SKY = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})\b")
_TEMP = re.compile(r"\b(M?\d{2})/(M?\d{2})\b")
_ALT = re.compile(r"\bA(\d{4})\b")


def _icao(text: str) -> Optional[str]:
    m = _ICAO.search(text)
    return m.group(1) if m else None


def decode_metar(text: str) -> dict[str, Any]:
    """Light decode — enough for a float-flight briefing, not a full parser."""
    out: dict[str, Any] = {}
    if w := _WIND.search(text):
        out["wind_dir"] = None if w.group(1) == "VRB" else int(w.group(1))
        out["wind_kt"] = int(w.group(2))
        out["gust_kt"] = int(w.group(3)) if w.group(3) else None
    if v := _VIS_SM.search(text):
        out["vis_sm"] = int(v.group(1)) + (eval(v.group(2)) if v.group(2) else 0)
    ceil = None
    for cover, hh in _SKY.findall(text):
        if cover in ("BKN", "OVC"):
            ft = int(hh) * 100
            ceil = ft if ceil is None else min(ceil, ft)
    out["ceiling_ft"] = ceil
    if t := _TEMP.search(text):
        conv = lambda s: -int(s[1:]) if s.startswith("M") else int(s)
        out["temp_c"], out["dew_c"] = conv(t.group(1)), conv(t.group(2))
    if a := _ALT.search(text):
        out["altimeter_inhg"] = int(a.group(1)) / 100
    return out


@dataclass
class Station:
    station_id: str
    name: str
    lon: float
    lat: float
    elevation_m: float
    metar_raw: str
    observed_at: Optional[str]


def load_airport_index(csv_path: str | Path) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for row in csv.DictReader(open(csv_path, encoding="utf-8")):
        try:
            idx[row["ident"]] = {
                "name": row["name"],
                "lon": float(row["longitude_deg"]),
                "lat": float(row["latitude_deg"]),
                "elev_m": (float(row["elevation_ft"]) * 0.3048) if row["elevation_ft"] else None,
            }
        except (ValueError, KeyError):
            continue
    return idx


def stations_from_metar_dump(payload: dict[str, Any], airport_idx: dict[str, dict[str, Any]]) -> list[Station]:
    """Turn a FIR METAR dump into located stations by joining ICAO -> coordinates."""
    out: list[Station] = []
    seen: set[str] = set()
    for rec in payload.get("data", []):
        raw = rec.get("text")
        if not isinstance(raw, str):
            continue
        icao = _icao(raw)
        if not icao or icao in seen or icao not in airport_idx:
            continue
        seen.add(icao)
        a = airport_idx[icao]
        out.append(Station(icao, a["name"], a["lon"], a["lat"], a["elev_m"] or 0.0,
                           raw.strip(), rec.get("startValidity")))
    return out


def infer_station_less(dest: dict[str, Any], stations: list[dict[str, Any]]) -> dict[str, Any]:
    """Synthesize a briefing for a destination that has no station of its own.

    `stations` is the distance-ranked nearest-K (each with dist_nm + metar_raw).
    Confidence model, stated openly on screen:
      reach  = nearest station distance (NM) — how far we're extrapolating
      spread = disagreement among the nearby stations (ceiling / wind)
      conf   = exp(-reach/60) * exp(-spread_penalty)   in [0,1]
    Nothing is suppressed; the nearest read is primary and every source travels
    with it.
    """
    if not stations:
        return {"available": False, "reason": "no reporting station within range"}
    decoded = []
    for s in stations:
        d = decode_metar(s.get("metar_raw", ""))
        d.update({"station_id": s["station_id"], "dist_nm": round(s["dist_nm"], 1),
                  "metar_raw": s.get("metar_raw")})
        decoded.append(d)

    reach = decoded[0]["dist_nm"]
    ceilings = [d["ceiling_ft"] for d in decoded if d.get("ceiling_ft") is not None]
    winds = [d["wind_kt"] for d in decoded if d.get("wind_kt") is not None]
    ceil_spread = (max(ceilings) - min(ceilings)) if len(ceilings) >= 2 else 0
    wind_spread = (max(winds) - min(winds)) if len(winds) >= 2 else 0
    spread_penalty = ceil_spread / 4000 + wind_spread / 20
    conf = round(exp(-reach / 60.0) * exp(-spread_penalty), 2)

    primary = decoded[0]
    return {
        "available": True,
        "destination": dest,
        "reach_nm": reach,
        "confidence": conf,
        "confidence_note": f"nearest station {reach} NM away; "
                           f"{len(decoded)} stations within range; "
                           f"ceiling spread {ceil_spread} ft, wind spread {wind_spread} kt",
        "inferred": {
            "wind_dir": primary.get("wind_dir"), "wind_kt": primary.get("wind_kt"),
            "gust_kt": primary.get("gust_kt"), "vis_sm": primary.get("vis_sm"),
            "ceiling_ft": primary.get("ceiling_ft"),
            "temp_c": primary.get("temp_c"), "dew_c": primary.get("dew_c"),
        },
        "sources": decoded,  # every station + its untouched raw METAR
    }
