#!/usr/bin/env python3
# /// script
# dependencies = ["mutagen>=1.47"]
# ///
"""Write BPM values from music.db back into audio file tags.

Only writes for rows where bpm_source is a lookup (never for manual
without explicit opt-in, to keep the DB as source of truth there too).
Uses mutagen for in-place tag updates — no re-encoding.

Usage:
  uv run --script scripts/write_bpm_tags.py --db music.db --dry-run
  uv run --script scripts/write_bpm_tags.py --db music.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

EXT_HANDLERS = {
    ".mp3":  ("id3",  lambda p: MP3(p, ID3=EasyID3)),
    ".m4a":  ("m4a",  EasyMP4),
    ".aac":  ("m4a",  EasyMP4),
    ".flac": ("flac", FLAC),
    ".ogg":  ("ogg",  OggVorbis),
    ".opus": ("opus", OggOpus),
}


def write_bpm(path: Path, bpm: float) -> str:
    """Return one of: 'written', 'skipped_same', 'skipped_ext', 'error:<msg>'."""
    handler = EXT_HANDLERS.get(path.suffix.lower())
    if handler is None:
        return "skipped_ext"
    kind, loader = handler
    try:
        if kind == "id3":
            try:
                audio = loader(str(path))
            except ID3NoHeaderError:
                audio = MP3(str(path), ID3=EasyID3)
                audio.add_tags()
        else:
            audio = loader(str(path))
    except Exception as exc:
        return f"error:load:{exc}"

    # Use EasyID3/EasyMP4's 'bpm' key, which maps to TBPM / tmpo respectively.
    # FLAC / Ogg use a 'BPM' free-form tag.
    key = "bpm" if kind in ("id3", "m4a") else "bpm"
    existing = audio.get(key)
    new_val = f"{int(round(bpm))}"
    if existing and existing[0] == new_val:
        return "skipped_same"
    try:
        audio[key] = new_val
        audio.save()
    except Exception as exc:
        return f"error:save:{exc}"
    return "written"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, help="cap rows (for testing)")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    q = (
        "SELECT path, bpm, bpm_source FROM tracks "
        "WHERE bpm IS NOT NULL AND bpm_source IN ('lookup_beatport', 'lookup_getsongbpm')"
    )
    if args.limit:
        q += f" LIMIT {args.limit}"
    rows = conn.execute(q).fetchall()
    conn.close()
    print(f"candidates: {len(rows)}", file=sys.stderr)

    counts: dict[str, int] = {}
    for i, (path_s, bpm, source) in enumerate(rows, 1):
        path = Path(path_s)
        if not path.exists():
            status = "error:missing_file"
        elif args.dry_run:
            status = "dry_run"
        else:
            status = write_bpm(path, bpm)
        counts[status.split(":")[0]] = counts.get(status.split(":")[0], 0) + 1
        if status.startswith("error"):
            print(f"[{i}] {status} — {path}", file=sys.stderr)
        if i % 100 == 0 or i == len(rows):
            print(f"# [{i}/{len(rows)}] {counts}", file=sys.stderr, flush=True)

    print("summary:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
