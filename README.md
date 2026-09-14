# reader-sync

Download the Readwise Reader documents that carry a tag. Write each one to disk as three files.

| Folder | Format | Content |
| --- | --- | --- |
| `documents/metadata` | JSON | The API response for the document, and the time of the query. |
| `documents/highlights` | Markdown | The highlights and notes for the document. |
| `documents/resources` | As served | The document content. |

The pipeline downloads only what is missing. A second run of an unchanged library writes nothing.

## Setup

Get a token from <https://readwise.io/access_token>. Put it in the environment:

```sh
export READWISE_API_TOKEN=...
```

## Usage

Sync every document tagged `favorite`:

```sh
make sync
```

The same command with the module interface:

```sh
uv run python -m reader_sync sync
```

### Options

| Flag | Effect |
| --- | --- |
| `--tag NAME` | Filter on this tag. The default is `favorite`. |
| `--root PATH` | Write to this folder. The default is `documents`. |
| `--full` | Ignore the saved position and fetch everything again. |
| `--limit N` | Stop after N documents. Use this to size up a large run. |
| `--no-resources` | Write metadata and highlights only. |
| `--force-resources` | Download content again even when the file exists. |

The command prints one line per document to standard error. It prints a summary to standard output. It prints nothing when nothing changed.

## Where the content comes from

The pipeline asks the original host first. It requests `source_url` and streams the response to `documents/resources`.

Links rot, hosts time out and paywalls appear. On any such failure the pipeline falls back to the copy that Readwise stored when it saved the document. The metadata file records which source won:

```json
"resource": { "file": "01abc__deep-work.pdf", "origin": "source_url" }
```

`origin` is one of `source_url`, `readwise_html`, `cached` or `unavailable`.

## How repeat runs stay cheap

Two separate mechanisms limit the work.

1. The API watermark limits what the pipeline fetches. Each run saves the newest `updated_at` it saw to `documents/.sync_state.json`. The next run sends that value as `updatedAfter`.
2. An existing resource file stops a second download. Readwise bumps `updated_at` when reading progress changes. Without this check, scrolling an article would download it again.

Two cases discard the watermark on purpose:

- A run with a different `--tag`. The old position would hide documents that the new tag returns.
- A run that finds a document for the first time. A document tagged today can hold highlights made a year ago, and an incremental highlight fetch would miss them.

## Tests

```sh
make test
```

This runs the docstring examples of every module. It needs no packages beyond the standard library.

## Notes

- The API parameter for tags is `tag`, not `tags`. Readwise ignores the plural form without an error and returns the whole library.
- Both list endpoints allow 20 requests per minute. The pipeline waits 3 seconds between requests and honors the `Retry-After` header.
- `documents/` stays out of git. It holds personal notes and third-party article text.
