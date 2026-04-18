#!/usr/bin/env python3
"""Strict Beatport matcher: parse __NEXT_DATA__, match artist+title exactly.

Fetches Beatport search pages with an on-disk cache and parallel workers,
then picks the first result whose artist_name and track_name match the
query (case- and punctuation-insensitive). Applies half-tempo doubling
for DnB (raw BPM < double_below).

Input TSV on stdin or via --input:
  artist<TAB>title[<TAB>...ignored...]

Output TSV on stdout:
  artist<TAB>title<TAB>match_artist<TAB>match_title<TAB>raw_bpm<TAB>adj_bpm<TAB>status

status is one of: match, no_match, error
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = "https://www.beatport.com/search/tracks"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def normalise(s: str) -> str:
    """Lowercase, strip non-alphanumerics — tolerates punctuation drift."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def fetch(artist: str, title: str, cache_dir: Path) -> str:
    key = hashlib.sha256(f"{artist}\t{title}".encode()).hexdigest()[:20]
    cache_path = cache_dir / f"{key}.html"
    if cache_path.exists():
        return cache_path.read_text()
    q = f"{artist} {title}"
    url = f"{SEARCH_URL}?{urllib.parse.urlencode({'q': q})}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    cache_path.write_text(html)
    return html


def extract_tracks(html: str) -> list[dict]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        return data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return []


def find_match(tracks: list[dict], artist: str, title: str) -> dict | None:
    na, nt = normalise(artist), normalise(title)
    for t in tracks:
        track_artists = [normalise(a.get("artist_name", "")) for a in t.get("artists", [])]
        if na not in track_artists:
            continue
        if normalise(t.get("track_name", "")) == nt:
            return t
    return None


def process(artist: str, title: str, cache_dir: Path, double_below: float):
    try:
        html = fetch(artist, title, cache_dir)
    except Exception as exc:
        return (artist, title, "", "", None, None, f"error:{exc}")
    tracks = extract_tracks(html)
    if not tracks:
        return (artist, title, "", "", None, None, "no_data")
    match = find_match(tracks, artist, title)
    if match is None:
        return (artist, title, "", "", None, None, "no_match")
    raw = match.get("bpm")
    try:
        raw = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        raw = None
    adj = raw * 2 if (raw is not None and raw < double_below) else raw
    m_artist = ", ".join(a.get("artist_name", "") for a in match.get("artists", []))
    m_title = match.get("track_name", "")
    return (artist, title, m_artist, m_title, raw, adj, "match")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, help="TSV of artist<TAB>title rows (default: stdin)")
    p.add_argument("--cache-dir", type=Path, default=Path("tmp/beatport_cache"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--double-below", type=float, default=100.0)
    args = p.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    src = args.input.open() if args.input else sys.stdin
    rows = []
    for line in src:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            rows.append((parts[0], parts[1]))
    if args.input:
        src.close()

    print("artist\ttitle\tmatch_artist\tmatch_title\traw_bpm\tadj_bpm\tstatus")

    start = time.time()
    matched = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(process, a, t, args.cache_dir, args.double_below) for a, t in rows]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            artist, title, ma, mt, raw, adj, status = fut.result()
            if status == "match":
                matched += 1
            raw_s = f"{raw:.1f}" if raw is not None else ""
            adj_s = f"{adj:.1f}" if adj is not None else ""
            print(f"{artist}\t{title}\t{ma}\t{mt}\t{raw_s}\t{adj_s}\t{status}", flush=True)
            if i % 25 == 0 or i == len(rows):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                print(f"# [{i}/{len(rows)}] matched={matched} {rate:.1f}/s", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
