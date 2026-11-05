#!/usr/bin/env python3
"""
fetch_anilist.py — paginate AniList's public GraphQL API and dump raw
media records to NDJSON for downstream processing.

Does two passes per media type:
  Pass 1: isAdult=false  (non-adult entries)
  Pass 2: isAdult=true   (adult-only entries)

Then merges both passes, deduplicating by ID, so the output NDJSON
contains every entry on AniList regardless of content rating.

Token (ANILIST_TOKEN env var) is optional but strongly recommended:
  - Increases rate limit headroom
  - Guarantees adult content is not silently filtered server-side

AniList public rate limit: 90 req/min unauthenticated
"""

import json
import os
import sys
import time
import argparse
import pathlib
import requests

ENDPOINT = "https://graphql.anilist.co"

QUERY = """
query ($type: MediaType, $page: Int, $isAdult: Boolean) {
  Page(page: $page, perPage: 50) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(type: $type, sort: ID, isAdult: $isAdult) {
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
    }
  }
}
"""


def build_session(token: str | None) -> requests.Session:
    session = requests.Session()
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session.headers.update(headers)
    return session


def fetch_page(
    media_type: str,
    page: int,
    is_adult: bool,
    session: requests.Session,
) -> dict:
    for attempt in range(6):
        try:
            resp = session.post(
                ENDPOINT,
                json={
                    "query": QUERY,
                    "variables": {
                        "type":    media_type,
                        "page":    page,
                        "isAdult": is_adult,
                    },
                },
                timeout=30,
            )
            if resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", 60))
                print(f"  [rate-limit] sleeping {retry}s…", flush=True)
                time.sleep(retry + 1)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(data["errors"][0].get("message", "GraphQL error"))
            return data["data"]["Page"]
        except Exception as exc:
            wait = 2 ** attempt
            print(f"  [error] {exc}  — retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Failed after 6 attempts on page {page}")


def fetch_pass(
    media_type: str,
    is_adult: bool,
    session: requests.Session,
) -> dict[int, dict]:
    """Fetch all pages for one (type, isAdult) combination.
    Returns a dict keyed by AniList ID."""
    label  = "ADULT" if is_adult else "NON-ADULT"
    result: dict[int, dict] = {}
    page   = 1

    while True:
        print(f"[{media_type}/{label}] page {page}…", flush=True)
        data        = fetch_page(media_type, page, is_adult, session)
        media_list  = data.get("media", [])

        for item in media_list:
            result[item["id"]] = item

        info = data.get("pageInfo", {})
        print(
            f"  → {len(media_list)} items  "
            f"(cumulative: {len(result)}, "
            f"last page: {info.get('lastPage', '?')})",
            flush=True,
        )

        if not info.get("hasNextPage"):
            break
        page += 1
        time.sleep(0.75)   # stay comfortably under 90 req/min

    return result


def run(media_type: str, out_path: pathlib.Path, token: str | None):
    out_path.mkdir(parents=True, exist_ok=True)
    session = build_session(token)

    print(f"\n=== Fetching {media_type} (non-adult pass) ===", flush=True)
    non_adult = fetch_pass(media_type, False, session)

    print(f"\n=== Fetching {media_type} (adult pass) ===", flush=True)
    adult = fetch_pass(media_type, True, session)

    # Merge — adult pass wins on conflict (fresher data)
    merged = {**non_adult, **adult}
    print(f"\n[{media_type}] merged: {len(non_adult)} non-adult + "
          f"{len(adult)} adult = {len(merged)} unique entries", flush=True)

    out_file = out_path / f"{media_type.lower()}_raw.ndjson"
    with out_file.open("w", encoding="utf-8") as fh:
        for record in sorted(merged.values(), key=lambda r: r["id"]):
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[{media_type}] written → {out_file}  ({len(merged)} records)")


def main():
    p = argparse.ArgumentParser(description="Fetch AniList media to NDJSON")
    p.add_argument("type", choices=["ANIME", "MANGA"], help="Media type to fetch")
    p.add_argument("--out", default="tmp", help="Output directory (default: tmp/)")
    args = p.parse_args()

    token = os.environ.get("ANILIST_TOKEN") or None
    if token:
        print("Token found — using authenticated requests", flush=True)
    else:
        print("No ANILIST_TOKEN env var — using anonymous requests", flush=True)

    run(args.type, pathlib.Path(args.out), token)


if __name__ == "__main__":
    main()
