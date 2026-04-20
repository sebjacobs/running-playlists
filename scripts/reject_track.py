#!/usr/bin/env python3
"""Manually exclude a track from run playlists.

Usage:
  uv run scripts/reject_track.py <id> "reason"
  uv run scripts/reject_track.py --unexclude <id>
  uv run scripts/reject_track.py --list              # show all exclusions
  uv run scripts/reject_track.py --review            # show beat_review_flag candidates
  uv run scripts/reject_track.py --review --m3u tmp/review.m3u   # export as playlist
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "music.db"


def show(conn: sqlite3.Connection, where: str, params: tuple = ()) -> None:
    rows = conn.execute(
        f"SELECT id, artist, title, bpm, run_exclude_reason FROM tracks WHERE {where} "
        f"ORDER BY artist, title",
        params,
    ).fetchall()
    for r in rows:
        tid, artist, title, bpm, reason = r
        bpm_s = f"{bpm:3.0f}" if bpm else "   "
        print(f"  {tid:>5}  {bpm_s}  {artist or '':30.30}  {title or '':40.40}  {reason or ''}")
    print(f"({len(rows)} rows)")


def export_m3u(conn: sqlite3.Connection, where: str, out: Path) -> int:
    rows = conn.execute(
        f"SELECT id, artist, title, duration_s, path FROM tracks WHERE {where} "
        f"ORDER BY artist, title"
    ).fetchall()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for tid, artist, title, duration, path in rows:
            dur = int(duration) if duration else -1
            label = f"{artist or ''} - {title or ''}".strip(" -")
            f.write(f"#EXTINF:{dur},{label}\n")
            f.write(f"# id={tid}\n")
            f.write(f"{path}\n")
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("id", type=int, nargs="?")
    p.add_argument("reason", nargs="?")
    p.add_argument("--unexclude", type=int)
    p.add_argument("--list", action="store_true")
    p.add_argument("--review", action="store_true")
    p.add_argument("--m3u", type=Path, help="write results to an m3u playlist")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH)

    if args.list or args.review:
        where = (
            "run_exclude = 1"
            if args.list
            else "beat_review_flag = 1 AND (run_exclude IS NULL OR run_exclude = 0)"
        )
        if args.m3u:
            n = export_m3u(conn, where, args.m3u)
            print(f"wrote {n} tracks to {args.m3u}")
        else:
            show(conn, where)
        return 0
    if args.unexclude is not None:
        conn.execute(
            "UPDATE tracks SET run_exclude=0, run_exclude_reason=NULL WHERE id=?",
            (args.unexclude,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT artist, title FROM tracks WHERE id=?", (args.unexclude,)
        ).fetchone()
        print(f"unexcluded: {row[0]} — {row[1]}" if row else "id not found")
        return 0

    if args.id is None or args.reason is None:
        p.print_help()
        return 1

    row = conn.execute(
        "SELECT artist, title, run_exclude, run_exclude_reason FROM tracks WHERE id=?",
        (args.id,),
    ).fetchone()
    if not row:
        print(f"id {args.id} not found", file=sys.stderr)
        return 1
    artist, title, cur_excl, cur_reason = row
    conn.execute(
        "UPDATE tracks SET run_exclude=1, run_exclude_reason=? WHERE id=?",
        (args.reason, args.id),
    )
    conn.commit()
    prefix = "updated" if cur_excl else "excluded"
    print(f"{prefix}: {artist} — {title}  ({args.reason})")
    if cur_excl and cur_reason != args.reason:
        print(f"  (was: {cur_reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
