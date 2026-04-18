#!/usr/bin/env python3
"""Apply looked-up BPM values to music.db.

Adds a `bpm_source` column on first run and populates rows by
(artist, title) match. Never overwrites rows already marked
`manual`. Safe to re-run — UPDATE is idempotent.

Input TSV columns: artist, title, strict_bp, gsb, chosen, source

Usage:
  uv run scripts/apply_bpm.py --db music.db --input tmp/run_bpm_160_189.tsv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

SOURCE_MAP = {
    "beatport": "lookup_beatport",
    "getsongbpm": "lookup_getsongbpm",
}


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tracks)")]
    if "bpm_source" not in cols:
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm_source TEXT")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_column(conn)

    updated_rows = 0
    tracks_affected = 0
    skipped_manual = 0
    no_match = 0

    with args.input.open() as f:
        reader = csv.DictReader(f, delimiter="\t",
                                fieldnames=["artist", "title", "strict_bp", "gsb", "chosen", "source"])
        for row in reader:
            if row["artist"] == "artist":
                continue
            if not row["chosen"]:
                continue
            source_db = SOURCE_MAP.get(row["source"])
            if source_db is None:
                continue
            bpm = float(row["chosen"])

            # Count targets
            targets = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE artist=? AND title=? "
                "AND (bpm_source IS NULL OR bpm_source != 'manual')",
                (row["artist"], row["title"]),
            ).fetchone()[0]
            manual = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE artist=? AND title=? AND bpm_source='manual'",
                (row["artist"], row["title"]),
            ).fetchone()[0]
            if targets == 0 and manual == 0:
                no_match += 1
                continue
            skipped_manual += manual

            if not args.dry_run and targets > 0:
                cur = conn.execute(
                    "UPDATE tracks SET bpm=?, bpm_source=? "
                    "WHERE artist=? AND title=? "
                    "AND (bpm_source IS NULL OR bpm_source != 'manual')",
                    (bpm, source_db, row["artist"], row["title"]),
                )
                tracks_affected += cur.rowcount
            else:
                tracks_affected += targets
            updated_rows += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"input rows processed: {updated_rows}")
    print(f"db rows affected:     {tracks_affected}")
    print(f"rows skipped (manual): {skipped_manual}")
    print(f"input rows with no db match: {no_match}")
    if args.dry_run:
        print("(dry run — no changes committed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
