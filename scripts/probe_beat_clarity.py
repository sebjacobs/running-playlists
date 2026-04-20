#!/usr/bin/env python3
"""Spike: classify DnB tracks as full-step vs half-step by groove template.

For each track we:
  1. Lock a beat grid to the tagged BPM.
  2. Split audio into kick (~40-120Hz) and snare (~180-600Hz) bands,
     compute band-limited onset strength.
  3. Fold the onset envelopes onto an 8-slot-per-bar grid
     (beats 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5).
  4. Cosine-match the measured kick+snare profile against two templates:
       full-step:  K=[1,0,0,0,0,1,0,0]  S=[0,0,1,0,0,0,1,0]
       half-step:  K=[1,0,0,0,0,0,0,0]  S=[0,0,0,0,1,0,0,0]
     trying all 4 bar-start rotations for each.

Usage:
    uv run scripts/probe_beat_clarity.py --id ID [--id ID ...] [PATH ...]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import librosa
from scipy.signal import butter, sosfiltfilt

DB_PATH = Path(__file__).resolve().parent.parent / "music.db"
SR = 22050
OFFSET_S = 30.0
DUR_S = 60.0
HOP = 256  # finer hop for better beat-slot resolution

SLOTS_PER_BAR = 8  # eighth-note grid

FULL_K = np.array([1, 0, 0, 0, 0, 1, 0, 0], dtype=float)
FULL_S = np.array([0, 0, 1, 0, 0, 0, 1, 0], dtype=float)
HALF_K = np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=float)
HALF_S = np.array([0, 0, 0, 0, 1, 0, 0, 0], dtype=float)


def bandpass(y: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    sos = butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def slot_profile(env: np.ndarray, beat_frames: np.ndarray) -> np.ndarray | None:
    """Fold onset envelope onto eighth-note-in-bar slots, averaged over bars."""
    if len(beat_frames) < 9:
        return None
    # Build an 8th-note grid: beats + midpoints, interleaved
    half_frames = (beat_frames[:-1] + beat_frames[1:]) / 2.0
    n = len(beat_frames) + len(half_frames)  # = 2*len(beats) - 1
    grid = np.empty(n)
    grid[0::2] = beat_frames
    grid[1::2] = half_frames
    grid = grid.astype(int)
    usable = (len(grid) // 8) * 8
    if usable < 8:
        return None
    grid = grid[:usable]
    # Sum envelope energy in window around each grid point
    win = 3
    vals = np.array([
        env[max(0, i - win) : min(len(env), i + win + 1)].sum() for i in grid
    ])
    bars = vals.reshape(-1, 8)
    profile = bars.mean(axis=0)
    # Normalise
    if profile.max() > 0:
        profile = profile / profile.max()
    return profile


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def best_template_score(
    kick_prof: np.ndarray, snare_prof: np.ndarray,
    tpl_k: np.ndarray, tpl_s: np.ndarray,
) -> tuple[float, int]:
    """Return best cosine score across 4 bar-start rotations, and the shift used."""
    best = -1.0
    best_shift = 0
    for shift in range(8):
        k = np.roll(kick_prof, shift)
        s = np.roll(snare_prof, shift)
        score = 0.5 * cosine(k, tpl_k) + 0.5 * cosine(s, tpl_s)
        if score > best:
            best = score
            best_shift = shift
    return best, best_shift


def analyse(path: str, tagged_bpm: float | None) -> dict:
    y, sr = librosa.load(path, sr=SR, mono=True, offset=OFFSET_S, duration=DUR_S)
    if len(y) < sr * 10:
        return {"error": "too short"}

    # Band-limited signals
    y_kick = bandpass(y, sr, 40.0, 120.0)
    y_snare = bandpass(y, sr, 180.0, 600.0)

    env_kick = librosa.onset.onset_strength(y=y_kick, sr=sr, hop_length=HOP)
    env_snare = librosa.onset.onset_strength(y=y_snare, sr=sr, hop_length=HOP)
    env_full = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)

    # Lock beat grid to tagged BPM
    prior_bpm = tagged_bpm if tagged_bpm else None
    _, beats = librosa.beat.beat_track(
        onset_envelope=env_full, sr=sr, hop_length=HOP,
        start_bpm=prior_bpm or 120.0, tightness=400, units="frames",
    )
    if len(beats) < 9:
        return {"error": "not enough beats"}

    kick_prof = slot_profile(env_kick, beats)
    snare_prof = slot_profile(env_snare, beats)
    if kick_prof is None or snare_prof is None:
        return {"error": "profile failed"}

    full_score, full_shift = best_template_score(kick_prof, snare_prof, FULL_K, FULL_S)
    half_score, half_shift = best_template_score(kick_prof, snare_prof, HALF_K, HALF_S)

    # --- Grid tightness: how close kick-band onsets land to the 8th-note grid.
    # Build the 8th-note grid frames used by slot_profile.
    half_frames = (beats[:-1] + beats[1:]) / 2.0
    grid_frames = np.empty(len(beats) + len(half_frames))
    grid_frames[0::2] = beats
    grid_frames[1::2] = half_frames
    grid_frames = grid_frames.astype(int)

    onsets = librosa.onset.onset_detect(
        onset_envelope=env_kick, sr=sr, hop_length=HOP, units="frames",
    )
    grid_tightness = float("nan")
    if len(onsets) > 4 and len(grid_frames) > 1:
        # Distance from each onset to nearest grid frame, in ms
        diffs = np.abs(onsets[:, None] - grid_frames[None, :]).min(axis=1)
        diff_ms = diffs * HOP / sr * 1000.0
        # Tightness = fraction of onsets within ±30ms of a grid point
        grid_tightness = float((diff_ms < 30.0).mean())

    # --- Kick prominence: peak kick-envelope at expected kick slots
    # divided by median kick envelope.
    # Expected kick slots (after best full-step rotation): slot 0 and slot 5
    shift = full_shift
    kick_slots_in_bar = [(0 - shift) % 8, (5 - shift) % 8]
    # Walk the full 8th-note grid and pick samples at kick-slot positions
    usable = (len(grid_frames) // 8) * 8
    trimmed = grid_frames[:usable].reshape(-1, 8)
    kick_hits = np.concatenate([
        [env_kick[f] for f in trimmed[:, s] if f < len(env_kick)]
        for s in kick_slots_in_bar
    ])
    baseline = float(np.median(env_kick)) + 1e-9
    kick_prominence = (
        float(np.mean(kick_hits) / baseline) if len(kick_hits) else float("nan")
    )

    return {
        "tagged_bpm": tagged_bpm,
        "full": full_score,
        "half": half_score,
        "delta": full_score - half_score,
        "grid_tightness": grid_tightness,
        "kick_prominence": kick_prominence,
        "kick": kick_prof,
        "snare": snare_prof,
    }


def fmt_prof(p: np.ndarray) -> str:
    return " ".join(f"{v:.2f}" for v in p)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, action="append", default=[])
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("paths", nargs="*")
    args = p.parse_args()

    con = sqlite3.connect(DB_PATH)
    targets: list[tuple] = []
    for tid in args.id:
        r = con.execute(
            "SELECT id, artist, title, bpm, path FROM tracks WHERE id = ?", (tid,)
        ).fetchone()
        if r:
            targets.append(r)
    con.close()
    for pth in args.paths:
        targets.append((None, "", Path(pth).stem, None, pth))

    if not targets:
        print("no targets", file=sys.stderr)
        return 1

    header = f"{'id':>5} {'artist':22.22} {'title':30.30} {'bpm':>4} {'full':>5} {'half':>5} {'Δ':>5} {'tight':>5} {'prom':>5} verdict"
    print(header)
    print("-" * len(header))
    for tid, artist, title, bpm, path in targets:
        try:
            m = analyse(path, bpm)
        except Exception as e:  # noqa: BLE001
            print(f"{str(tid or ''):>5} {artist:25.25} {title:35.35}  ERROR: {e}")
            continue
        if "error" in m:
            print(f"{str(tid or ''):>5} {artist:25.25} {title:35.35}  {m['error']}")
            continue
        reasons = []
        if m["delta"] < 0.05:
            reasons.append("half?")
        if m["grid_tightness"] < 0.55:
            reasons.append("off-grid")
        if m["kick_prominence"] < 2.0:
            reasons.append("soft-kick")
        verdict = ",".join(reasons) if reasons else "ok"
        print(
            f"{str(tid or ''):>5} {artist:22.22} {title:30.30} "
            f"{(bpm or 0):4.0f} {m['full']:5.2f} {m['half']:5.2f} {m['delta']:+5.2f} "
            f"{m['grid_tightness']:5.2f} {m['kick_prominence']:5.2f} {verdict}"
        )
        if args.verbose:
            print(f"      kick:  {fmt_prof(m['kick'])}")
            print(f"      snare: {fmt_prof(m['snare'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
