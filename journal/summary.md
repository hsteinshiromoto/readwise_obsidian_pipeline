# Summary

Running record of the changes in this repository.

## 2026-09-13

Built `reader_sync`, a pipeline that downloads tagged Readwise Reader documents and writes each one
as metadata JSON, highlights Markdown, and the document content. Runs are incremental through a
saved watermark plus a file-existence check. Content comes from `source_url`, and falls back to the
copy Readwise stored.

Found that the earlier scripts filtered on `tags` instead of `tag`, so they had been downloading the
whole library rather than the favorites. Deleted those scripts and their output.

Details: [2026-09-13.md](2026-09-13.md)
