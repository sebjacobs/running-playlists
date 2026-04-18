#!/usr/bin/env python3
"""Run aubio tempo over a filtered slice of music.db for BPM comparison.

Outputs TSV: artist<TAB>title<TAB>known_bpm<TAB>aubio_bpm<TAB>path

Usage:
  uv run scripts/probe_aubio.py --db music.db --genre 'D&B' --max-duration 600 \
    > tmp/probe_aubio_dnb.tsv
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def aubio_bpm(path: str) -> float | None:
    try:
        result = subprocess.run(
            ["aubio", "tempo", path],
            capture_output=True, text=True, timeout=60, check=True,
        )
        out = result.stdout.strip().split()
        return float(out[0]) if out else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def sample(db: Path, artists, genre, min_duration, max_duration):
    conn = sqlite3.connect(db)
    where = ["path IS NOT NULL"]
    params: list = []
    if artists:
        where.append(f"artist IN ({','.join('?' * len(artists))})")
        params.extend(artists)
    if genre:
        where.append("genre = ?")
        params.append(genre)
    if min_duration is not None:
        where.append("duration_s >= ?")
        params.append(min_duration)
    if max_duration is not None:
        where.append("duration_s < ?")
        params.append(max_duration)
    rows = conn.execute(
        f"SELECT artist, title, bpm, path FROM tracks WHERE {' AND '.join(where)} "
        f"ORDER BY artist, title",
        params,
    ).fetchall()
    conn.close()
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--artist", action="append")
    p.add_argument("--genre")
    p.add_argument("--min-duration", type=float, default=120.0,
                   help="skip tracks shorter than this (seconds) — excludes intros/outros/interludes")
    p.add_argument("--max-duration", type=float)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    tracks = sample(args.db, args.artist, args.genre, args.min_duration, args.max_duration)
    total = len(tracks)
    print(f"# {total} tracks, workers={args.workers}", file=sys.stderr)
    print("artist\ttitle\tknown_bpm\taubio_bpm\tpath")

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(aubio_bpm, path): (artist, title, known, path)
                   for artist, title, known, path in tracks}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            artist, title, known, path = futures[fut]
            bpm = fut.result()
            k = f"{known:.1f}" if known is not None else ""
            b = f"{bpm:.1f}" if bpm is not None else ""
            # tab-separated, strip tabs/newlines from fields
            clean = lambda s: (s or "").replace("\t", " ").replace("\n", " ")
            print(f"{clean(artist)}\t{clean(title)}\t{k}\t{b}\t{clean(path)}", flush=True)
            if i % 50 == 0 or i == total:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (total - i) / rate if rate else 0
                print(f"# [{i}/{total}] {rate:.1f}/s eta {eta:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
