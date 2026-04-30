#!/usr/bin/env python3
"""Import Traktor analysis data from collection.nml into music.db.

Populates per-track columns (no overwrite of existing bpm/genre):
  - traktor_bpm           float BPM from <TEMPO>
  - traktor_key           integer 0-23 from <MUSICAL_KEY>
  - traktor_beatgrid_ms   float ms from <CUE_V2 TYPE="4"> (AutoGrid anchor)
  - traktor_imported_at   ISO timestamp

Also rebuilds `traktor_cues` (one row per non-grid CUE_V2 — i.e. user-placed
hotcues, fade points, loops). Existing rows for a matched track are deleted
and replaced on each run, so re-running picks up edits made in Traktor.

Matches NML entries to music.db rows by absolute file path.

Usage:
  uv run scripts/import_traktor.py [--nml path] [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "music.db"
DEFAULT_NML = REPO / "collection.nml"

COLUMNS = {
    "traktor_bpm": "REAL",
    "traktor_key": "INTEGER",
    "traktor_beatgrid_ms": "REAL",
    "traktor_imported_at": "TEXT",
}


CUE_TYPE_LABELS = {
    0: "cue",
    1: "fade_in",
    2: "fade_out",
    3: "load",
    4: "grid",
    5: "loop",
}


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    for name, decl in COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {decl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS traktor_cues (
            id        INTEGER PRIMARY KEY,
            track_id  INTEGER NOT NULL,
            hotcue    INTEGER,
            type      INTEGER NOT NULL,
            type_label TEXT,
            name      TEXT,
            start_ms  REAL NOT NULL,
            len_ms    REAL,
            FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_traktor_cues_track_id "
        "ON traktor_cues (track_id)"
    )
    conn.commit()


def reconstruct_path(loc: ET.Element) -> str | None:
    """Traktor LOCATION: DIR uses '/:' as separator, FILE is basename."""
    d = loc.get("DIR")
    f = loc.get("FILE")
    if not d or not f:
        return None
    return d.replace("/:", "/") + f


def parse_entry(entry: ET.Element) -> dict | None:
    loc = entry.find("LOCATION")
    if loc is None:
        return None
    path = reconstruct_path(loc)
    if not path:
        return None
    tempo = entry.find("TEMPO")
    key = entry.find("MUSICAL_KEY")
    grid_ms = None
    cues: list[dict] = []
    for cue in entry.findall("CUE_V2"):
        try:
            t = int(cue.get("TYPE", ""))
            start = float(cue.get("START", ""))
        except ValueError:
            continue
        if t == 4 and grid_ms is None:
            grid_ms = start
            continue
        try:
            length = float(cue.get("LEN", "") or 0.0)
        except ValueError:
            length = 0.0
        try:
            hot = int(cue.get("HOTCUE", ""))
        except ValueError:
            hot = None
        cues.append({
            "hotcue": hot,
            "type": t,
            "type_label": CUE_TYPE_LABELS.get(t),
            "name": cue.get("NAME"),
            "start_ms": start,
            "len_ms": length,
        })
    return {
        "path": path,
        "bpm": float(tempo.get("BPM")) if tempo is not None and tempo.get("BPM") else None,
        "key": int(key.get("VALUE")) if key is not None and key.get("VALUE") else None,
        "beatgrid_ms": grid_ms,
        "cues": cues,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nml", type=Path, default=DEFAULT_NML, help="Path to collection.nml")
    ap.add_argument("--dry-run", action="store_true", help="Parse and report; do not write")
    ap.add_argument("--limit", type=int, help="Process only the first N entries")
    args = ap.parse_args()

    if not args.nml.exists():
        raise SystemExit(f"NML not found: {args.nml}")
    if not DB_PATH.exists():
        raise SystemExit(f"music.db not found: {DB_PATH}")

    print(f"Parsing {args.nml} ...", flush=True)
    tree = ET.parse(args.nml)
    entries = tree.getroot().findall(".//ENTRY")
    if args.limit:
        entries = entries[: args.limit]
    print(f"  {len(entries)} entries")

    parsed: list[dict] = []
    skipped = 0
    for e in entries:
        row = parse_entry(e)
        if row is None or row["bpm"] is None:
            skipped += 1
            continue
        parsed.append(row)
    print(f"  {len(parsed)} parseable, {skipped} skipped (no path or no BPM)")

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_columns(conn)
        db_paths = {r[0]: r[1] for r in conn.execute("SELECT path, id FROM tracks")}

        matched: list[tuple[int, dict]] = []
        unmatched: list[str] = []
        for row in parsed:
            tid = db_paths.get(row["path"])
            if tid is None:
                unmatched.append(row["path"])
            else:
                matched.append((tid, row))

        print(f"  {len(matched)} matched to music.db, {len(unmatched)} unmatched")
        if unmatched[:3]:
            print("  sample unmatched:")
            for p in unmatched[:3]:
                print(f"    {p}")

        if args.dry_run:
            print("\n[dry-run] no writes")
            return

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany(
            """UPDATE tracks
               SET traktor_bpm = ?, traktor_key = ?, traktor_beatgrid_ms = ?,
                   traktor_imported_at = ?
               WHERE id = ?""",
            [
                (r["bpm"], r["key"], r["beatgrid_ms"], now, tid)
                for tid, r in matched
            ],
        )

        # Rebuild cues for matched tracks: delete-then-insert keeps re-runs idempotent
        matched_ids = [tid for tid, _ in matched]
        conn.executemany(
            "DELETE FROM traktor_cues WHERE track_id = ?",
            [(tid,) for tid in matched_ids],
        )
        cue_rows = [
            (tid, c["hotcue"], c["type"], c["type_label"],
             c["name"], c["start_ms"], c["len_ms"])
            for tid, r in matched for c in r["cues"]
        ]
        conn.executemany(
            """INSERT INTO traktor_cues
               (track_id, hotcue, type, type_label, name, start_ms, len_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            cue_rows,
        )
        conn.commit()
        print(f"\nUpdated {len(matched)} rows, inserted {len(cue_rows)} cues at {now}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
