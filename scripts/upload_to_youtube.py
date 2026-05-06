#!/usr/bin/env python3
"""Upload a generated mix mp4 to YouTube via the youtubeuploader CLI.

Filename convention (set by generate_run_playlists.py / generate_strides_workout.py):
    2026-05-01_run_180bpm_75min_1.mp4
    2026-05-01_strides_180bpm_60min.mp4

Looks up the matching {basename}_tracklist.txt sidecar.
For run mixes: full tracklist is appended to the description.
For strides mixes: tracklist is omitted (exceeds YouTube's 5000-char description limit).

Credentials and config live outside the repo at:
    ~/.config/youtubeuploader/client_secrets.json   (from Google Cloud Console)
    ~/.config/youtubeuploader/request.token         (auto-cached after first OAuth)

All uploads are private + not made-for-kids. Playlist names are derived from BPM/kind
via PLAYLIST_NAMES below — playlists must exist in YouTube already.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "youtubeuploader"
SECRETS_PATH = CONFIG_DIR / "client_secrets.json"
TOKEN_PATH = CONFIG_DIR / "request.token"

YOUTUBE_DESCRIPTION_LIMIT = 5000  # hard cap; we leave headroom

# Playlist naming convention. YouTube playlists are flat — slashes are cosmetic.
# Playlists must be created in the YouTube UI before upload.
PLAYLIST_NAMES = {
    ("run", 175): "Running/Mixes/175BPM",
    ("strides", 175): "Running/Mixes/175BPM/Strides",
    ("run", 180): "Running/Mixes/180BPM",
    ("strides", 180): "Running/Mixes/180BPM/Strides",
}

FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<kind>run|strides)_(?P<bpm>\d+)bpm_(?P<duration>\d+)min(?:_(?P<index>\d+))?$"
)


@dataclass
class MixMeta:
    path: Path
    date: str
    kind: str  # "run" | "strides"
    bpm: int
    duration_min: int
    index: int | None

    @property
    def title(self) -> str:
        # e.g. "175 BPM run mix · 75min #1 (2026-05-01)"
        kind_label = "strides mix" if self.kind == "strides" else "run mix"
        idx = f" #{self.index}" if self.index else ""
        return f"{self.bpm} BPM {kind_label} · {self.duration_min}min{idx} ({self.date})"

    @property
    def tracklist_path(self) -> Path:
        return self.path.with_name(self.path.stem + "_tracklist.txt")

    @property
    def playlist_name(self) -> str | None:
        return PLAYLIST_NAMES.get((self.kind, self.bpm))

    @property
    def tags(self) -> list[str]:
        return ["drum and bass", "running", f"{self.bpm} bpm", self.kind]


def parse_filename(path: Path) -> MixMeta:
    m = FILENAME_RE.match(path.stem)
    if not m:
        raise ValueError(
            f"Filename {path.name!r} does not match expected pattern "
            "YYYY-MM-DD_(run|strides)_<bpm>bpm_<duration>min[_<index>].mp4"
        )
    return MixMeta(
        path=path,
        date=m["date"],
        kind=m["kind"],
        bpm=int(m["bpm"]),
        duration_min=int(m["duration"]),
        index=int(m["index"]) if m["index"] else None,
    )


def build_description(meta: MixMeta) -> str:
    header = f"{meta.bpm} BPM {meta.kind} mix — {meta.duration_min} minutes\nGenerated {meta.date}\n"

    if meta.kind == "strides":
        # Tracklist is too long for YT's 5000-char limit on strides mixes.
        return header + "\nStrides workout mix: bar-aligned phrases at target BPM."

    if not meta.tracklist_path.exists():
        print(f"warning: no tracklist sidecar at {meta.tracklist_path}", file=sys.stderr)
        return header

    tracklist = meta.tracklist_path.read_text()
    description = f"{header}\nTracklist:\n{tracklist}"
    if len(description) > YOUTUBE_DESCRIPTION_LIMIT:
        raise ValueError(
            f"Description exceeds YouTube limit ({len(description)} > {YOUTUBE_DESCRIPTION_LIMIT})"
        )
    return description


def build_metadata(meta: MixMeta) -> dict:
    md: dict = {
        "title": meta.title,
        "description": build_description(meta),
        "tags": meta.tags,
        "privacyStatus": "private",
        "categoryId": "10",  # Music
        "madeForKids": False,
        "recordingDate": meta.date,
    }
    if meta.playlist_name:
        md["playlistTitles"] = [meta.playlist_name]
    else:
        print(
            f"warning: no playlist mapping for kind={meta.kind} bpm={meta.bpm}",
            file=sys.stderr,
        )
    return md


def upload(meta: MixMeta, dry_run: bool) -> None:
    metadata = build_metadata(meta)

    if dry_run:
        print(f"--- would upload: {meta.path} ---")
        print(json.dumps(metadata, indent=2))
        return

    if not SECRETS_PATH.exists():
        sys.exit(
            f"missing OAuth secrets at {SECRETS_PATH}\n"
            "Create OAuth Desktop app credentials in Google Cloud Console "
            "and save the downloaded JSON there."
        )

    if not shutil.which("youtubeuploader"):
        sys.exit("youtubeuploader binary not found on PATH (try: brew install youtubeuploader)")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(metadata, f)
        meta_path = f.name

    try:
        cmd = [
            "youtubeuploader",
            "-filename", str(meta.path),
            "-metaJSON", meta_path,
            "-secrets", str(SECRETS_PATH),
            "-cache", str(TOKEN_PATH),
        ]
        print(f"running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
    finally:
        Path(meta_path).unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mp4", type=Path, nargs="+", help="path(s) to mp4 to upload")
    p.add_argument("--dry-run", action="store_true", help="print metadata without uploading")
    args = p.parse_args()

    for path in args.mp4:
        meta = parse_filename(path)
        upload(meta, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
