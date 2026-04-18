#!/usr/bin/env python3
# /// script
# dependencies = ["mutagen>=1.47"]
# ///
"""Generate run playlists at a target BPM from music.db.

Selects eligible D&B tracks, builds non-overlapping playlists targeting
a duration with artist variety, retempos each via rubberband using the
stored DB BPM (bypassing aubio — unreliable for breakbeat), and mixes
with crossfades via scripts/mix.sh.

Output: tmp/playlists/run<target>_<n>.mp3 plus matching .tsv ramp files.

Usage:
  uv run scripts/generate_run_playlists.py --db music.db \\
    --target-bpm 174 --duration-min 30 --count 3 --seed 42
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

REPO = Path(__file__).resolve().parent.parent
MIX_SH = REPO / "scripts" / "mix.sh"


def select(conn, target_bpm, min_bpm, max_bpm, min_dur, max_dur):
    rows = conn.execute(
        """
        SELECT artist, title, bpm, duration_s, path FROM tracks
        WHERE bpm BETWEEN ? AND ?
          AND duration_s BETWEEN ? AND ?
          AND bpm_source IS NOT NULL
          AND (run_exclude IS NULL OR run_exclude = 0)
        """,
        (min_bpm, max_bpm, min_dur, max_dur),
    ).fetchall()
    seen = {}
    for r in rows:
        seen.setdefault((r[0], r[1]), r)
    return list(seen.values())


def build_playlist(tracks, target_s, tol_s, max_per_artist=1):
    picks, artists, total = [], {}, 0
    for t in tracks:
        if artists.get(t[0], 0) >= max_per_artist:
            continue
        if total + t[3] > target_s + tol_s:
            continue
        picks.append(t)
        artists[t[0]] = artists.get(t[0], 0) + 1
        total += t[3]
        if abs(total - target_s) <= tol_s:
            break
    return picks, total


def retempo(src: Path, src_bpm: float, target_bpm: float, out: Path) -> None:
    """Decode → (optionally stretch) → encode to MP3 for a consistent mix input."""
    wav = out.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-i", str(src), "-ac", "2", "-ar", "44100", str(wav)],
                   check=True, capture_output=True)
    if abs(src_bpm - target_bpm) < 0.1:
        source_for_encode = wav
        stretched = None
    else:
        ratio = target_bpm / src_bpm
        stretched = wav.with_name(wav.stem + ".stretched.wav")
        subprocess.run(["rubberband", "--tempo", f"{ratio:.6f}", "--crisp", "6",
                        str(wav), str(stretched)], check=True, capture_output=True)
        source_for_encode = stretched
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source_for_encode),
         "-map_metadata", "-1",
         "-b:a", "192k",
         "-metadata", f"TBPM={int(round(target_bpm))}",
         "-metadata", f"bpm={int(round(target_bpm))}",
         str(out)],
        check=True, capture_output=True,
    )
    wav.unlink()
    if stretched is not None:
        stretched.unlink()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--target-bpm", type=float, default=174.0)
    p.add_argument("--duration-min", type=float, default=30.0)
    p.add_argument("--tolerance-s", type=float, default=60.0)
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--bpm-range", type=float, default=4.0,
                   help="accept tracks within ±N of target bpm")
    p.add_argument("--min-duration", type=float, default=180.0)
    p.add_argument("--max-duration", type=float, default=420.0)
    p.add_argument("--crossfade", type=int, default=4)
    p.add_argument("--output-dir", type=Path, default=REPO / "tmp" / "playlists")
    args = p.parse_args()

    random.seed(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    tracks = select(
        conn,
        args.target_bpm,
        args.target_bpm - args.bpm_range,
        args.target_bpm + args.bpm_range,
        args.min_duration,
        args.max_duration,
    )
    conn.close()
    random.shuffle(tracks)
    print(f"eligible unique tracks: {len(tracks)}", file=sys.stderr)

    target_s = args.duration_min * 60
    remaining = list(tracks)
    for n in range(1, args.count + 1):
        pl, total = build_playlist(remaining, target_s, args.tolerance_s)
        if not pl:
            print(f"playlist {n}: no tracks left — stopping", file=sys.stderr)
            break
        slug = f"run{int(args.target_bpm)}_{n}"
        ramp_path = args.output_dir / f"{slug}.tsv"
        with ramp_path.open("w") as f:
            f.write(f"# target_bpm={int(args.target_bpm)}, duration={total/60:.1f}m\n")
            for a, t, bpm, d, path in pl:
                f.write(f"{path}\t{bpm}\t{int(args.target_bpm)}\n")
        print(f"\n=== Playlist {n} → {slug}.mp3 ({total/60:.1f} min) ===", file=sys.stderr)
        for a, t, bpm, d, _ in pl:
            print(f"  {int(bpm)} bpm  {d/60:4.1f}m  {a} — {t}", file=sys.stderr)

        retempoed_paths = []
        work = args.output_dir / f"{slug}_work"
        work.mkdir(exist_ok=True)
        for i, (a, t, bpm, _d, path) in enumerate(pl, 1):
            out = work / f"{i:02d}_{int(bpm)}to{int(args.target_bpm)}.mp3"
            print(f"  [{i}/{len(pl)}] retempo {bpm}→{int(args.target_bpm)}: {Path(path).name}", file=sys.stderr)
            retempo(Path(path), bpm, args.target_bpm, out)
            retempoed_paths.append(str(out))

        mix_out = args.output_dir / f"{slug}.mp3"
        print(f"  mixing → {mix_out}", file=sys.stderr)
        subprocess.run([str(MIX_SH), "-d", str(args.crossfade), "-o", str(mix_out),
                        *retempoed_paths], check=True)

        try:
            audio = MP3(str(mix_out), ID3=EasyID3)
        except ID3NoHeaderError:
            audio = MP3(str(mix_out), ID3=EasyID3)
            audio.add_tags()
        for key in ("title", "artist", "album", "albumartist",
                    "tracknumber", "date", "genre"):
            if key in audio:
                del audio[key]
        audio["title"] = f"Run Mix {n} — {int(args.target_bpm)} BPM D&B"
        audio["artist"] = "Various"
        audio["album"] = "Run Playlists"
        audio["genre"] = "D&B"
        audio["bpm"] = str(int(args.target_bpm))
        audio.save()

        remaining = [t for t in remaining if t not in pl]
        random.shuffle(remaining)

    print("done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
