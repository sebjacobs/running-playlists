#!/usr/bin/env python3
"""Probe Beatport track search for BPM coverage.

Beatport no longer offers a public API; this scrapes the search results
page and extracts BPM from the embedded Next.js data blob. Use sparingly
and with a reasonable delay between requests.

Usage:
  uv run scripts/probe_beatport.py --db music.db --genre 'D&B' \
    --max-duration 600 --n 50 > tmp/probe_beatport_dnb.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = "https://www.beatport.com/search/tracks"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

BPM_RE = re.compile(r'"bpm"\s*:\s*(\d+(?:\.\d+)?)')


def search(artist: str, title: str) -> tuple[float | None, int]:
    q = f"{artist} {title}"
    url = f"{SEARCH_URL}?{urllib.parse.urlencode({'q': q})}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    matches = BPM_RE.findall(html)
    if not matches:
        return None, 0
    return float(matches[0]), len(matches)


def sample(db: Path, artists, genre, min_duration, max_duration, n):
    conn = sqlite3.connect(db)
    where = ["artist IS NOT NULL", "title IS NOT NULL"]
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
    params.append(n)
    rows = conn.execute(
        f"SELECT artist, title, bpm FROM tracks WHERE {' AND '.join(where)} "
        f"ORDER BY artist, title LIMIT ?",
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
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    p.add_argument("--double-below", type=float, default=100.0,
                   help="double the returned bpm if below this threshold (DnB half-tempo fix)")
    args = p.parse_args()

    tracks = sample(args.db, args.artist, args.genre, args.min_duration, args.max_duration, args.n)
    if not tracks:
        print("no tracks found", file=sys.stderr)
        return 1

    print(f"# {len(tracks)} tracks, delay={args.delay}s", file=sys.stderr)
    print(f"{'artist':<30} {'title':<45} {'known':>7} {'raw':>5} {'adj':>5}")
    print("-" * 95)

    hits = 0
    for artist, title, known in tracks:
        try:
            bpm, n_hits = search(artist, title)
        except Exception as exc:
            print(f"{artist[:30]:<30} {title[:45]:<45}  ERROR: {exc}")
            time.sleep(args.delay)
            continue
        if bpm is not None:
            hits += 1
        adj = bpm * 2 if (bpm is not None and bpm < args.double_below) else bpm
        k = f"{known:.0f}" if known else "-"
        b = f"{bpm:.0f}" if bpm else "-"
        a = f"{adj:.0f}" if adj else "-"
        print(f"{artist[:30]:<30} {title[:45]:<45} {k:>7} {b:>5} {a:>5}", flush=True)
        time.sleep(args.delay)

    print("-" * 95)
    print(f"coverage: {hits}/{len(tracks)} ({hits/len(tracks)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
