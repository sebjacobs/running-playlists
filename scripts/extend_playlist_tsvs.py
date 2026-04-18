#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""Extend short ramp tsvs to a target duration by appending fresh tracks.

Reads existing tsvs, picks additional tracks at the same target-bpm from
music.db (excluding tracks already used in any playlist at that bpm and
respecting a max-per-artist cap across the extended list), and writes the
extended tsvs to an output directory ready for re-rendering via:

    uv run scripts/generate_run_playlists.py --from-tsv <ext-tsvs> \
        --target-bpm <bpm> --start-n <n>
"""
from __future__ import annotations

import argparse
import random
import re
import sqlite3
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def parse_header_duration(tsv: Path) -> float:
    for line in tsv.read_text().splitlines():
        if line.startswith("#"):
            m = re.search(r"duration=([\d.]+)m", line)
            if m:
                return float(m.group(1)) * 60
    return 0.0


def parse_tracks(tsv: Path) -> list[tuple[str, float]]:
    out = []
    for line in tsv.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        out.append((parts[0], float(parts[1])))
    return out


def ffprobe_dur(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=REPO / "music.db")
    p.add_argument("--target-bpm", type=float, required=True)
    p.add_argument("--bpm-range", type=float, default=4.0)
    p.add_argument("--target-min", type=float, default=36.0)
    p.add_argument("--tolerance-s", type=float, default=60.0)
    p.add_argument("--min-duration", type=float, default=180.0)
    p.add_argument("--max-duration", type=float, default=420.0)
    p.add_argument("--seed", type=int, default=101)
    p.add_argument("--exclude-dir", type=Path, nargs="+", default=[],
                   help="read every *.tsv under these dirs; their track paths "
                        "are globally excluded from selection")
    p.add_argument("--tsv", type=Path, nargs="+", required=True,
                   help="short tsvs to extend")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="write extended tsvs here (filename preserved)")
    args = p.parse_args()

    random.seed(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    global_excluded: set[str] = set()
    for d in args.exclude_dir:
        for t in sorted(d.rglob("*.tsv")):
            for path, _bpm in parse_tracks(t):
                global_excluded.add(path)
    print(f"globally excluding {len(global_excluded)} tracks from selection")

    conn = sqlite3.connect(args.db)
    pool_rows = conn.execute(
        """
        SELECT artist, title, bpm, duration_s, path FROM tracks
        WHERE bpm BETWEEN ? AND ?
          AND duration_s BETWEEN ? AND ?
          AND bpm_source IS NOT NULL
          AND (run_exclude IS NULL OR run_exclude = 0)
        """,
        (args.target_bpm - args.bpm_range, args.target_bpm + args.bpm_range,
         args.min_duration, args.max_duration),
    ).fetchall()
    conn.close()

    pool_by_path = {r[4]: r for r in pool_rows}
    seen_pair: dict[tuple[str, str], tuple] = {}
    for r in pool_rows:
        seen_pair.setdefault((r[0], r[1]), r)
    unique_pool = list(seen_pair.values())
    random.shuffle(unique_pool)
    print(f"eligible tracks at {args.target_bpm}±{args.bpm_range} bpm: {len(unique_pool)}")

    used_across_extensions: set[str] = set()
    target_s = args.target_min * 60

    for tsv in args.tsv:
        existing = parse_tracks(tsv)
        existing_total = sum(
            (pool_by_path[p][3] if p in pool_by_path else ffprobe_dur(p))
            for p, _ in existing
        )
        # retempo compresses/stretches: final duration ≈ existing_total * src_bpm / target_bpm
        # but existing tracks were retempoed to target already if they were written
        # with target_bpm; treat existing_total as approximate at target.
        existing_paths = {p for p, _ in existing}
        existing_artists: set[str] = set()
        for p, _ in existing:
            row = pool_by_path.get(p)
            if row:
                existing_artists.add(row[0])

        needed_s = target_s - existing_total
        if needed_s <= args.tolerance_s:
            print(f"{tsv.name}: already at or above target ({existing_total/60:.1f}m); skipping")
            continue

        picks: list[tuple] = []
        running = existing_total
        artists = set(existing_artists)
        for cand in unique_pool:
            c_artist, _c_title, _c_bpm, c_dur, c_path = cand
            if c_path in existing_paths:
                continue
            if c_path in global_excluded:
                continue
            if c_path in used_across_extensions:
                continue
            if c_artist in artists:
                continue
            if running + c_dur > target_s + args.tolerance_s:
                continue
            picks.append(cand)
            artists.add(c_artist)
            used_across_extensions.add(c_path)
            running += c_dur
            if abs(running - target_s) <= args.tolerance_s:
                break

        ext_out = args.out_dir / tsv.name
        with ext_out.open("w") as f:
            f.write(f"# target_bpm={int(args.target_bpm)}, duration={running/60:.1f}m\n")
            for path, src_bpm in existing:
                f.write(f"{path}\t{src_bpm}\t{int(args.target_bpm)}\n")
            for a, t, bpm, d, path in picks:
                f.write(f"{path}\t{bpm}\t{int(args.target_bpm)}\n")
        print(f"{tsv.name}: +{len(picks)} tracks, {existing_total/60:.1f}m → {running/60:.1f}m")
        for a, t, _bpm, d, _p in picks:
            print(f"    + {d/60:4.1f}m  {a} — {t}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
