# Roadmap

## Done

- **BPM ingestion** — `bpm_source` column on `tracks`, Beatport (strict `__NEXT_DATA__` parse + exact artist+title match, half-tempo doubling for DnB) + GetSongBPM lookup, manual-tap residual; aubio dropped from the ingestion path (unreliable on breakbeat). ~892 tracks tagged in DB and written back to file metadata via mutagen (TBPM / tmpo / BPM).
- **Playlist generator** — `scripts/generate_run_playlists.py`: DB selection or `--from-tsv` re-render, artist-varied picking with duration tolerance, parallel retempo, crossfade mix, mp4 wrap with cover image, `<bpm>bpm/<mins>mins/` subfolder output, `--start-n` for numbering extensions without colliding with existing outputs.
- **Playlist extension** — `scripts/extend_playlist_tsvs.py`: tops up a short ramp tsv to a target duration by picking fresh tracks from the same BPM pool, respecting artist variety within the extended list and a global exclusion list of tracks already used in other playlists at that BPM.

## Open

- **Extension smart-fit** — the greedy picker sometimes lands well short of target (e.g. 28m → 33.7m) because any second pick would overshoot the upper tolerance. A two-pass or subset-sum picker that swaps a long pick for two shorter ones when the gap allows would hit target more reliably.
- **Consolidate off-target extension buckets** — extensions can land in 34/35/37/38min folders even when the intent was 36, because the slug derives from the actual total. Options: widen bucket rounding (e.g. nearest 5min), or expose a CLI flag to force the target bucket regardless of actual duration.
- **Lookup coverage** — residual ~41% of the DnB slice under 10min still lacks a trusted BPM source. Prioritise manual tap for frequently-picked artists over adding more scrapers.
