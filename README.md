# running-playlists

Tools for building continuous, tempo-ramped run playlists from an audio file collection.

The pipeline:

1. **Index** a directory of audio files into a sqlite catalogue (`music.db`) via `ffprobe` tag extraction — `scripts/index_music.py`
2. **Pick** tracks by target BPM and genre — SQL against `music.db`
3. **Retempo** each track to an exact target BPM — `scripts/retempo.sh`
4. **Mix** retempoed tracks with crossfades — `scripts/mix.sh`
5. **Wrap** as mp4 with a cover image, ready to upload — `scripts/tovideo.sh`

`scripts/build-run-playlist.sh` orchestrates steps 3–5 from a ramp file (`path target_bpm` per line).

`scripts/generate_run_playlists.py` runs the whole pipeline end-to-end: selects eligible tracks from `music.db` at a target BPM, builds artist-varied playlists targeting a duration, retempos each track in parallel, mixes with crossfades, tags, and wraps as mp4. Outputs land under `tmp/playlists/<bpm>bpm/<mins>mins/`. Supports `--from-tsv` to re-render an existing ramp at a new target BPM without re-selecting.

`scripts/extend_playlist_tsvs.py` tops up a short ramp tsv to a target duration by picking fresh tracks from the same BPM pool — feed the output back into `generate_run_playlists.py --from-tsv` to render the extended mix.

## BPM data

BPM data provided by [GetSongBPM.com](https://getsongbpm.com).

## Layout

```
scripts/       pipeline scripts
music.db       sqlite catalogue (gitignored)
tmp/           scratch output (gitignored)
```
