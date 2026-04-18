# running-playlists

Build tempo-locked, crossfaded running mixes from your own music library, at exactly the BPM you want to run at.

## Why

Running at a consistent cadence is one of the simplest form improvements available — around 175–180 steps per minute is a commonly cited target, though the point is less the absolute number and more staying *stable* at whatever you're working toward. Music at a matched BPM makes this nearly effortless: your feet settle onto the beat and stay there.

Training a specific cadence is progressive. Pick a target — say 175 spm — and work toward it over weeks:

- Week 1–2: run to 172 BPM playlists
- Week 3–4: step up to 173, then 174
- Week 5+: 175 as the new steady state

This only works if the playlist holds tempo *exactly*, for as long as the session needs. Streaming services don't deliver that:

- Their BPM metadata is unreliable — especially on breakbeat-led genres like drum & bass, where beat detectors routinely halve or double the reported tempo
- "Running mixes" shuffle across a loose BPM window rather than locking to a value
- There's no way to re-render the same curated set at a slightly different target

This project reads BPMs from trusted sources (GetSongBPM, Beatport) rather than detectors, retempos each track to an exact target with `rubberband` (tempo change, no pitch shift), and crossfade-mixes the result into a continuous output.

## What you get

A single command produces:

```
tmp/playlists/174bpm/30mins/
  run174_30min_1.mp3
  run174_30min_1.mp4   ← static cover, ready to upload
  run174_30min_2.mp3
  run174_30min_2.mp4
  ...
```

Each mp3 is locked to exactly 174 BPM from first track to last, artist-varied, tagged, and crossfaded.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) — Python tooling (scripts declare their own deps via PEP 723 inline metadata, so `uv run` handles everything)
- `ffmpeg` / `ffprobe` — tag extraction and audio conversion
- `rubberband` — tempo adjustment without pitch change
- `aubio` — optional, BPM detection fallback (unreliable on breakbeat; the ingestion path avoids it)
- A [GetSongBPM API key](https://getsongbpm.com/api) if you want to populate BPMs from their lookup

macOS / Linux. Shell scripts are bash.

## Quick start

```bash
git clone https://github.com/sebjacobs/running-playlists
cd running-playlists

# 1. Index your music library into a sqlite catalogue
uv run scripts/index_music.py ~/Music

# 2. (Optional) populate BPMs from GetSongBPM + Beatport
cp .env.example .env
# edit .env and set BPM_API_KEY

uv run --env-file .env scripts/probe_getsongbpm.py \
  --db music.db --genre "Drum & Bass" --max-duration 600
uv run scripts/probe_beatport_strict.py \
  --db music.db --genre "Drum & Bass"
uv run scripts/apply_bpm.py --db music.db
uv run scripts/write_bpm_tags.py --db music.db

# 3. Generate three 30-minute playlists at 174 BPM
uv run scripts/generate_run_playlists.py \
  --target-bpm 174 --duration-mins 30 --count 3
```

Finished mixes land under `tmp/playlists/174bpm/30mins/`, ready to copy to a phone or upload.

## How it works

1. **Index** a directory of audio files into a sqlite catalogue (`music.db`) via `ffprobe` tag extraction — `scripts/index_music.py`
2. **Populate BPMs** from lookup sources with provenance tracked in the DB — `scripts/probe_*.py`, `scripts/apply_bpm.py`, `scripts/write_bpm_tags.py`
3. **Pick** tracks at the target BPM respecting duration and artist variety — `scripts/generate_run_playlists.py`
4. **Retempo** each track to an exact target BPM using `rubberband` (parallelised per-track)
5. **Mix** retempoed tracks with crossfades — `scripts/mix.sh`
6. **Wrap** the mp3 as mp4 with a static cover image — `scripts/tovideo.sh`

Steps 3–6 run end-to-end from `scripts/generate_run_playlists.py`.

## Script reference

| Script | Purpose |
|---|---|
| `index_music.py` | Scan a directory and build `music.db` from ffprobe tags |
| `probe_getsongbpm.py` | Look up BPMs via the GetSongBPM API |
| `probe_beatport_strict.py` | Scrape BPMs from Beatport (responses cached on disk) |
| `probe_beatport.py` | Legacy greedy-regex Beatport probe, kept for comparison |
| `probe_aubio.py` | Aubio BPM detection baseline (for evaluation only) |
| `compare_bpm.py` | Agreement analysis between BPM sources |
| `apply_bpm.py` | Ingest probe outputs into `music.db` with provenance |
| `write_bpm_tags.py` | Write trusted BPMs back into audio file tags |
| `generate_run_playlists.py` | End-to-end: select, retempo, mix, wrap |
| `extend_playlist_tsvs.py` | Top up a short ramp tsv with extra tracks |
| `retempo.sh`, `mix.sh`, `tovideo.sh` | Lower-level pipeline primitives |
| `build-run-playlist.sh` | Orchestrates retempo/mix/wrap from a ramp file |

### Re-rendering at a different BPM

```bash
uv run scripts/generate_run_playlists.py \
  --from-tsv tmp/playlist_sources/174bpm/30mins/run174_30min_1.tsv \
  --target-bpm 176
```

Preserves the original track list and order; just retempos to a new target.

## Output layout

```
tmp/playlists/<bpm>bpm/<mins>mins/run<bpm>_<mins>min_<n>.{mp3,mp4}   finished mixes
tmp/playlist_sources/<bpm>bpm/<mins>mins/run<bpm>_<mins>min_<n>.tsv  ramp source
tmp/playlist_sources/<bpm>bpm/<mins>mins/_work/                      retempo intermediates
```

## BPM data

BPM data provided by [GetSongBPM.com](https://getsongbpm.com).

## Repo layout

```
assets/        cover image used by mp4 wrap
scripts/       pipeline scripts
music.db       sqlite catalogue (gitignored)
tmp/           scratch output (gitignored)
```
