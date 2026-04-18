#!/usr/bin/env python3
"""Probe GetSongBPM lookup against a small sample from music.db.

Usage:
  uv run --env-file .env scripts/probe_getsongbpm.py --db music.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.getsongbpm.com/search/"


def lookup(api_key: str, artist: str, title: str) -> dict:
    lookup_str = f"song:{title} artist:{artist}"
    params = urllib.parse.urlencode({
        "api_key": api_key,
        "type": "both",
        "lookup": lookup_str,
    })
    url = f"{API_BASE}?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "running-playlists-probe/0.1",
        "Referer": os.environ.get("BPM_API_REFERER", "https://github.com/sebjacobs/running-playlists"),
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def first_tempo(payload: dict) -> float | None:
    search = payload.get("search")
    if not isinstance(search, list) or not search:
        return None
    first = search[0]
    tempo = first.get("tempo")
    try:
        return float(tempo) if tempo is not None else None
    except (TypeError, ValueError):
        return None


def sample(
    db: Path,
    n: int,
    artists: list[str] | None,
    genre: str | None,
    min_duration: float | None,
    max_duration: float | None,
) -> list[tuple[str, str, float | None]]:
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
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--artist", action="append", help="restrict to these artists (repeatable)")
    p.add_argument("--genre", help="restrict to exact genre string")
    p.add_argument("--min-duration", type=float, default=120.0,
                   help="skip tracks shorter than this (seconds) — excludes intros/outros/interludes")
    p.add_argument("--max-duration", type=float, help="exclude tracks longer than this (seconds)")
    p.add_argument("--raw", action="store_true", help="print first raw response")
    args = p.parse_args()

    api_key = os.environ.get("BPM_API_KEY")
    if not api_key:
        print("BPM_API_KEY not set (try: uv run --env-file .env ...)", file=sys.stderr)
        return 1

    tracks = sample(args.db, args.n, args.artist, args.genre, args.min_duration, args.max_duration)
    if not tracks:
        print("no tracks found", file=sys.stderr)
        return 1

    print(f"{'artist':<30} {'title':<40} {'known':>7} {'api':>7} {'delta':>7}")
    print("-" * 95)

    hits = 0
    for i, (artist, title, known) in enumerate(tracks):
        try:
            payload = lookup(api_key, artist, title)
        except Exception as exc:
            print(f"{artist[:30]:<30} {title[:40]:<40}  ERROR: {exc}")
            continue

        if args.raw and i == 0:
            print("--- raw response for first lookup ---")
            print(json.dumps(payload, indent=2)[:1000])
            print("--- end raw ---")

        api_bpm = first_tempo(payload)
        if api_bpm is not None:
            hits += 1
        k = f"{known:.0f}" if known else "-"
        a = f"{api_bpm:.0f}" if api_bpm else "-"
        d = f"{api_bpm - known:+.0f}" if (api_bpm and known) else "-"
        print(f"{artist[:30]:<30} {title[:40]:<40} {k:>7} {a:>7} {d:>7}")
        time.sleep(0.3)

    print("-" * 95)
    print(f"coverage: {hits}/{len(tracks)} ({hits/len(tracks)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
