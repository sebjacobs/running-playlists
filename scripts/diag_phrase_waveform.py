#!/usr/bin/env python3
# /// script
# dependencies = ["numpy", "matplotlib", "soundfile"]
# ///
"""Render waveform PNGs with Traktor beatgrid + phrase window overlaid.

Usage:
  uv run scripts/diag_phrase_waveform.py \\
      --track-id-or-search "Blu Mar Ten:All Thoughts" \\
      --intro-bars 48 --phrase-bars 32 --pad-bars 8 \\
      --out tmp/diag/blumarten.png
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "music.db"


def load_audio(src: Path, start_s: float, dur_s: float, sr: int = 22050) -> np.ndarray:
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}",
         "-ac", "1", "-ar", str(sr), str(tmp)],
        check=True, capture_output=True,
    )
    audio, _ = sf.read(tmp)
    tmp.unlink()
    return audio


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--search", required=True,
                   help="artist:title fragment, e.g. 'Blu Mar Ten:All Thoughts'")
    p.add_argument("--intro-bars", type=int, default=48)
    p.add_argument("--phrase-bars", type=int, default=32)
    p.add_argument("--pad-bars", type=int, default=8,
                   help="bars to render before phrase start and after phrase end")
    p.add_argument("--zoom-start", type=float, default=None,
                   help="override window start (seconds in source track)")
    p.add_argument("--zoom-dur", type=float, default=None,
                   help="override window duration in seconds (use with --zoom-start)")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    artist_q, _, title_q = args.search.partition(":")
    conn = sqlite3.connect(args.db)
    row = conn.execute(
        "SELECT id, artist, title, bpm, traktor_beatgrid_ms, path, duration_s "
        "FROM tracks WHERE artist LIKE ? AND title LIKE ? "
        "AND traktor_beatgrid_ms IS NOT NULL LIMIT 1",
        (f"%{artist_q}%", f"%{title_q}%"),
    ).fetchone()
    if not row:
        print(f"No match for {args.search}", file=sys.stderr)
        conn.close()
        return 1
    track_id, artist, title, bpm, grid_ms, path, duration_s = row
    cue_rows = conn.execute(
        "SELECT hotcue, type, type_label, name, start_ms, len_ms "
        "FROM traktor_cues WHERE track_id = ? ORDER BY start_ms",
        (track_id,),
    ).fetchall()
    conn.close()

    bar_s = 4 * 60 / bpm
    grid_s = grid_ms / 1000.0
    phrase_start = grid_s + args.intro_bars * bar_s
    phrase_end = phrase_start + args.phrase_bars * bar_s

    if args.zoom_start is not None and args.zoom_dur is not None:
        win_start = max(0.0, args.zoom_start)
        win_end = min(duration_s, args.zoom_start + args.zoom_dur)
    else:
        win_start = max(0.0, phrase_start - args.pad_bars * bar_s)
        win_end = min(duration_s, phrase_end + args.pad_bars * bar_s)
    win_dur = win_end - win_start

    sr = 22050
    audio = load_audio(Path(path), win_start, win_dur, sr=sr)
    t = np.arange(len(audio)) / sr + win_start

    # Compute RMS envelope (10ms window) for clearer visual
    win_n = int(0.010 * sr)
    rms = np.sqrt(np.convolve(audio.astype(np.float64) ** 2,
                              np.ones(win_n) / win_n, mode="same"))

    fig, ax = plt.subplots(figsize=(20, 4.5))
    ax.plot(t, audio, color="#888", lw=0.4, alpha=0.7, label="waveform")
    ax.plot(t, rms, color="#1a1a1a", lw=0.9, label="RMS")
    ax.plot(t, -rms, color="#1a1a1a", lw=0.9)

    # Beatgrid: bar lines from grid anchor extending across window.
    # Beat ticks (quarter-bar) shown only when zoomed enough to read them.
    beat_s = bar_s / 4
    first_bar_idx = int(np.floor((win_start - grid_s) / bar_s))
    last_bar_idx = int(np.ceil((win_end - grid_s) / bar_s))
    show_beats = win_dur <= 16.0
    for i in range(first_bar_idx, last_bar_idx + 1):
        bar_t = grid_s + i * bar_s
        if win_start <= bar_t <= win_end:
            is_phrase4 = i % 4 == 0
            is_phrase16 = i % 16 == 0
            color = "#cc0000" if is_phrase16 else ("#ff8800" if is_phrase4 else "#ffd0a0")
            lw = 1.4 if is_phrase16 else (0.9 if is_phrase4 else 0.5)
            ax.axvline(bar_t, color=color, lw=lw, alpha=0.8, zorder=0)
        if show_beats:
            for b in range(1, 4):
                bt = bar_t + b * beat_s
                if win_start <= bt <= win_end:
                    ax.axvline(bt, color="#aaaaaa", lw=0.4, alpha=0.5, zorder=0, ls=":")

    # Phrase window shading
    ax.axvspan(phrase_start, phrase_end, color="#3399ff", alpha=0.12,
               label=f"phrase ({args.phrase_bars} bars)")
    ax.axvline(phrase_start, color="#0066cc", lw=2, label="phrase start")
    ax.axvline(phrase_end, color="#0066cc", lw=2, ls="--", label="phrase end")
    ax.axvline(grid_s, color="#009933", lw=1.5, ls=":", label=f"grid anchor ({grid_s:.2f}s)")

    # Hotcues / fade points / loops — anything in traktor_cues for this track
    cue_palette = {
        "cue": "#9933cc", "fade_in": "#33aa33", "fade_out": "#aa3333",
        "load": "#666666", "loop": "#cc6600",
    }
    seen_labels: set[str] = set()
    for hot, ctype, label, name, start_ms, len_ms in cue_rows:
        ct = start_ms / 1000.0
        if not (win_start <= ct <= win_end):
            continue
        color = cue_palette.get(label, "#cc00cc")
        legend_label = label or f"type{ctype}"
        kw = {"color": color, "lw": 1.6, "alpha": 0.9}
        if legend_label not in seen_labels:
            kw["label"] = f"cue: {legend_label}"
            seen_labels.add(legend_label)
        ax.axvline(ct, **kw)
        tag = name or legend_label
        if hot is not None and hot >= 0:
            tag = f"H{hot} {tag}"
        ax.annotate(tag, xy=(ct, 0.95), xytext=(2, 0), textcoords="offset points",
                    fontsize=7, color=color, rotation=90, va="top")
        if len_ms and len_ms > 0:
            ax.axvspan(ct, ct + len_ms / 1000.0, color=color, alpha=0.08)

    ax.set_xlim(win_start, win_end)
    ax.set_ylim(-1.05, 1.05)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("amp")
    ax.set_title(
        f"{artist} — {title}  ({bpm:.1f} bpm, bar={bar_s*1000:.0f}ms)\n"
        f"grid={grid_s:.3f}s  intro_bars={args.intro_bars} → phrase={phrase_start:.2f}s to {phrase_end:.2f}s\n"
        f"red=16-bar | orange=4-bar | tan=1-bar"
    )
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(False)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(args.out, dpi=140)
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
