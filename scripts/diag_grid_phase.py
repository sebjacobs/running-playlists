#!/usr/bin/env python3
# /// script
# dependencies = ["numpy", "soundfile"]
# ///
"""Measure how well Traktor's beatgrid aligns with audio onsets.

For a chosen window of a track, computes a spectral-flux-style onset envelope,
picks peaks, and reports each peak's offset (ms) to the nearest beat boundary
predicted by `traktor_beatgrid_ms` + bpm.

A grid that's truly on-beat → most peaks within ±20ms of zero.
A half-beat-off grid    → cluster around ±half-beat.
A drifting grid          → spread / bimodal.

Usage:
  uv run scripts/diag_grid_phase.py \\
      --search "Blu Mar Ten:All Thoughts" --start 60 --dur 12
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

REPO = Path(__file__).resolve().parent.parent


def load_mono(path: Path, start_s: float, dur_s: float, sr: int) -> np.ndarray:
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path),
         "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}",
         "-ac", "1", "-ar", str(sr), str(tmp)],
        check=True, capture_output=True,
    )
    audio, _ = sf.read(tmp)
    tmp.unlink()
    return audio.astype(np.float32)


def onset_envelope(audio: np.ndarray, sr: int, hop: int = 256, fft: int = 1024):
    """Spectral flux onset envelope. Returns (times_s, flux)."""
    n_frames = 1 + (len(audio) - fft) // hop
    win = np.hanning(fft).astype(np.float32)
    mag = np.empty((n_frames, fft // 2 + 1), dtype=np.float32)
    for i in range(n_frames):
        frame = audio[i * hop: i * hop + fft] * win
        mag[i] = np.abs(np.fft.rfft(frame))
    diff = np.diff(mag, axis=0)
    diff[diff < 0] = 0
    flux = diff.sum(axis=1)
    flux = np.concatenate([[0], flux])
    times = np.arange(n_frames) * hop / sr
    # Normalise
    if flux.max() > 0:
        flux = flux / flux.max()
    return times, flux


def pick_peaks(times, flux, min_gap_s=0.12, threshold=0.25):
    peaks = []
    last_t = -1e9
    for i in range(1, len(flux) - 1):
        if flux[i] >= threshold and flux[i] > flux[i - 1] and flux[i] >= flux[i + 1]:
            if times[i] - last_t >= min_gap_s:
                peaks.append((times[i], flux[i]))
                last_t = times[i]
    return peaks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=REPO / "music.db")
    p.add_argument("--search", required=True, help="artist:title fragment")
    p.add_argument("--start", type=float, required=True, help="window start in source (s)")
    p.add_argument("--dur", type=float, default=12.0)
    p.add_argument("--threshold", type=float, default=0.25)
    p.add_argument("--min-gap", type=float, default=0.12)
    args = p.parse_args()

    artist_q, _, title_q = args.search.partition(":")
    conn = sqlite3.connect(args.db)
    row = conn.execute(
        "SELECT artist, title, bpm, traktor_beatgrid_ms, path "
        "FROM tracks WHERE artist LIKE ? AND title LIKE ? "
        "AND traktor_beatgrid_ms IS NOT NULL LIMIT 1",
        (f"%{artist_q}%", f"%{title_q}%"),
    ).fetchone()
    conn.close()
    if not row:
        print(f"No match for {args.search}", file=sys.stderr)
        return 1
    artist, title, bpm, grid_ms, path = row

    grid_s = grid_ms / 1000.0
    bar_s = 4 * 60 / bpm
    beat_s = bar_s / 4

    sr = 22050
    audio = load_mono(Path(path), args.start, args.dur, sr=sr)
    times, flux = onset_envelope(audio, sr)
    times = times + args.start
    peaks = pick_peaks(times, flux, min_gap_s=args.min_gap, threshold=args.threshold)

    print(f"{artist} — {title}")
    print(f"  bpm={bpm:.3f}  grid={grid_s:.3f}s  bar={bar_s*1000:.1f}ms  beat={beat_s*1000:.1f}ms")
    print(f"  window {args.start:.1f}–{args.start + args.dur:.1f}s, "
          f"{len(peaks)} peaks above {args.threshold}")
    if not peaks:
        print("  (no peaks — try a louder section or lower --threshold)")
        return 0

    print(f"\n  {'peak_t':>8s}  {'beat_off_ms':>11s}  {'beat_phase':>10s}  {'bar_off_ms':>10s}  {'bar_phase':>9s}")
    beat_offs = []
    bar_offs = []
    for pt, _ in peaks:
        # Offset to nearest beat
        n_beats = (pt - grid_s) / beat_s
        nearest = round(n_beats)
        beat_off = (n_beats - nearest) * beat_s * 1000  # ms
        beat_phase = (n_beats - int(np.floor(n_beats))) % 1
        # Offset to nearest bar
        n_bars = (pt - grid_s) / bar_s
        nearest_bar = round(n_bars)
        bar_off = (n_bars - nearest_bar) * bar_s * 1000
        bar_phase = (n_bars - int(np.floor(n_bars))) % 1
        beat_offs.append(beat_off)
        bar_offs.append(bar_off)
        print(f"  {pt:8.3f}  {beat_off:+11.1f}  {beat_phase:10.3f}  {bar_off:+10.1f}  {bar_phase:9.3f}")

    beat_offs = np.array(beat_offs)
    bar_offs = np.array(bar_offs)
    print()
    print(f"  beat-offset:  mean={beat_offs.mean():+.1f}ms  median={np.median(beat_offs):+.1f}ms  "
          f"std={beat_offs.std():.1f}ms  |median|={np.abs(np.median(beat_offs)):.1f}ms")
    print(f"  bar-offset:   mean={bar_offs.mean():+.1f}ms  median={np.median(bar_offs):+.1f}ms  "
          f"std={bar_offs.std():.1f}ms")
    on_beat = np.abs(beat_offs) <= 25
    print(f"  peaks within ±25ms of a beat: {on_beat.sum()}/{len(beat_offs)} "
          f"({100*on_beat.mean():.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
