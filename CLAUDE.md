# running-playlists

Tools for building tempo-ramped run playlists. See `README.md` for the pipeline overview.

## Conventions

- **Python:** use `uv` — never `pip` or bare `python3`. `.venv/` is local
- **Scratch:** put temporary output in `tmp/` (gitignored). Never `/tmp`
- **DB:** `music.db` lives at the repo root, gitignored — it's data, regenerable via `scripts/index_music.py`

## Current focus

Playlist generation is the active layer: `scripts/generate_run_playlists.py` selects tracks at a target BPM from `music.db`, retempos, mixes with crossfades, and wraps as mp4 under `tmp/playlists/<bpm>bpm/<mins>mins/`. `scripts/extend_playlist_tsvs.py` tops up short tsvs. BPM ingestion pipeline is complete — see `ROADMAP.md` for done/open items.

## Tools

- `ffmpeg` / `ffprobe` — tag extraction, stream inspection, audio conversion
- `aubio` — BPM detection (fallback only; unreliable on breakbeat-led genres)
- `rubberband` / `ffmpeg atempo` — tempo adjustment without pitch change

## External data

BPM values from GetSongBPM.com — attribution required, see `README.md`.
