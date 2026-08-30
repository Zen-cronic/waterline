#!/usr/bin/env python3
"""Create one real, recoverable NOTAM digest mismatch for the repeat-flight demo.

Start this command immediately before clicking "Brief this flight" for flight 2.
It waits until that run commits a new NOTAM ingest, then mutates the nearest
previously acknowledged route NOTAM before CorridorAgent reads it. The next live
ingest restores the authoritative source automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
import time

import psycopg
from psycopg.rows import dict_row


DEPARTURE = (-79.629421, 43.675935)
DESTINATION = (-80.30, 47.35)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-ref", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(fetched_at) AS fetched_at FROM ingests WHERE product='notam'")
            before = cur.fetchone()["fetched_at"]
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT max(fetched_at) AS fetched_at FROM ingests WHERE product='notam'")
                current = cur.fetchone()["fetched_at"]
            if current is not None and (before is None or current > before):
                break
            time.sleep(0.05)
        else:
            raise SystemExit("timed out waiting for flight 2 NOTAM ingest")

        with conn.cursor() as cur:
            cur.execute(
                """
                WITH route AS (
                    SELECT ST_MakeLine(ST_MakePoint(%s,%s), ST_MakePoint(%s,%s))::geography AS line
                )
                SELECT n.pk, n.raw, n.end_valid, a.raw_sha256, a.end_valid AS ack_end_valid
                FROM notams n
                JOIN notam_acknowledgements a ON a.notam_pk = n.pk
                CROSS JOIN route
                WHERE a.owner_ref = %s
                ORDER BY ST_Distance(n.center, route.line), a.created_at DESC
                LIMIT 200
                """,
                (*DEPARTURE, *DESTINATION, args.owner_ref),
            )
            candidates = cur.fetchall()
            target = next(
                (
                    row for row in candidates
                    if hashlib.sha256(row["raw"].encode()).hexdigest() == row["raw_sha256"]
                    and row["end_valid"] == row["ack_end_valid"]
                ),
                None,
            )
            if target is None:
                raise SystemExit("no exact acknowledged NOTAM is available to mutate")
            marker = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            changed_raw = f"{target['raw']}\nWATERLINE SYNTHETIC CHANGE {marker}"
            cur.execute(
                """
                UPDATE notams SET raw=%s
                WHERE pk=%s AND raw=%s
                RETURNING pk
                """,
                (changed_raw, target["pk"], target["raw"]),
            )
            changed = cur.fetchone()
            if changed is None:
                conn.rollback()
                raise SystemExit("NOTAM changed concurrently; no demo mutation committed")
        conn.commit()
    print(
        f"mutated {target['pk']}: "
        f"{hashlib.sha256(target['raw'].encode()).hexdigest()[:12]} -> "
        f"{hashlib.sha256(changed_raw.encode()).hexdigest()[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
