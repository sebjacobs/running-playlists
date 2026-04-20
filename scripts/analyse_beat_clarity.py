#!/usr/bin/env python3
"""Populate music.db with beat-clarity metrics for DnB-range tracks.

Metrics stored per track:
  - groove_full       cosine score vs full-step template (K on 1 + 3.5, S on 2 + 4)
  - groove_half       cosine score vs half-step template (K on 1, S on 3)
  - groove_delta      full - half
  - grid_tightness    fraction of kick-band onsets within ±30ms of 8th-note grid
  - kick_prominence   mean envelope peak at expected kick slots / median envelope
  - beat_analysed_at  ISO timestamp of last analysis

Actions based on metrics:
  - groove_delta < 0.05  ->  run_exclude=1, run_exclude_reason='half-step'
                             (only if the track isn't already manually excluded)
  - tightness < 0.55 AND prominence < 8  ->  beat_review_flag=1
                             (hint for manual triage; does NOT auto-exclude,
                              because this pattern overlaps liquid DnB you love)

Usage:
  uv run scripts/analyse_beat_clarity.py [--limit N] [--reanalyse] [--workers K]
                                          [--bpm-min 160] [--bpm-max 180]
                                          [--id ID ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Reuse the analysis core from the spike
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_beat_clarity import analyse  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "music.db"

COLUMNS = {
    "groove_full": "REAL",
    "groove_half": "REAL",
    "groove_delta": "REAL",
    "grid_tightness": "REAL",
    "kick_prominence": "REAL",
    "beat_review_flag": "INTEGER DEFAULT 0",
    "beat_analysed_at": "TEXT",
}

HALF_STEP_DELTA = 0.05
REVIEW_TIGHTNESS = 0.55
REVIEW_PROMINENCE = 8.0


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    for name, decl in COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {decl}")
    conn.commit()


def select_targets(
    conn: sqlite3.Connection,
    bpm_min: float,
    bpm_max: float,
    limit: int | None,
    reanalyse: bool,
    ids: list[int],
) -> list[tuple[int, str, float]]:
    if ids:
        q = "SELECT id, path, bpm FROM tracks WHERE id IN (%s)" % ",".join("?" * len(ids))
        return list(conn.execute(q, ids))
    where = "bpm BETWEEN ? AND ?"
    params: list = [bpm_min, bpm_max]
    if not reanalyse:
        where += " AND beat_analysed_at IS NULL"
    q = f"SELECT id, path, bpm FROM tracks WHERE {where} ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return list(conn.execute(q, params))


def _worker(task: tuple[int, str, float]) -> tuple[int, dict | None, str | None]:
    tid, path, bpm = task
    try:
        m = analyse(path, bpm)
        if "error" in m:
            return tid, None, m["error"]
        return tid, m, None
    except Exception as e:  # noqa: BLE001
        return tid, None, f"{type(e).__name__}: {e}"


def apply_result(
    conn: sqlite3.Connection, tid: int, m: dict, dry_run: bool
) -> tuple[bool, bool]:
    """Write metrics and apply auto-flags. Returns (excluded, review_flagged)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    review = int(
        m["grid_tightness"] < REVIEW_TIGHTNESS
        and m["kick_prominence"] < REVIEW_PROMINENCE
    )
    half_step = m["delta"] < HALF_STEP_DELTA

    if not dry_run:
        conn.execute(
            """UPDATE tracks
               SET groove_full=?, groove_half=?, groove_delta=?,
                   grid_tightness=?, kick_prominence=?,
                   beat_review_flag=?, beat_analysed_at=?
               WHERE id=?""",
            (
                m["full"], m["half"], m["delta"],
                m["grid_tightness"], m["kick_prominence"],
                review, now, tid,
            ),
        )

        if half_step:
            # Only apply auto-exclude if not already excluded for another reason
            row = conn.execute(
                "SELECT run_exclude, run_exclude_reason FROM tracks WHERE id=?",
                (tid,),
            ).fetchone()
            if row and (row[0] == 0 or row[0] is None):
                conn.execute(
                    "UPDATE tracks SET run_exclude=1, run_exclude_reason='half-step' WHERE id=?",
                    (tid,),
                )

    return half_step, bool(review)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--reanalyse", action="store_true")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--bpm-min", type=float, default=160.0)
    p.add_argument("--bpm-max", type=float, default=180.0)
    p.add_argument("--id", type=int, action="append", default=[])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH)
    ensure_columns(conn)

    targets = select_targets(
        conn, args.bpm_min, args.bpm_max, args.limit, args.reanalyse, args.id
    )
    if not targets:
        print("no tracks to analyse")
        return 0

    print(f"analysing {len(targets)} tracks with {args.workers} workers")
    start = time.time()

    done = 0
    errors = 0
    excluded = 0
    flagged = 0
    commit_every = 25

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_worker, t): t for t in targets}
        for fut in as_completed(futures):
            tid, m, err = fut.result()
            done += 1
            if err:
                errors += 1
                if errors <= 5:
                    print(f"  [err] id={tid}: {err}", file=sys.stderr)
                continue
            ex_, rev = apply_result(conn, tid, m, args.dry_run)
            if ex_:
                excluded += 1
            if rev:
                flagged += 1
            if done % commit_every == 0:
                if not args.dry_run:
                    conn.commit()
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                eta = (len(targets) - done) / rate if rate else 0
                print(
                    f"  {done}/{len(targets)}  excluded={excluded}  "
                    f"flagged={flagged}  errors={errors}  "
                    f"rate={rate:.1f}/s  eta={eta:.0f}s"
                )

    if not args.dry_run:
        conn.commit()
    conn.close()

    elapsed = time.time() - start
    print(
        f"done: {done} analysed in {elapsed:.0f}s  "
        f"(excluded={excluded} half-step, review_flagged={flagged}, errors={errors})"
        + ("  [dry run]" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
