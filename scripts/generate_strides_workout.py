#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""Generate a strides workout mix at a target BPM.

Extracts bar-aligned verse phrases from tracks in music.db using Traktor
beatgrid anchors, skipping the intro, retempos each phrase to the target
BPM via rubberband, then crossfades all phrases into a single continuous mix.

Because every phrase is cut at a bar boundary computed from traktor_beatgrid_ms
and then stretched uniformly to the same BPM, all clips land on the same
beat phase — crossfades stay phase-locked across the whole mix.

    32 bars @ 175bpm ≈ 43.9s per phrase
    16-bar crossfade ≈ 21.9s overlap between phrases
    → 22s effective net contribution per phrase to total duration

Usage:
  uv run scripts/generate_strides_workout.py --db music.db
  uv run scripts/generate_strides_workout.py --db music.db \\
      --duration-min 20 --phrase-bars 24 --phrases-per-track 2 \\
      --intro-bars 64 --xfade-bars 2 --seed 7
"""
from __future__ import annotations

import argparse
import datetime
import math
import os
import random
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIX_SH = REPO / "scripts" / "mix.sh"
DEFAULT_DB = REPO / "music.db"
PLAYLISTS_ROOT = REPO / "tmp" / "playlists"


# ---------------------------------------------------------------------------
# ffmpeg / rubberband helpers
# ---------------------------------------------------------------------------

def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def extract_and_retempo(
    src: Path,
    start_s: float,
    dur_s: float,
    src_bpm: float,
    target_bpm: float,
    out: Path,
) -> None:
    """Extract a segment (sample-accurate) and retempo to target_bpm."""
    wav = out.with_suffix(".wav")
    # -ss after -i for sample-accurate extraction (slower, but phase-correct)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}",
         "-ac", "2", "-ar", "44100", str(wav)],
        check=True, capture_output=True,
    )
    if abs(src_bpm - target_bpm) < 0.1:
        stretched = None
        encode_src = wav
    else:
        ratio = target_bpm / src_bpm
        stretched = wav.with_name(wav.stem + ".stretched.wav")
        subprocess.run(
            ["rubberband", "--tempo", f"{ratio:.6f}", "--crisp", "6",
             str(wav), str(stretched)],
            check=True, capture_output=True,
        )
        encode_src = stretched
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(encode_src),
         "-map_metadata", "-1", "-b:a", "192k", str(out)],
        check=True, capture_output=True,
    )
    wav.unlink()
    if stretched:
        stretched.unlink()


# ---------------------------------------------------------------------------
# Track selection
# ---------------------------------------------------------------------------

def select_tracks(conn: sqlite3.Connection, target_bpm: float, bpm_range: float) -> list:
    rows = conn.execute(
        """
        SELECT id, artist, title, bpm, duration_s, path, traktor_beatgrid_ms,
               COALESCE(phasing_heavy, 0)
        FROM tracks
        WHERE bpm BETWEEN ? AND ?
          AND traktor_beatgrid_ms IS NOT NULL
          AND (run_exclude IS NULL OR run_exclude = 0)
          AND path IS NOT NULL
        """,
        (target_bpm - bpm_range, target_bpm + bpm_range),
    ).fetchall()
    seen: dict = {}
    for r in rows:
        seen.setdefault((r[1], r[2]), r)
    return list(seen.values())


def find_drop_cue_ms(conn: sqlite3.Connection, track_id: int, cue_name: str) -> float | None:
    """Return start_ms of the first cue whose NAME matches cue_name (ci), else None."""
    row = conn.execute(
        "SELECT start_ms FROM traktor_cues "
        "WHERE track_id = ? AND LOWER(name) = LOWER(?) "
        "ORDER BY start_ms LIMIT 1",
        (track_id, cue_name),
    ).fetchone()
    return row[0] if row else None


def snap_to_bar(t_ms: float, grid_ms: float, bar_ms: float) -> float:
    """Snap an arbitrary timecode to the nearest bar boundary on the grid."""
    return grid_ms + round((t_ms - grid_ms) / bar_ms) * bar_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--target-bpm", type=float, default=175.0)
    p.add_argument("--bpm-range", type=float, default=10.0,
                   help="accept tracks within ±N bpm of target (default: 10)")
    p.add_argument("--duration-min", type=float, default=10.0,
                   help="target mix duration in minutes (default: 10)")
    p.add_argument("--phrase-bars", type=int, default=32,
                   help="bars per phrase; 32 bars ≈ 43.9s @ 175bpm (default: 32)")
    p.add_argument("--phrases-per-track", type=int, default=1,
                   help="consecutive bar-aligned phrases to take from the same track "
                        "before moving on (default: 1)")
    p.add_argument("--intro-bars", type=int, default=48,
                   help="bars to skip from beatgrid anchor before cutting first phrase "
                        "(skips intro; default: 48 ≈ 66s @ 175bpm)")
    p.add_argument("--xfade-bars", type=int, default=16,
                   help="crossfade length in bars at target BPM; "
                        "16 bars ≈ 21.9s @ 175bpm (default: 16)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--count", type=int, default=None,
                   help="exact number of phrases to include (overrides --duration-min)")
    p.add_argument("--cue-name", default="drop",
                   help="Traktor hotcue NAME to use as phrase anchor when present "
                        "(case-insensitive; falls back to grid+intro-bars; default: 'drop')")
    p.add_argument("--post-cue-bars", type=int, default=0,
                   help="bars to advance past the cue before cutting (default: 0)")
    p.add_argument("--first-artists", default="",
                   help="comma-separated artist name fragments to prioritise first "
                        "(case-insensitive substring match, e.g. 'technimatic,technicolour')")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="output dir (default: tmp/playlists/<bpm>bpm/strides/)")
    p.add_argument("--cover", type=Path, default=REPO / "assets" / "cover.png",
                   help="cover image for mp4 wrap")
    p.add_argument("--no-video", action="store_true", help="skip mp4 wrap")
    args = p.parse_args()

    if args.output_dir is None:
        args.output_dir = PLAYLISTS_ROOT / f"{int(args.target_bpm)}bpm" / "strides"

    # Derived timing constants (all at target BPM after retempoing)
    bar_s = 4 * 60 / args.target_bpm
    phrase_s = args.phrase_bars * bar_s
    xfade_s = args.xfade_bars * bar_s
    effective_phrase_s = phrase_s - xfade_s  # each phrase's net contribution to total

    if effective_phrase_s <= 0:
        print("Error: xfade-bars must be smaller than phrase-bars", file=sys.stderr)
        return 1

    # Number of phrases
    if args.count is not None:
        n_phrases = max(2, args.count)
    else:
        target_s = args.duration_min * 60
        n_phrases = max(2, math.ceil((target_s - xfade_s) / effective_phrase_s))

    conn = sqlite3.connect(args.db)
    tracks = select_tracks(conn, args.target_bpm, args.bpm_range)

    print(f"Eligible tracks (±{args.bpm_range}bpm, has beatgrid): {len(tracks)}", file=sys.stderr)
    print(f"Need {n_phrases} phrases × {phrase_s:.1f}s ({args.phrase_bars} bars)", file=sys.stderr)
    print(f"Crossfade: {args.xfade_bars} bars = {xfade_s:.2f}s", file=sys.stderr)

    rng = random.Random(args.seed)
    rng.shuffle(tracks)

    # Move first-artists matches to the front
    if args.first_artists:
        fragments = [f.strip().lower() for f in args.first_artists.split(",") if f.strip()]
        priority = [r for r in tracks if any(f in (r[0] or "").lower() for f in fragments)]
        rest = [r for r in tracks if r not in priority]
        tracks = priority + rest

    # Build clip list: (artist, title, bpm, path, start_s, dur_s)
    clips: list[tuple] = []
    seen_artists: set[str] = set()

    cue_used = 0
    last_was_phased = False
    for row in tracks:
        if len(clips) >= n_phrases:
            break
        track_id, artist, title, bpm, duration_s, path, grid_ms, phasing_heavy = row
        akey = (artist or "").lower().strip()
        if akey in seen_artists:
            continue
        # Avoid placing two phasing-heavy tracks in adjacent crossfade windows —
        # phased drums against phased drums = comb-filter mush regardless of grid.
        if last_was_phased and phasing_heavy:
            continue

        native_bar_ms = 4 * 60000 / bpm
        cue_ms = find_drop_cue_ms(conn, track_id, args.cue_name)
        if cue_ms is not None:
            anchor_ms = snap_to_bar(cue_ms, grid_ms, native_bar_ms) \
                + args.post_cue_bars * native_bar_ms
            anchor_label = f"cue:{args.cue_name}@{cue_ms/1000:.2f}s"
        else:
            anchor_ms = grid_ms + args.intro_bars * native_bar_ms
            anchor_label = f"grid+{args.intro_bars}bars"
        added = 0
        for phrase_idx in range(args.phrases_per_track):
            if len(clips) >= n_phrases:
                break
            start_ms = anchor_ms + phrase_idx * args.phrase_bars * native_bar_ms
            dur_ms = args.phrase_bars * native_bar_ms
            # Ensure phrase fits within the track
            if (start_ms + dur_ms) / 1000 > duration_s - 1.0:
                print(f"  skip {artist} — {title}: phrase {phrase_idx+1} exceeds track length",
                      file=sys.stderr)
                break
            clips.append((artist, title, bpm, path, start_ms / 1000, dur_ms / 1000))
            added += 1

        if added > 0:
            seen_artists.add(akey)
            last_was_phased = bool(phasing_heavy)
            if cue_ms is not None:
                cue_used += 1
            tag = " [phasing]" if phasing_heavy else ""
            print(f"  pick: {artist} — {title}{tag}  ({anchor_label})", file=sys.stderr)

    conn.close()
    print(f"Cue-anchored: {cue_used}/{len(clips)} phrases (rest via grid+intro-bars)",
          file=sys.stderr)

    if len(clips) < 2:
        print("Not enough eligible tracks — try widening --bpm-range or reducing --duration-min",
              file=sys.stderr)
        return 1

    if len(clips) < n_phrases:
        print(f"Warning: only {len(clips)} phrases available (wanted {n_phrases})", file=sys.stderr)

    # Setup output dirs
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = args.output_dir / f"_work_{os.getpid()}"
    work_dir.mkdir(exist_ok=True)

    # Extract and retempo each phrase
    retempoed: list[Path] = []
    for i, (artist, title, bpm, path, start_s, dur_s) in enumerate(clips, 1):
        out = work_dir / f"{i:03d}_{(artist or 'unknown')[:24].replace('/', '_')}.mp3"
        print(
            f"  [{i:2d}/{len(clips)}] {artist} — {title}  "
            f"start={start_s:.1f}s dur={dur_s:.1f}s  {bpm:.1f}→{args.target_bpm:.0f}bpm",
            file=sys.stderr,
        )
        extract_and_retempo(Path(path), start_s, dur_s, bpm, args.target_bpm, out)
        retempoed.append(out)

    # Mix all phrases with bar-aligned crossfades
    today = datetime.date.today().isoformat()
    actual_min = int(round(
        (len(clips) * phrase_s - (len(clips) - 1) * xfade_s) / 60
    ))
    slug = f"{today}_strides_{int(args.target_bpm)}bpm_{actual_min}min"
    out_mp3 = args.output_dir / f"{slug}.mp3"

    print(f"\nMixing {len(retempoed)} phrases (xfade={xfade_s:.2f}s) → {out_mp3.name}",
          file=sys.stderr)
    subprocess.run(
        [str(MIX_SH), "-d", f"{xfade_s:.3f}", "-o", str(out_mp3),
         *[str(rp) for rp in retempoed]],
        check=True,
    )

    # Tracklist with timestamps
    tracklist_path = args.output_dir / f"{slug}_tracklist.txt"
    lines = [f"# {int(args.target_bpm)}bpm strides mix — {actual_min}min",
             f"# phrase: {args.phrase_bars} bars = {phrase_s:.1f}s  |  "
             f"xfade: {args.xfade_bars} bars = {xfade_s:.2f}s  |  "
             f"intro skip: {args.intro_bars} bars",
             ""]
    cursor = 0.0
    last_key: tuple | None = None
    for i, (artist, title, bpm, path, start_s, dur_s) in enumerate(clips):
        key = (artist, title)
        if key != last_key:
            mm, ss = divmod(int(cursor), 60)
            suffix = f" (×{args.phrases_per_track})" if args.phrases_per_track > 1 else ""
            lines.append(f"{mm:02d}:{ss:02d}  {artist} — {title}{suffix}  [{bpm:.1f}bpm → {start_s:.1f}s in track]")
            last_key = key
        d = probe_duration(retempoed[i])
        cursor += d - (xfade_s if i < len(clips) - 1 else 0)
    tracklist_path.write_text("\n".join(lines) + "\n")
    print(f"Tracklist → {tracklist_path}", file=sys.stderr)

    # Cleanup work dir
    shutil.rmtree(work_dir)

    total_s = probe_duration(out_mp3)
    print(f"\nDone → {out_mp3}  ({total_s / 60:.1f} min)", file=sys.stderr)

    # Wrap as mp4 for YouTube
    if not args.no_video:
        tovideo = REPO / "scripts" / "tovideo.sh"
        if not args.cover.exists():
            print(f"Cover not found at {args.cover} — skipping mp4 wrap", file=sys.stderr)
        else:
            out_mp4 = args.output_dir / f"{slug}.mp4"
            print(f"Wrapping → {out_mp4.name}", file=sys.stderr)
            subprocess.run(
                [str(tovideo), "-i", str(args.cover), "-a", str(out_mp3),
                 "-o", str(out_mp4), "-b", str(int(args.target_bpm)),
                 "-d", str(actual_min)],
                check=True,
            )
            print(f"Done → {out_mp4}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
