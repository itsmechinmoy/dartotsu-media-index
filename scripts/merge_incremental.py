#!/usr/bin/env python3
"""
merge_incremental.py — daily incremental update.

Fetches entries updated on AniList in the last 48 hours (sorted by
UPDATED_AT_DESC) for both ANIME and MANGA, then merges them into the
existing shards. New IDs get inserted into the correct shard; existing
IDs are updated in-place.

Run after the main weekly build so it stays in sync between rebuilds.
"""

import datetime
import json
import math
import os
import pathlib
import time

import requests

ENDPOINT   = "https://graphql.anilist.co"
SHARD_SIZE = 5000
DATA_DIR   = pathlib.Path("data")
CUTOFF_HRS = 48

QUERY = """
query ($type: MediaType, $page: Int, $isAdult: Boolean) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: $type, sort: UPDATED_AT_DESC, isAdult: $isAdult) {
      id
      idMal
      isAdult
      status
      format
      episodes
      chapters
      nextAiringEpisode { episode timeUntilAiring }
      meanScore
      popularity
      favourites
      countryOfOrigin
      genres
      bannerImage
      coverImage { large extraLarge }
      title { english romaji native userPreferred }
      startDate { year month day }
      endDate   { year month day }
      updatedAt
    }
  }
}
"""

CUTOFF_EPOCH = int(
    (datetime.datetime.utcnow() - datetime.timedelta(hours=CUTOFF_HRS)).timestamp()
)


def build_session() -> requests.Session:
    session = requests.Session()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token   = os.environ.get("ANILIST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        print("Using authenticated session", flush=True)
    session.headers.update(headers)
    return session


def fetch_recent_pass(
    media_type: str,
    is_adult: bool,
    session: requests.Session,
) -> list[dict]:
    out, page = [], 1
    while page <= 100:
        resp = session.post(
            ENDPOINT,
            json={
                "query":     QUERY,
                "variables": {"type": media_type, "page": page, "isAdult": is_adult},
            },
            timeout=30,
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            time.sleep(wait + 1)
            continue
        resp.raise_for_status()
        data        = resp.json()["data"]["Page"]
        batch       = data.get("media", [])
        fresh       = [m for m in batch if (m.get("updatedAt") or 0) >= CUTOFF_EPOCH]
        out.extend(fresh)
        # Stop when records fall older than cutoff or no more pages
        if len(fresh) < len(batch) or not data["pageInfo"]["hasNextPage"]:
            break
        page += 1
        time.sleep(0.75)
    return out


def fetch_recent(media_type: str, session: requests.Session) -> dict[int, dict]:
    non_adult = fetch_recent_pass(media_type, False, session)
    adult     = fetch_recent_pass(media_type, True,  session)
    merged    = {r["id"]: r for r in non_adult}
    merged.update({r["id"]: r for r in adult})
    return merged


def load_shard(path: pathlib.Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["id"]: r for r in json.load(fh)}


def save_shard(path: pathlib.Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, separators=(",", ":"))


def rebuild_title_index(media_type: str) -> None:
    """Regenerate the flat titles file from the current shards."""
    sub       = media_type.lower()
    shard_dir = DATA_DIR / sub
    titles    = []
    for shard_path in sorted(shard_dir.glob("*.json")):
        with shard_path.open(encoding="utf-8") as fh:
            for rec in json.load(fh):
                t = rec.get("title") or {}
                titles.append({
                    "id":      rec["id"],
                    "romaji":  t.get("romaji"),
                    "english": t.get("english"),
                    "native":  t.get("native"),
                })
    title_path = pathlib.Path("search") / f"{sub}_titles.json"
    title_path.parent.mkdir(parents=True, exist_ok=True)
    with title_path.open("w", encoding="utf-8") as fh:
        json.dump(titles, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  rebuilt {title_path}  ({len(titles)} titles)")


def merge(media_type: str, updates: dict[int, dict]) -> None:
    sub        = media_type.lower()
    shard_dir  = DATA_DIR / sub
    shards: dict[int, dict[int, dict]] = {}

    # Load all existing shards
    for f in sorted(shard_dir.glob("*.json")):
        idx        = int(f.stem)
        shards[idx] = load_shard(f)

    touched: set[int] = set()
    for rid, rec in updates.items():
        idx = rid // SHARD_SIZE
        if idx not in shards:
            shards[idx] = {}
        shards[idx][rid] = rec
        touched.add(idx)

    # Only rewrite shards that changed
    for idx in touched:
        path   = shard_dir / f"{idx:04d}.json"
        bucket = shards[idx]
        save_shard(path, sorted(bucket.values(), key=lambda r: r["id"]))
        print(f"  updated shard {path}  ({len(bucket)} entries)")

    rebuild_title_index(media_type)
    print(f"[{media_type}] merged {len(updates)} records across "
          f"{len(touched)} shards")


def update_meta(session: requests.Session) -> None:
    meta_path = DATA_DIR / "meta.json"
    if not meta_path.exists():
        return
    with meta_path.open() as fh:
        meta = json.load(fh)
    meta["last_updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with meta_path.open("w") as fh:
        json.dump(meta, fh, indent=2)


def main():
    session = build_session()

    for mtype in ["ANIME", "MANGA"]:
        print(f"\n=== Incremental update: {mtype} ===", flush=True)
        updates = fetch_recent(mtype, session)
        print(f"  {len(updates)} entries updated in last {CUTOFF_HRS}h")
        if updates:
            merge(mtype, updates)

    update_meta(session)
    print("\nIncremental update complete.")


if __name__ == "__main__":
    main()
