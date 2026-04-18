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
import concurrent.futures
import os
import random
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

REPO = Path(__file__).resolve().parent.parent
MIX_SH = REPO / "scripts" / "mix.sh"
TOVIDEO_SH = REPO / "scripts" / "tovideo.sh"
DEFAULT_COVER = REPO / "assets" / "cover.png"


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


def read_tsv(tsv: Path) -> tuple[list[tuple], float]:
    """Parse a ramp tsv written by this script. Returns (pl_tuples, total_s).

    Total duration is parsed from the header comment if present; otherwise 0.
    """
    pl: list[tuple] = []
    total_s = 0.0
    for line in tsv.read_text().splitlines():
        if line.startswith("#"):
            m = re.search(r"duration=([\d.]+)m", line)
            if m:
                total_s = float(m.group(1)) * 60
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        path, src_bpm = parts[0], float(parts[1])
        pl.append(("", "", src_bpm, 0, path))
    return pl, total_s


def render_playlist(n: int, pl: list[tuple], total_s: float, slug: str,
                    dur_min: int, args, video_executor, video_futures) -> None:
    bucket = Path(f"{int(args.target_bpm)}bpm") / f"{dur_min}mins"
    exports_dir = args.output_dir / bucket
    sources_dir = args.sources_dir / bucket
    exports_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)

    ramp_path = sources_dir / f"{slug}.tsv"
    with ramp_path.open("w") as f:
        f.write(f"# target_bpm={int(args.target_bpm)}, duration={total_s/60:.1f}m\n")
        for _a, _t, bpm, _d, path in pl:
            f.write(f"{path}\t{bpm}\t{int(args.target_bpm)}\n")
    print(f"\n=== Playlist {n} → {slug}.mp3 ({total_s/60:.1f} min) ===", file=sys.stderr)
    for a, t, bpm, d, path in pl:
        label = f"{a} — {t}" if (a or t) else Path(path).name
        print(f"  {int(bpm)} bpm  {d/60:4.1f}m  {label}", file=sys.stderr)

    work = sources_dir / f"{slug}_work"
    work.mkdir(exist_ok=True)
    jobs = []
    for i, (_a, _t, bpm, _d, path) in enumerate(pl, 1):
        out = work / f"{i:02d}_{int(bpm)}to{int(args.target_bpm)}.mp3"
        jobs.append((i, bpm, Path(path), out))

    retempoed_paths: list[str | None] = [None] * len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(retempo, src, bpm, args.target_bpm, out): (i, bpm, src, out)
            for (i, bpm, src, out) in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            i, bpm, src, out = futures[fut]
            fut.result()
            print(f"  [{i}/{len(jobs)}] retempo {bpm}→{int(args.target_bpm)}: {src.name}",
                  file=sys.stderr)
            retempoed_paths[i - 1] = str(out)

    mix_out = exports_dir / f"{slug}.mp3"
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

    if not args.no_video:
        if not args.cover.exists():
            print(f"  cover not found at {args.cover} — skipping video", file=sys.stderr)
        else:
            def wrap_video(mix_out: Path, video_out: Path, cover: Path) -> None:
                print(f"  wrapping → {video_out}", file=sys.stderr)
                subprocess.run([str(TOVIDEO_SH), "-i", str(cover),
                                "-a", str(mix_out), "-o", str(video_out)], check=True)
            video_out = exports_dir / f"{slug}.mp4"
            video_futures.append(video_executor.submit(
                wrap_video, mix_out, video_out, args.cover))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path,
                   help="music.db (required unless --from-tsv is used)")
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
    p.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2),
                   help="parallel retempo workers (default: half of cpu count)")
    p.add_argument("--output-dir", type=Path, default=REPO / "tmp" / "playlists",
                   help="where finished mp3/mp4 deliverables land")
    p.add_argument("--sources-dir", type=Path, default=None,
                   help="where ramp tsvs and _work retempo dirs land "
                        "(default: sibling 'playlist_sources' of --output-dir)")
    p.add_argument("--cover", type=Path, default=DEFAULT_COVER,
                   help=f"cover image for mp4 wrap (default: {DEFAULT_COVER.relative_to(REPO)})")
    p.add_argument("--no-video", action="store_true",
                   help="skip wrapping the mp3 into an mp4")
    p.add_argument("--from-tsv", type=Path, nargs="+",
                   help="re-render existing ramp tsv(s) at --target-bpm instead of "
                        "selecting fresh tracks from --db")
    p.add_argument("--start-n", type=int, default=1,
                   help="number the first output playlist as N (default: 1)")
    args = p.parse_args()

    if not args.from_tsv and not args.db:
        p.error("--db is required unless --from-tsv is supplied")

    if args.sources_dir is None:
        args.sources_dir = args.output_dir.parent / "playlist_sources"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.sources_dir.mkdir(parents=True, exist_ok=True)
    video_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    video_futures: list[concurrent.futures.Future] = []

    if args.from_tsv:
        for n, tsv in enumerate(args.from_tsv, args.start_n):
            pl, total_s = read_tsv(tsv)
            if not pl:
                print(f"{tsv}: empty — skipping", file=sys.stderr)
                continue
            dur_min = int(round((total_s or args.duration_min * 60) / 60))
            slug = f"run_{int(args.target_bpm)}bpm_{dur_min}min_{n}"
            render_playlist(n, pl, total_s, slug, dur_min, args, video_executor, video_futures)
    else:
        random.seed(args.seed)
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

        for n in range(args.start_n, args.start_n + args.count):
            pl, total = build_playlist(remaining, target_s, args.tolerance_s)
            if not pl:
                print(f"playlist {n}: no tracks left — stopping", file=sys.stderr)
                break
            dur_min = int(round(args.duration_min))
            slug = f"run_{int(args.target_bpm)}bpm_{dur_min}min_{n}"
            render_playlist(n, pl, total, slug, dur_min, args, video_executor, video_futures)
            remaining = [t for t in remaining if t not in pl]
            random.shuffle(remaining)

    for fut in video_futures:
        fut.result()
    video_executor.shutdown(wait=True)

    print("done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
