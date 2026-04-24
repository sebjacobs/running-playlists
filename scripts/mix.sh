#!/usr/bin/env bash
set -euo pipefail

DURATION=4

usage() {
  cat <<EOF
Usage: $(basename "$0") [-d SECONDS] -o OUTPUT INPUT [INPUT ...]

Concatenate tracks into a single continuous mix with crossfades between them.
Input order is preserved — pass files in the order you want to hear them.

  -d SECONDS   crossfade duration (default: ${DURATION})
  -o OUTPUT    output file (e.g. run-mix.mp3)
  -h           show this help
EOF
  exit "${1:-0}"
}

output=""
while getopts ":d:o:h" opt; do
  case $opt in
    d) DURATION=$OPTARG ;;
    o) output=$OPTARG ;;
    h) usage 0 ;;
    *) usage 1 ;;
  esac
done
shift $((OPTIND - 1))

[[ -z "$output" || $# -lt 2 ]] && usage 1
command -v ffmpeg >/dev/null || { echo "missing dependency: ffmpeg" >&2; exit 1; }

inputs=("$@")
n=${#inputs[@]}

ff_inputs=()
for f in "${inputs[@]}"; do
  ff_inputs+=(-i "$f")
done

# Chain pairwise acrossfades: [0][1]->[a1]; [a1][2]->[a2]; ...
filter=""
prev="[0]"
for ((i = 1; i < n; i++)); do
  label="[a${i}]"
  [[ $i -eq $((n - 1)) ]] && label=""
  filter+="${prev}[${i}]acrossfade=d=${DURATION}:c1=tri:c2=tri${label};"
  prev="[a${i}]"
done
filter=${filter%;}

printf 'mixing %d tracks with %ss crossfade → %s\n' "$n" "$DURATION" "$output"
ffmpeg -y "${ff_inputs[@]}" -filter_complex "$filter" "$output"
printf 'wrote %s\n' "$output"
