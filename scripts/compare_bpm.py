#!/usr/bin/env python3
"""Cross-check Beatport vs GetSongBPM probe outputs.

Both probes run against the same SQL filter with the same ORDER BY, so
rows align by position. This script zips them, computes per-track
agreement, and flags disagreements over a tolerance.

Usage:
  uv run scripts/compare_bpm.py \
    --beatport tmp/probe_beatport_dnb.txt \
    --getsongbpm tmp/probe_dnb_under10min.txt \
    --tolerance 2 > tmp/compare_bpm_dnb.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Row lines end with numeric columns; header/footer don't.
# Beatport columns (right-aligned widths: known 7, raw 5, adj 5)
BEATPORT_ROW = re.compile(r"^(.{30}) (.{45}) (.{7}) (.{5}) (.{5})$")
# GetSongBPM columns (widths: known 7, api 7, delta 7)
GSB_ROW = re.compile(r"^(.{30}) (.{40}) (.{7}) (.{7}) (.{7})$")


def parse_num(s: str) -> float | None:
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_beatport(path: Path) -> list[tuple[str, str, float | None]]:
    rows = []
    for line in path.read_text().splitlines():
        m = BEATPORT_ROW.match(line)
        if not m:
            continue
        artist, title, _known, _raw, adj = m.groups()
        # skip header line where "adj" is literally "  adj"
        if adj.strip() == "adj":
            continue
        rows.append((artist.strip(), title.strip(), parse_num(adj)))
    return rows


def parse_gsb(path: Path) -> list[tuple[str, str, float | None]]:
    rows = []
    for line in path.read_text().splitlines():
        m = GSB_ROW.match(line)
        if not m:
            continue
        artist, title, _known, api, _delta = m.groups()
        if api.strip() == "api":
            continue
        rows.append((artist.strip(), title.strip(), parse_num(api)))
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--beatport", required=True, type=Path)
    p.add_argument("--getsongbpm", required=True, type=Path)
    p.add_argument("--tolerance", type=float, default=2.0,
                   help="BPM difference treated as agreement")
    p.add_argument("--disagreements-out", type=Path,
                   help="write all disagreeing artist<TAB>title rows here")
    args = p.parse_args()

    bp = parse_beatport(args.beatport)
    gsb = parse_gsb(args.getsongbpm)

    # Build a lookup from GetSongBPM by (artist,title) so we can match even
    # if ordering drifts.
    gsb_idx = {(a, t): v for a, t, v in gsb}

    both = agree = disagree = half_double = only_bp = only_gsb = 0
    disagreements: list[tuple[str, str, float, float]] = []

    for artist, title, bp_v in bp:
        gsb_v = gsb_idx.get((artist, title))
        if bp_v is not None and gsb_v is not None:
            both += 1
            if abs(bp_v - gsb_v) <= args.tolerance:
                agree += 1
            elif abs(bp_v - 2 * gsb_v) <= args.tolerance or abs(2 * bp_v - gsb_v) <= args.tolerance:
                half_double += 1
            else:
                disagree += 1
                disagreements.append((artist, title, bp_v, gsb_v))
        elif bp_v is not None:
            only_bp += 1
        elif gsb_v is not None:
            only_gsb += 1

    total = len(bp)
    print(f"total tracks: {total}")
    print(f"both sources:   {both}")
    print(f"  agree (±{args.tolerance}): {agree}  ({agree/both*100:.0f}% of overlap)" if both else "  agree: 0")
    print(f"  half/double mismatch: {half_double}")
    print(f"  disagree:             {disagree}")
    print(f"only Beatport:  {only_bp}")
    print(f"only GetSongBPM:{only_gsb}")
    print()
    if args.disagreements_out and disagreements:
        with args.disagreements_out.open("w") as f:
            for artist, title, bp_v, gsb_v in disagreements:
                f.write(f"{artist}\t{title}\t{bp_v:.1f}\t{gsb_v:.1f}\n")
        print(f"wrote {len(disagreements)} disagreements to {args.disagreements_out}")
    if disagreements:
        print("=== disagreements (first 30) ===")
        print(f"{'artist':<30} {'title':<45} {'beatport':>9} {'gsb':>5} {'diff':>5}")
        for artist, title, bp_v, gsb_v in disagreements[:30]:
            print(f"{artist[:30]:<30} {title[:45]:<45} {bp_v:>9.0f} {gsb_v:>5.0f} {bp_v-gsb_v:>+5.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
