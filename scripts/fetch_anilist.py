#!/usr/bin/env python3
"""
fetch_anilist.py — paginate AniList's public GraphQL API and dump raw
media records to NDJSON for downstream processing.

NOTE ON ANILIST PAGINATION LIMIT:
AniList enforces a hard limit of page 100 (5,000 items) on any single
query. Crossing page 100 triggers HTTP 400 "Page depth exceeds maximum
allowed for API requests".

To bypass this without missing titles, this script partitions the catalog
by broadcast/release date ranges (years for anime, years/quarters for manga),
where no single partition approaches 5,000 titles. Each partition pages
to exhaustion, plus a sweep for unscheduled titles (status: NOT_YET_RELEASED).

Both adult and non-adult content are scraped and deduplicated by ID.
"""

import argparse
import datetime
import json
import os
import pathlib
import sys
import time

import requests

ENDPOINT = "https://graphql.anilist.co"
MAX_PAGE = 100

QUERY = """
query (
  $type: MediaType
  $page: Int
  $isAdult: Boolean
  $greater: FuzzyDateInt
  $lesser: FuzzyDateInt
  $status: MediaStatus
) {
  Page(page: $page, perPage: 50) {
    pageInfo { total currentPage lastPage hasNextPage }
    media(
      type: $type
      sort: ID
      isAdult: $isAdult
      startDate_greater: $greater
      startDate_lesser: $lesser
      status: $status
    ) {
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


class AniListFetcher:
    def __init__(self, token: str | None = None):
        self.session = requests.Session()
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "Dartotsu-media-index/1.0",
            "Referer":      "https://anilist.co",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            print("Using authenticated AniList session.", flush=True)
        else:
            print("Using unauthenticated AniList session.", flush=True)
        self.session.headers.update(headers)

        self.last_req_time = 0.0
        self.min_interval = 0.7  # stay well under 90 req/min
        self.remaining: int | None = None
        self.reset_at: float = 0.0

    def _pace(self):
        elapsed = time.time() - self.last_req_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        if self.remaining is not None and self.remaining <= 5:
            now = time.time()
            if self.reset_at > now:
                wait = self.reset_at - now + 1
                print(f"  [quota-low] {self.remaining} remaining, sleeping {wait:.1f}s to reset", flush=True)
                time.sleep(wait)

    def fetch_page(self, variables: dict) -> dict:
        for attempt in range(6):
            self._pace()
            try:
                resp = self.session.post(
                    ENDPOINT,
                    json={"query": QUERY, "variables": variables},
                    timeout=35,
                )
                self.last_req_time = time.time()

                rem = resp.headers.get("X-RateLimit-Remaining")
                if rem is not None:
                    self.remaining = int(rem)
                rst = resp.headers.get("X-RateLimit-Reset")
                if rst is not None:
                    self.reset_at = float(rst)

                if resp.status_code == 429:
                    retry = float(resp.headers.get("Retry-After", 60)) + 1
                    print(f"  [rate-limit 429] sleeping {retry:.0f}s…", flush=True)
                    time.sleep(retry)
                    continue

                if resp.status_code >= 500:
                    wait = 2 * (attempt + 1)
                    print(f"  [server error {resp.status_code}] retrying in {wait}s…", flush=True)
                    time.sleep(wait)
                    continue

                if resp.status_code == 400:
                    raise RuntimeError(f"AniList rejected query (400): {resp.text[:400]}")

                resp.raise_for_status()
                data = resp.json()
                if "errors" in data and data["errors"]:
                    raise RuntimeError(f"AniList GraphQL error: {data['errors']}")
                return data["data"]["Page"]

            except requests.RequestException as exc:
                wait = 2 ** attempt
                print(f"  [network error] {exc} — retrying in {wait}s", flush=True)
                time.sleep(wait)

        raise RuntimeError(f"Failed to fetch after 6 attempts: {variables}")


def get_partitions(media_type: str) -> list[tuple[str, dict]]:
    current_year = datetime.datetime.now(datetime.timezone.utc).year
    partitions: list[tuple[str, dict]] = []

    if media_type == "ANIME":
        # Anime by year from 1950 to current_year + 2
        for y in range(1950, current_year + 3):
            partitions.append((
                f"{y}",
                {"greater": y * 10000, "lesser": y * 10000 + 1232}
            ))
    else:
        # Manga: older years by year; modern years (>= 2006) by quarter to stay safely under 100 pages
        for y in range(1950, 2006):
            partitions.append((
                f"{y}",
                {"greater": y * 10000, "lesser": y * 10000 + 1232}
            ))
        for y in range(2006, current_year + 3):
            quarters = [
                ("Q1", y * 10000 + 100,  y * 10000 + 332),
                ("Q2", y * 10000 + 400,  y * 10000 + 632),
                ("Q3", y * 10000 + 700,  y * 10000 + 932),
                ("Q4", y * 10000 + 1000, y * 10000 + 1232),
            ]
            for q_name, g, l in quarters:
                partitions.append((f"{y}-{q_name}", {"greater": g, "lesser": l}))

    # Sweep unscheduled announcements with no start date
    partitions.append(("unscheduled", {"status": "NOT_YET_RELEASED"}))
    return partitions


def run(media_type: str, out_path: pathlib.Path, token: str | None):
    out_path.mkdir(parents=True, exist_ok=True)
    fetcher = AniListFetcher(token)
    partitions = get_partitions(media_type)

    by_id: dict[int, dict] = {}

    for is_adult in [False, True]:
        label_rating = "ADULT" if is_adult else "NON-ADULT"
        print(f"\n==========================================", flush=True)
        print(f"Starting {media_type} ({label_rating})", flush=True)
        print(f"Total partitions: {len(partitions)}", flush=True)
        print(f"==========================================", flush=True)

        for p_label, p_vars in partitions:
            page = 1
            part_added = 0
            while page <= MAX_PAGE:
                vars_dict = {
                    "type": media_type,
                    "page": page,
                    "isAdult": is_adult,
                    **p_vars,
                }
                data = fetcher.fetch_page(vars_dict)
                media_list = data.get("media") or []

                for item in media_list:
                    if item["id"] not in by_id:
                        part_added += 1
                    by_id[item["id"]] = item

                page_info = data.get("pageInfo") or {}
                if not page_info.get("hasNextPage") or len(media_list) == 0:
                    break

                page += 1

            if page > MAX_PAGE:
                print(f"  [WARN] Partition {p_label} hit max page {MAX_PAGE} cap!", flush=True)

            if part_added > 0:
                print(f"  [{p_label} {label_rating}] +{part_added} items (total: {len(by_id)})", flush=True)

    out_file = out_path / f"{media_type.lower()}_raw.ndjson"
    print(f"\nWriting {len(by_id)} sorted records to {out_file}…", flush=True)
    with out_file.open("w", encoding="utf-8") as fh:
        for rec in sorted(by_id.values(), key=lambda r: r["id"]):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[{media_type}] Completed successfully: {len(by_id)} records written.")


def main():
    p = argparse.ArgumentParser(description="Fetch AniList media to NDJSON")
    p.add_argument("type", choices=["ANIME", "MANGA"], help="Media type to fetch")
    p.add_argument("--out", default="tmp", help="Output directory (default: tmp/)")
    args = p.parse_args()

    token = os.environ.get("ANILIST_TOKEN") or None
    run(args.type, pathlib.Path(args.out), token)


if __name__ == "__main__":
    main()
