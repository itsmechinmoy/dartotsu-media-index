# dartotsu-media-index

A static, paginated JSON media catalog seeded from AniList's public GraphQL API.  
Used by [Dartotsu](https://github.com/aayush2622/Dartotsu) as an **offline / rate-limit fallback** when `graphql.anilist.co` is unreachable.

Inspired by [sanilili/media-index](https://github.com/sanilili/media-index) from the Anilili project.

---

## How it works

| Schedule | Action |
|---|---|
| Every Sunday 02:00 UTC | Full rebuild — fetches every anime & manga from AniList from scratch |
| Mon–Sat 04:00 UTC | Incremental update — fetches only entries updated in the last 48 h |

Both passes fetch **adult and non-adult content** (two separate API passes, merged by ID).  
No user-specific data (scores, list status, favourites) is stored — those always come live from the API.

---

## Repository structure

```
data/
  meta.json               ← index metadata (counts, shard info, last_updated)
  anime/
    0000.json             ← AniList IDs 0–4999
    0001.json             ← AniList IDs 5000–9999
    …
  manga/
    0000.json
    …
search/
  anime_titles.json       ← [{id, romaji, english, native}] flat array
  manga_titles.json
scripts/
  fetch_anilist.py        ← paginate AniList GraphQL → NDJSON
  build_index.py          ← NDJSON → JSON shards + title files + meta.json
  merge_incremental.py    ← daily diff merge into existing shards
.github/workflows/
  build_index.yml         ← weekly full rebuild
  update_index.yml        ← daily incremental update
```

---

## Entry schema

Each record matches Dartotsu's internal `Media` model fields:

```json
{
  "id": 1,
  "idMal": 392,
  "isAdult": false,
  "type": "ANIME",
  "status": "FINISHED",
  "format": "TV",
  "episodes": 26,
  "chapters": null,
  "nextAiringEpisode": null,
  "meanScore": 86,
  "popularity": 300000,
  "favourites": 15000,
  "countryOfOrigin": "JP",
  "genres": ["Action", "Adventure", "Drama", "Sci-Fi"],
  "bannerImage": "https://s4.anilist.co/file/…",
  "coverImage": {
    "large": "https://s4.anilist.co/file/…",
    "extraLarge": "https://s4.anilist.co/file/…"
  },
  "title": {
    "english": "Cowboy Bebop",
    "romaji": "Cowboy Bebop",
    "native": "カウボーイビバップ",
    "userPreferred": "Cowboy Bebop"
  },
  "startDate": { "year": 1998, "month": 4, "day": 3 },
  "endDate":   { "year": 1999, "month": 4, "day": 24 }
}
```

---

## Consuming the index

**meta.json** (read this first):
```
https://raw.githubusercontent.com/itsmechinmoy/dartotsu-media-index/main/data/meta.json
```

**A shard** (ID-based lookup — shard = id ÷ 5000):
```
https://raw.githubusercontent.com/itsmechinmoy/dartotsu-media-index/main/data/anime/0000.json
```

**Title search index:**
```
https://raw.githubusercontent.com/itsmechinmoy/dartotsu-media-index/main/search/anime_titles.json
```

---

## Related projects

- [dartotsu_manga_mapper](https://github.com/itsmechinmoy/dartotsu_manga_mapper) — cross-service manga ID mapping
- [dartotsu_image_rust](https://github.com/itsmechinmoy/dartotsu_image_rust) — Rust image processing for Dartotsu
- [Dartotsu](https://github.com/aayush2622/Dartotsu) — the app this index serves
