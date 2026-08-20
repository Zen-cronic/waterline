"""Deterministic NOTAM ingest + Q-line geometry parser.

The parse is *not* the achievement — open parsers exist. What matters here is
that every FIR-wide NOTAM carries machine-readable geometry (a centre, a radius,
and a flight-level band), because that geometry is what the corridor filter runs
against. NAV CANADA's own API refuses geometry queries (`bbox=`, `radius=`,
`point=` all return `alpha.geomNone`); it answers only `site=`. So the spatial
reduction from "the whole Flight Information Region" down to "your route" is our
code, not theirs.

Everything in this module is pure and synchronous so it can be unit-tested
against a frozen live capture with no database and no network.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# Q-line: Q) FIR/QCODE/TRAFFIC/PURPOSE/SCOPE/LOWER/UPPER/COORD+RADIUS
# e.g.    Q) CZYZ/QMXLC/IV/BO/A/000/999/4341N07938W005
_QLINE = re.compile(r"Q\)\s*([^/]+)/([^/]+)/([^/]+)/([^/]+)/([^/]+)/(\d{3})/(\d{3})/(\S+)")

# COORD+RADIUS trailing token: DDMM[SS]{N|S}DDDMM[SS]{E|W}RRR  (radius in NM)
_COORD = re.compile(r"(\d{2})(\d{2})(\d{2})?([NS])(\d{3})(\d{2})(\d{2})?([EW])(\d{3})")

# A) affected aerodrome/FIR, B) start, C) end — inside the raw body.
_A = re.compile(r"\bA\)\s*([A-Z0-9 ]+?)\s+B\)")
_B = re.compile(r"\bB\)\s*(\d{10})")
_C = re.compile(r"\bC\)\s*(\d{10})(PERM|EST|)?", re.I)

FIR_WIDE_RADIUS_NM = 999  # the sentinel radius that means "entire FIR"


@dataclass
class Notam:
    pk: str
    location: str            # A) aerodrome, e.g. CYQG
    fir: str                 # Q-line FIR, e.g. CZYZ
    qcode: str               # e.g. QMXLC
    lat: float               # centre, decimal degrees (+N / -S)
    lon: float               # centre, decimal degrees (+E / -W)
    radius_nm: int           # Q-line radius in nautical miles
    fl_lower: int            # flight level, hundreds of feet (000 = surface)
    fl_upper: int            # flight level, hundreds of feet (999 = unlimited)
    start: Optional[str]     # ISO8601 (validity start)
    end: Optional[str]       # ISO8601 or None (permanent)
    fir_wide: bool           # radius == 999 NM → whole-FIR noise
    raw: str                 # the untouched NOTAM text (always shown beside the decode)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dms_to_deg(d: str, m: str, s: Optional[str], hemi: str) -> float:
    deg = int(d) + int(m) / 60 + (int(s) / 3600 if s else 0)
    return -deg if hemi in ("S", "W") else deg


def _notam_dt(token: str) -> Optional[str]:
    """B)/C) NOTAM date form YYMMDDHHMM (UTC) -> ISO8601. '9912312359' etc. tolerated."""
    try:
        dt = datetime.strptime(token, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def parse_qline(raw: str) -> Optional[dict[str, Any]]:
    """Return geometry {lat, lon, radius_nm, fl_lower, fl_upper, fir, qcode} or None."""
    m = _QLINE.search(raw)
    if not m:
        return None
    fir, qcode, _traffic, _purpose, _scope, lower, upper, coord = m.groups()
    cm = _COORD.search(coord)
    if not cm:
        return None
    la_d, la_m, la_s, ns, lo_d, lo_m, lo_s, ew, rad = cm.groups()
    return {
        "fir": fir.strip(),
        "qcode": qcode.strip(),
        "lat": round(_dms_to_deg(la_d, la_m, la_s, ns), 5),
        "lon": round(_dms_to_deg(lo_d, lo_m, lo_s, ew), 5),
        "radius_nm": int(rad),
        "fl_lower": int(lower),
        "fl_upper": int(upper),
    }


def parse_record(rec: dict[str, Any]) -> Optional[Notam]:
    """Parse one NAV CANADA `data[]` record into a Notam, or None if it has no Q-line geometry."""
    text = rec.get("text")
    if isinstance(text, str):
        try:
            text = json.loads(text)  # `text` is itself a JSON string
        except json.JSONDecodeError:
            text = {"raw": text}
    raw = (text or {}).get("raw") or ""
    if not raw:
        return None
    geo = parse_qline(raw)
    if not geo:
        return None

    a = _A.search(raw)
    location = a.group(1).strip() if a else (rec.get("location") or geo["fir"])

    # Prefer the API's parsed validity; fall back to B)/C) in the raw body.
    start = rec.get("startValidity")
    end = rec.get("endValidity")
    if not start:
        b = _B.search(raw)
        start = _notam_dt(b.group(1)) if b else None
    if not end:
        c = _C.search(raw)
        end = _notam_dt(c.group(1)) if c else None

    return Notam(
        pk=str(rec.get("pk") or rec.get("location") or geo["fir"]),
        location=location,
        fir=geo["fir"],
        qcode=geo["qcode"],
        lat=geo["lat"],
        lon=geo["lon"],
        radius_nm=geo["radius_nm"],
        fl_lower=geo["fl_lower"],
        fl_upper=geo["fl_upper"],
        start=start,
        end=end,
        fir_wide=(geo["radius_nm"] >= FIR_WIDE_RADIUS_NM),
        raw=raw,
    )


def parse_dump(payload: dict[str, Any] | list) -> list[Notam]:
    rows: Iterable = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[Notam] = []
    for rec in rows:
        n = parse_record(rec)
        if n:
            out.append(n)
    return out


if __name__ == "__main__":  # quick parse-rate check against a frozen capture
    import sys
    payload = json.load(open(sys.argv[1]))
    rows = payload.get("data", [])
    parsed = parse_dump(payload)
    fir_wide = sum(1 for n in parsed if n.fir_wide)
    print(f"records={len(rows)}  parsed_geometry={len(parsed)}  "
          f"parse_rate={len(parsed)/max(len(rows),1):.1%}  fir_wide(999NM)={fir_wide}")
    for n in parsed[:3]:
        print(f"  {n.location:8s} {n.qcode:7s} "
              f"({n.lat:.3f},{n.lon:.3f}) r={n.radius_nm}NM FL{n.fl_lower:03d}-{n.fl_upper:03d} "
              f"{'FIR-WIDE' if n.fir_wide else ''}")
