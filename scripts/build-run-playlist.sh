#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUNNING_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
TMP_DIR="$RUNNING_DIR/tmp/run-playlist"
CROSSFADE=4

usage() {
  cat <<EOF
Usage: $(basename "$0") -r RAMP -i COVER -o OUTPUT [-d CROSSFADE_SECONDS] [-b BPM] [-m MIN]

Build a continuous run playlist from a ramp file: retempo each track, mix with
crossfades, wrap as mp4 ready to upload to YouTube.

Ramp file format — one track per line, "path target_bpm":
    track1.mp3 172
    track2.mp3 174
    track3.mp3 178
  # blank lines and lines starting with # are ignored

  -r RAMP      ramp file
  -i COVER     cover image for the video
  -o OUTPUT    output mp4 path
  -d SECONDS   crossfade duration (default: ${CROSSFADE})
  -b BPM       overlay BPM badge on cover (passed to tovideo.sh)
  -m MIN       overlay runtime badge on cover (passed to tovideo.sh)
  -h           show this help
EOF
  exit "${1:-0}"
}

ramp=""
cover=""
output=""
bpm=""
mins=""
while getopts ":r:i:o:d:b:m:h" opt; do
  case $opt in
    r) ramp=$OPTARG ;;
    i) cover=$OPTARG ;;
    o) output=$OPTARG ;;
    d) CROSSFADE=$OPTARG ;;
    b) bpm=$OPTARG ;;
    m) mins=$OPTARG ;;
    h) usage 0 ;;
    *) usage 1 ;;
  esac
done

[[ -z "$ramp" || -z "$cover" || -z "$output" ]] && usage 1
[[ -f "$ramp" ]] || { echo "ramp file not found: $ramp" >&2; exit 1; }
[[ -f "$cover" ]] || { echo "cover not found: $cover" >&2; exit 1; }

mkdir -p "$TMP_DIR"

retempoed=()
skipped=()
lineno=0
while IFS= read -r line || [[ -n "$line" ]]; do
  lineno=$((lineno + 1))
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue

  track=$(echo "$line" | awk '{print $1}')
  target=$(echo "$line" | awk '{print $2}')
  [[ -z "$track" || -z "$target" ]] && { echo "ramp line $lineno malformed: $line" >&2; exit 1; }
  [[ -f "$track" ]] || { echo "track not found (line $lineno): $track" >&2; exit 1; }

  ext="${track##*.}"
  base=$(basename "${track%.*}")
  out="$TMP_DIR/${base}.${target}bpm.${ext}"

  printf '\n[%d] %s → %s bpm\n' "$lineno" "$track" "$target"
  if "$SCRIPT_DIR/retempo.sh" -t "$target" "$track" "$out"; then
    retempoed+=("$out")
  else
    rc=$?
    if [[ $rc -eq 2 ]]; then
      printf '  skipped (source bpm out of range)\n' >&2
      skipped+=("$track")
    else
      echo "retempo failed for $track" >&2
      exit $rc
    fi
  fi
done < "$ramp"

if [[ ${#retempoed[@]} -lt 2 ]]; then
  echo "need at least 2 tracks after retempo; got ${#retempoed[@]}" >&2
  exit 1
fi

mix="$TMP_DIR/mix.mp3"
printf '\nmixing %d tracks\n' "${#retempoed[@]}"
"$SCRIPT_DIR/mix.sh" -d "$CROSSFADE" -o "$mix" "${retempoed[@]}"

printf '\nwrapping as video\n'
overlay_args=()
[[ -n "$bpm" ]] && overlay_args+=(-b "$bpm")
[[ -n "$mins" ]] && overlay_args+=(-d "$mins")
"$SCRIPT_DIR/tovideo.sh" -i "$cover" -a "$mix" -o "$output" "${overlay_args[@]}"

printf '\ndone → %s\n' "$output"
if [[ ${#skipped[@]} -gt 0 ]]; then
  printf 'skipped %d tracks (out of bpm range):\n' "${#skipped[@]}"
  printf '  %s\n' "${skipped[@]}"
fi
