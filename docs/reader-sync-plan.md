# Readwise Reader sync pipeline

## Context

Today this repo holds throwaway scripts (`readwise.py`, `readwise_favorites.sh`) that dump the
Reader API to flat JSON files. They have a silent bug: both pass `tags=favorite`, but the API
parameter is **`tag`** (singular). The unknown parameter is ignored, so every run downloaded the
entire library. `readwise2.json` proves it — `count: 10000`, and all 100 sampled documents have
`tags: {}`.

The goal is a real pipeline: pull tag-filtered documents from Reader, and split each one into
three durable on-disk products — metadata, highlights, and the document content itself — so the
library becomes a browsable, greppable folder rather than one giant JSON blob. Reruns must be
cheap: fetch only what changed since the last run.

## API facts (confirmed against readwise.io/reader_api and /api_deets)

| Fact | Detail |
|---|---|
| Filter parameter | `tag` (singular), up to 5 for AND logic. `tags` is silently ignored. |
| Incremental sync | `updatedAfter` (ISO 8601) on both `/api/v3/list/` and `/api/v2/export/` |
| Rate limit | 20 requests/minute per token on both LIST endpoints → pace at ~3s/request |
| Content | `content` is null unless `withHtmlContent=true`, which adds `html_content` (Readwise's stored snapshot, not the live origin) |
| Highlights | No `parentId` filter exists. `/api/v2/export/` returns highlights already grouped per document, linked to Reader docs by `external_id` |
| Pagination | `nextPageCursor` → pass back as `pageCursor` |

## Layout

```
documents/                 # gitignored
  metadata/<id>__<slug>.json
  highlights/<id>__<slug>.md
  resources/<id>__<slug>.<ext>
  .sync_state.json
reader_sync/
  __init__.py
  api.py         # auth, pacing, retry, cursor pagination
  state.py       # last-run watermarks
  writers.py     # render + write the three products
  pipeline.py    # orchestration
  cli.py         # argparse entry point
```

Package named `reader_sync`, not `readwise`, so it can never shadow a future PyPI `readwise`.

## Module responsibilities

Each module does one thing; each function does one thing (per repo standards).

**`api.py`** — the only module that touches HTTP.
- `get_pages(path, params)` — generator yielding one response page at a time, following
  `nextPageCursor`. Paces requests to stay under 20/min; on HTTP 429 honours the `Retry-After`
  header when present, else exponential backoff. Raises on other non-2xx (fail loudly).
- `iter_documents(tag, updated_after)` — v3 `/list/` with `tag`, `withHtmlContent=true`.
- `iter_highlight_books(updated_after)` — v2 `/export/`.
- Token read once from `READWISE_API_TOKEN`; missing → exit 1 with a clear message.
- Drops `verify=False` from the old script — that disabled TLS verification for no reason.

**`state.py`** — `documents/.sync_state.json`:
```json
{"tag": "favorite",
 "documents_updated_after": "2026-09-13T11:44:25.066203+00:00",
 "highlights_updated_after": "..."}
```
- Watermark is the **max `updated_at` seen in the response**, not wall-clock now — immune to clock
  skew and to documents changing mid-run.
- If the stored `tag` differs from the requested one, the watermark is discarded and a full sync
  runs. Otherwise switching tags would silently return nothing.

**`writers.py`** — pure rendering + file writes, no HTTP.
- `write_metadata(doc)` → `{"queried_at": ..., "resource": {...}, "document": {...full API object}}`.
  `queried_at` satisfies "when this document was queried"; `resource` records which content source
  won (see below).
- `write_highlights(doc, highlights)` → Markdown: title/author/source header, then each highlight
  as a blockquote with its note underneath. Skips documents with zero highlights — no empty files.
- `write_resource(doc)` → see below.
- `slug(title)` via `re` + `unicodedata`; filenames are `<id>__<slug>` so they sort by stable id but
  stay readable. Existence checks glob `<id>__*`, so a retitled document is found, not re-fetched.

**`pipeline.py`** — orchestration only:
1. Load state → resolve `updated_after`.
2. Stream documents for the tag; skip child docs (`parent_id` set) — those are highlights/notes.
3. For each document: write metadata, fetch resource if missing.
4. Stream v2 export once; index highlights by `external_id`; write the ones matching our documents.
5. Persist new watermarks only after a clean run.

**`cli.py`** — `sync` with `--tag` (default `favorite`), `--full` (ignore watermarks),
`--force-resources` (re-download existing), `--no-resources`, `--limit`.

## Resource fetching (the (C) product)

Per your choice: live original first, Readwise snapshot as backstop.

1. `GET doc["source_url"]` — streamed to disk, browser-like User-Agent, timeout.
2. Extension from `Content-Type`, falling back to the URL suffix: `pdf` → `.pdf`,
   `html` → `.html`, else `.bin`.
3. On **any** failure (non-2xx, timeout, connection error, missing `source_url`) write
   `doc["html_content"]` to `.html` instead.
4. Either way record the outcome in the metadata file:
   `{"path": ..., "origin": "source_url" | "readwise_html" | "unavailable", "fetched_at": ...}`.

A single resource failure never aborts the run — but the API layer still fails loudly.

## Incrementality (requirement 3)

Two independent layers, because they answer different questions:

- **API level** — `updatedAfter` watermark cuts how much is fetched at all.
- **File level** — a resource whose file already exists is never re-downloaded. This matters
  because `updated_at` bumps on reading-progress changes; without it, scrolling an article would
  re-download it every run.

Net effect: the second run of an unchanged library makes ~2 API calls and writes nothing.

## Files created / changed / deleted

**Created:** the six `reader_sync/*.py` modules.

**Changed:**
- `.gitignore` — add `documents/`. Resources hold full third-party article text; metadata and
  highlights are personal. Easy to un-ignore later if you want the library versioned.
- `pyproject.toml` — add a `reader-sync` script entry point. No new dependencies: `requests` is
  already declared, everything else is stdlib.

**Deleted** (all untracked, so unrecoverable once removed — the build step will confirm first):
`readwise.py`, `readwise_favorites.sh`, `readwise.json`, `readwise2.json`,
`readwise_archived.json`, `readwise_recent.json`. The four JSON dumps are the broken-filter output
(whole library, not favorites), so they are misleading to keep. `main.py` is left alone.

**Secrets:** the token is read from the environment only and never written to disk; no metadata
field carries it.

## Verification

1. `uv run reader-sync sync --tag favorite --limit 5`
2. **Confirm the tag bug is fixed** — every document in `documents/metadata/` must carry `favorite`
   in its `tags` object. This is the check the old scripts would have failed:
   `jq -r '.document.tags | keys[]' documents/metadata/*.json | sort -u`
3. Inspect one of each product: a metadata JSON (has `queried_at` + `resource.origin`), a
   highlights `.md`, a file in `resources/`.
4. Confirm `resource.origin` shows a mix of `source_url` and `readwise_html` — proves both the live
   fetch and the fallback path execute.
5. **Re-run the same command** — it must report nothing new and write no files. This is the
   incrementality proof.
6. `uv run reader-sync sync --tag favorite --full` — re-processes everything.
7. Unset `READWISE_API_TOKEN` and run — must exit non-zero with a clear message, not a traceback.
8. `/sanity-check`, then journal the work.

## Risks

- **10,000-document library.** A first run with a broad tag is ~100 paginated calls at 3s each
  (~5 min) plus one resource download per document. Start with `--limit` to size it up.
- **Resource fetches are unpaced.** They hit arbitrary third-party hosts, one request per document,
  no rate limiting. Fine at favorites scale; would need throttling for a whole-library run.
- **v2 export is account-wide.** It returns highlights for every Readwise source (Kindle, etc.) and
  is filtered client-side to our document ids. Correct, but the fetch is larger than strictly needed
  — there is no server-side way to scope it to a document set.
