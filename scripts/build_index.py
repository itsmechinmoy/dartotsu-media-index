#!/usr/bin/env python3
"""
build_index.py — consume NDJSON from fetch_anilist.py and write:

  data/anime/NNNN.json        — shards of SHARD_SIZE entries sorted by ID
  data/manga/NNNN.json
  search/anime_titles.json    — flat array [{id, romaji, english, native}]
  search/manga_titles.json
  data/meta.json              — index metadata consumed by Dartotsu
"""

import argparse
import datetime
import json
import math
import pathlib

SHARD_SIZE = 5000
REPO       = "itsmechinmoy/dartotsu-media-index"
BRANCH     = "main"
BASE_URL   = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/data"


def load_ndjson(path: pathlib.Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return sorted(records, key=lambda r: r["id"])


def build(media_type: str, ndjson: pathlib.Path, out: pathlib.Path) -> tuple[int, int]:
    if not ndjson.exists():
        print(f"[WARN] {ndjson} not found — skipping {media_type}")
        return 0, 0

    records    = load_ndjson(ndjson)
    sub        = media_type.lower()
    out_dir    = out / "data" / sub
    search_dir = out / "search"
    out_dir.mkdir(parents=True, exist_ok=True)
    search_dir.mkdir(parents=True, exist_ok=True)

    num_shards = math.ceil(len(records) / SHARD_SIZE)
    titles: list[dict] = []

    for i in range(num_shards):
        chunk      = records[i * SHARD_SIZE : (i + 1) * SHARD_SIZE]
        shard_path = out_dir / f"{i:04d}.json"
        with shard_path.open("w", encoding="utf-8") as fh:
            json.dump(chunk, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"  wrote {shard_path}  ({len(chunk)} entries)")

        for r in chunk:
            t = r.get("title") or {}
            titles.append({
                "id":      r["id"],
                "romaji":  t.get("romaji"),
                "english": t.get("english"),
                "native":  t.get("native"),
            })

    title_path = search_dir / f"{sub}_titles.json"
    with title_path.open("w", encoding="utf-8") as fh:
        json.dump(titles, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote {title_path}  ({len(titles)} titles)")

    return len(records), num_shards


def main():
    p = argparse.ArgumentParser(description="Build AniList media index shards")
    p.add_argument("--tmp", default="tmp", help="Directory containing *_raw.ndjson files")
    p.add_argument("--out", default=".",   help="Repo root to write data/ and search/ into")
    args = p.parse_args()

    tmp = pathlib.Path(args.tmp)
    out = pathlib.Path(args.out)

    print("\n=== Building ANIME index ===")
    anime_count, anime_shards = build("ANIME", tmp / "anime_raw.ndjson", out)

    print("\n=== Building MANGA index ===")
    manga_count, manga_shards = build("MANGA", tmp / "manga_raw.ndjson", out)

    meta = {
        "schema_version": 1,
        "last_updated":   datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "anime_count":    anime_count,
        "manga_count":    manga_count,
        "anime_shards":   anime_shards,
        "manga_shards":   manga_shards,
        "shard_size":     SHARD_SIZE,
        "base_url":       BASE_URL,
    }
    meta_path = out / "data" / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_path.open("w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"\nwrote {meta_path}")
    print(f"\nDone — {anime_count} anime across {anime_shards} shards, "
          f"{manga_count} manga across {manga_shards} shards")


if __name__ == "__main__":
    main()
