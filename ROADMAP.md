# Roadmap

## Populate bpm for the catalogue — lookup first, detect as fallback

The indexer populates bpm for only a small fraction of tracks (mostly those already tagged at source). Aubio detection alone is unreliable for breakbeat-led genres (detected 109.40 for a 175-bpm track — tracks breakbeat, not kick; half-tempo heuristic doesn't cover this case).

- Add `bpm_source` column to `tracks` (values: `lookup`, `detected`, `manual`)
- Lookup step keyed on `artist + title`, precedence **Beatport → GetSongBPM → manual tap**:
  - **Beatport** (primary) — scrape, strong DnB coverage (100% on initial 5-track sample). Publishes half-tempo for DnB; double any returned value below 100 BPM. Rate-limit carefully (~1.5s/req)
  - **GetSongBPM** (fallback) — free API, requires Referer header. Probe shows ~42% coverage on a 5-artist DnB sample with correct (non-half-tempo) values; misses remixes, interludes, and acoustic variants
  - **Manual tap** (final fallback) — for the residual gap after lookups, tap out BPM by hand and record as `bpm_source='manual'`. Faster and more reliable than any detector on breakbeat-led material; never overwritten by subsequent runs
  - ~~aubio~~ — confirmed unreliable on breakbeat (mostly half-tempo or wrong on first 20 DnB tracks). Keeping the probe script for reference but not using it in the ingestion path
  - **Discogs** — still on the list if Beatport+GetSongBPM leaves a bigger gap than expected; BPM coverage patchy
  - ~~Shazam via `shazamio`~~ — `search_track` broken (404/XML); also needs audio not text
  - ~~Spotify `audio_features`~~ — endpoint restricted for new apps since late 2024
  - ~~AcousticBrainz~~ — shut down 2022
- Fall back to aubio only when no API match; cross-check with a second detector (`bpm-tools`, essentia) before trusting
- Never overwrite `manual` values

## Write BPM back to audio file metadata

Once a track's BPM is trusted (lookup or manual), write it to the audio file's tags so the value survives outside `music.db` and is visible in every player. Use **mutagen** for in-place tag updates across formats — no re-encoding:

- MP3 → `TBPM` frame (ID3v2)
- M4A/AAC → `tmpo` atom
- FLAC/Vorbis → `BPM` tag
- OGG/Opus → `BPM` comment

Only write when `bpm_source` is `lookup_beatport`, `lookup_getsongbpm`, or `manual` — never from a detector guess. Skip files that already have a matching tag.

## First lookup target

Prototype the GetSongBPM lookup against a small set of artists with well-catalogued discographies (excluding DJ mixes > 10 min). Confirms coverage and BPM accuracy on a tight genre cluster before rolling out to the full catalogue.
