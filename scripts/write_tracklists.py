#!/usr/bin/env python3
# /// script
# dependencies = ["mutagen>=1.47"]
# ///
"""Write tracklist.txt files next to existing playlist mp4s.

Walks tmp/playlist_sources for ramp tsvs and their sibling _work dirs,
probes retempoed mp3 durations, and emits {slug}_tracklist.txt in the
matching exports directory for pasting into YouTube descriptions.

Usage:
  uv run scripts/write_tracklists.py
  uv run scripts/write_tracklists.py --crossfade 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from generate_run_playlists import write_tracklist

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources-dir", type=Path,
                   default=REPO / "tmp" / "playlist_sources")
    p.add_argument("--output-dir", type=Path,
                   default=REPO / "tmp" / "playlists")
    p.add_argument("--crossfade", type=int, default=4)
    args = p.parse_args()

    written = 0
    for tsv in sorted(args.sources_dir.rglob("*.tsv")):
        slug = tsv.stem
        work = tsv.with_name(f"{slug}_work")
        if not work.is_dir():
            print(f"{tsv}: no _work dir — skipping", file=sys.stderr)
            continue

        sources: list[Path] = []
        for line in tsv.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            sources.append(Path(line.split("\t")[0]))

        retempoed = sorted(work.glob("*.mp3"))
        if len(retempoed) != len(sources):
            print(f"{tsv}: {len(sources)} sources but {len(retempoed)} "
                  f"retempoed files — skipping", file=sys.stderr)
            continue

        rel = tsv.parent.relative_to(args.sources_dir)
        exports = args.output_dir / rel
        if not (exports / f"{slug}.mp4").exists() and \
           not (exports / f"{slug}.mp3").exists():
            print(f"{tsv}: no matching export in {exports} — skipping",
                  file=sys.stderr)
            continue

        exports.mkdir(parents=True, exist_ok=True)
        out = exports / f"{slug}_tracklist.txt"
        write_tracklist(out, sources, retempoed, args.crossfade)
        print(f"wrote {out}")
        written += 1

    print(f"done — {written} tracklist(s) written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
