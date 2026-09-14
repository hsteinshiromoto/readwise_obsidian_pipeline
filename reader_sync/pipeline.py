"""Orchestrates one sync run: documents in, three products out."""

import sys
from pathlib import Path
from typing import Optional

from . import api, state, writers

SUBFOLDERS = ("metadata", "highlights", "resources")


def ensure_directories(root: Path) -> dict:
    """Create the documents tree and return its subfolders.

    Args:
        root: The documents root folder.

    Returns:
        Mapping of subfolder name to path.

    Example:
        Prepare the output tree::

            folders = ensure_directories(Path("documents"))
            folders["metadata"].is_dir()
    """
    folders = {name: root / name for name in SUBFOLDERS}
    for path in folders.values():
        path.mkdir(parents=True, exist_ok=True)
    return folders


def _is_child(document: dict) -> bool:
    """Report whether a document is a highlight or note rather than a source.

    Args:
        document: A Reader document.

    Returns:
        ``True`` when the document hangs off a parent.

    Example:
        >>> _is_child({"parent_id": "01abc"})
        True
        >>> _is_child({"parent_id": None})
        False
    """
    return bool(document.get("parent_id"))


def collect_documents(
    token: str, tag: Optional[str], updated_after: Optional[str], limit: Optional[int]
) -> list:
    """Fetch the source documents for this run.

    Highlights and notes are dropped: they arrive as child documents and are
    handled through the export endpoint instead.

    Args:
        token: Readwise API token.
        tag: Tag to filter on.
        updated_after: ISO 8601 watermark, or ``None`` for a full fetch.
        limit: Stop after this many documents, or ``None`` for all.

    Returns:
        The documents to process.

    Example:
        Take the five most recently changed favourites::

            documents = collect_documents(token, "favorite", None, 5)
    """
    documents = []
    for document in api.iter_documents(token, tag=tag, updated_after=updated_after):
        if _is_child(document):
            continue
        documents.append(document)
        if limit and len(documents) >= limit:
            break
    return documents


def index_highlights(token: str, updated_after: Optional[str], wanted_ids: set) -> dict:
    """Return highlights for the documents in this run, keyed by document id.

    Args:
        token: Readwise API token.
        updated_after: ISO 8601 watermark, or ``None`` for a full fetch.
        wanted_ids: Reader document ids to keep.

    Returns:
        Mapping of document id to its list of highlights, plus the key
        ``__watermark__`` holding the newest group timestamp seen.

    Example:
        Collect highlights for a document set::

            found = index_highlights(token, None, {"01abc"})
    """
    index = {}
    stamps = []
    for book in api.iter_highlight_books(token, updated_after=updated_after):
        stamps.append(book.get("last_highlight_at") or book.get("updated"))
        external_id = book.get("external_id")
        if external_id in wanted_ids:
            index[external_id] = book.get("highlights", [])

    index["__watermark__"] = state.newest_timestamp(stamps)
    return index


def sync(
    root: Path,
    token: str,
    tag: Optional[str] = "favorite",
    full: bool = False,
    limit: Optional[int] = None,
    with_resources: bool = True,
    force_resources: bool = False,
) -> dict:
    """Run one sync and return a summary of what changed.

    Incrementality works on two levels. The API watermark limits what is
    fetched; an existing resource file stops it being downloaded again, which
    matters because ``updated_at`` bumps whenever reading progress changes.

    Args:
        root: The documents root folder.
        token: Readwise API token.
        tag: Tag to filter on.
        full: Ignore stored watermarks and re-fetch everything.
        limit: Process at most this many documents.
        with_resources: Download document content.
        force_resources: Re-download resources that already exist.

    Returns:
        Counts under ``documents``, ``resources`` and ``highlights``.

    Example:
        Sync favourites::

            summary = sync(Path("documents"), token, tag="favorite")
    """
    folders = ensure_directories(root)
    saved = {} if full else state.load_state(root, tag)

    documents = collect_documents(
        token, tag, saved.get("documents_updated_after"), limit
    )
    if not documents:
        return {"documents": 0, "resources": 0, "highlights": 0}

    counts = {"documents": 0, "resources": 0, "highlights": 0}
    has_new_documents = False

    for document in documents:
        is_new = writers.find_existing(folders["metadata"], document["id"]) is None
        has_new_documents = has_new_documents or is_new

        resource = {"file": None, "origin": "skipped", "fetched_at": None}
        existing = writers.find_existing(folders["resources"], document["id"])
        if with_resources and (force_resources or existing is None):
            resource = writers.download_resource(folders["resources"], document)
            counts["resources"] += 1
        elif existing is not None:
            resource = {"file": existing.name, "origin": "cached", "fetched_at": None}

        writers.write_metadata(folders["metadata"], document, resource)
        counts["documents"] += 1
        print(
            f"{'new ' if is_new else 'upd '}{document['id']} {document.get('title', '')}",
            file=sys.stderr,
        )

    # A newly tagged document may carry highlights made long ago, which an
    # incremental export would not return. Any new document forces a full pass.
    highlights_after = None if (full or has_new_documents) else saved.get(
        "highlights_updated_after"
    )
    found = index_highlights(
        token, highlights_after, {document["id"] for document in documents}
    )
    highlights_watermark = found.pop("__watermark__", None)

    for document in documents:
        highlights = found.get(document["id"], [])
        if writers.write_highlights(folders["highlights"], document, highlights):
            counts["highlights"] += 1

    state.save_state(
        root,
        tag,
        state.newest_timestamp(
            [document.get("updated_at") for document in documents]
        )
        or saved.get("documents_updated_after"),
        highlights_watermark or saved.get("highlights_updated_after"),
    )
    return counts
