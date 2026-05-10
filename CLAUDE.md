# cadence

Tools for building tempo-ramped run playlists. See `README.md` for the pipeline overview.

## Conventions

- **Python:** use `uv` — never `pip` or bare `python3`. `.venv/` is local
- **Scratch:** put temporary output in `tmp/` (gitignored). Never `/tmp`
- **DB:** `music.db` lives at the repo root, gitignored — it's data, regenerable via `scripts/index_music.py`

## Terminology

- **"mix" / "run mix"** → run playlist (`generate_run_playlists.py`)
- **"strides mix" / "mini mix" / "minimix"** → strides workout mix (`generate_strides_workout.py`)

## Current focus

Two active generators:

**Run playlists:** `scripts/generate_run_playlists.py` selects tracks at a target BPM from `music.db`, retempos, mixes with crossfades, wraps as mp4 under `tmp/playlists/<bpm>bpm/<mins>mins/`, and emits a `{slug}_tracklist.txt` sidecar with crossfade-adjusted timestamps for YouTube descriptions. `scripts/extend_playlist_tsvs.py` tops up short tsvs. `scripts/write_tracklists.py` backfills tracklists for already-rendered mixes without re-mixing.

**Strides workout mixes:** `scripts/generate_strides_workout.py` extracts bar-aligned verse phrases from post-intro sections of tracks using `traktor_beatgrid_ms` as the beat anchor, retempos each phrase to the target BPM via rubberband, and crossfades all phrases into a continuous mix. Output goes to `tmp/playlists/<bpm>bpm/strides/`. Key design: cutting at bar boundaries + uniform stretch means all clips land on the same beat phase — grids stay locked across crossfades without nudging. Key CLI flags: `--count` (exact phrases), `--duration-min`, `--phrase-bars` (default 32 ≈ 44s), `--xfade-bars` (default 16 ≈ 22s), `--intro-bars` (default 48, skips intro), `--first-artists`, `--seed`, `--no-video`.

Track selection respects `run_exclude=1` on `tracks`. Exclusions come from: `scripts/analyse_beat_clarity.py` (auto-sets `run_exclude_reason='half-step'` when `groove_delta < 0.05`); and `scripts/reject_track.py <id> "reason"` for manual cuts. `scripts/reject_track.py --review --m3u` exports beat-review candidates as a playlist for audition; `scripts/audit_shipped_playlists.py` reports which shipped-playlist tracks are now excluded. `scripts/probe_beat_clarity.py` is the diagnostic spike — calibrate thresholds before rerunning the batch analyser.

Traktor Pro 3 is a secondary source of BPM / key / beatgrid anchors. Export the collection from Traktor to `collection.nml` at the repo root (gitignored), then `scripts/import_traktor.py` parses it and populates `traktor_bpm`, `traktor_key`, `traktor_beatgrid_ms`, `traktor_imported_at` on `tracks` — additive, does not overwrite existing `bpm`. Also rebuilds `traktor_cues` per track (non-grid CUE_V2 entries — hotcues, fade points, loops; idempotent delete-then-insert). Known gotcha: Traktor's "Automatic" BPM range halftimes many D&B tracks by selecting the lower octave — set the range to `128–255` in *Preferences → Analyze Options* before analysing a D&B-heavy collection.

## Diagnosing bad transitions

When a generated strides mix has a bad transition, the canonical reference is `docs/strides-mix-issues.md` — a 7-issue taxonomy (BPM snap, off-grid anchor, odd-structure intro, phasing-heavy production, phrase-on-breakdown, key clash, monitoring chain) with detect/fix/status per issue. Two diagnostic tools sit alongside:

- `scripts/diag_phrase_waveform.py --search "<artist>:<title>"` renders a waveform with the Traktor beatgrid, the `intro_bars=48` phrase window, and any cues overlaid. `--zoom-start <s> --zoom-dur <s>` for tight inspection of suspect regions; beat ticks (quarter-bar) appear automatically when zoomed under 16s.
- `scripts/diag_grid_phase.py --search "..." --start <s> --dur <s>` runs spectral-flux onset detection and reports each peak's offset to the nearest beat boundary. Watch median offset and drift slope vs the FD baseline of ~–20 ms (the detector's noise floor); offsets significantly outside that range or a consistently drifting slope flag a real grid issue.

Mixing environment for actually hearing transition issues: `docs/traktor-setup.md` documents the wired aggregate device (laptop speakers as master + Z1 headphone as cue, Z1 as clock source). Bluetooth in the monitoring chain corrupts diagnosis — macOS's HFP fallback (16 kHz mono) triggers automatically when a BT device serves both input and output, and that alone makes any mix sound rough.

## Tools

- `ffmpeg` / `ffprobe` — tag extraction, stream inspection, audio conversion
- `aubio` — BPM detection (fallback only; unreliable on breakbeat-led genres)
- `rubberband` / `ffmpeg atempo` — tempo adjustment without pitch change

## External data

BPM values from GetSongBPM.com — attribution required, see `README.md`.
