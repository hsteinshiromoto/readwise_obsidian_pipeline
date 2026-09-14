"""Render and write the three on-disk products for a document.

Metadata is JSON, highlights are Markdown, and the resource is whatever the
source served. No module-level network calls happen here except the resource
download, which targets arbitrary third-party hosts rather than Readwise.
"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

SLUG_MAX_LENGTH = 60
RESOURCE_TIMEOUT = 30
CHUNK_SIZE = 65536
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/epub+zip": ".epub",
}
KNOWN_SUFFIXES = {".pdf", ".html", ".htm", ".txt", ".epub"}


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        Timestamp such as ``2026-09-13T21:00:00+00:00``.

    Example:
        Stamp a record::

            record = {"fetched_at": _now()}
    """
    return datetime.now(timezone.utc).isoformat()


def slug(text: Optional[str]) -> str:
    """Return a lowercase, filesystem-safe slug.

    Args:
        text: Arbitrary text, typically a document title.

    Returns:
        A hyphenated ASCII slug, or ``untitled`` when nothing usable remains.

    Example:
        >>> slug("How to find a date in real life")
        'how-to-find-a-date-in-real-life'
        >>> slug("Café / Bar!")
        'cafe-bar'
        >>> slug(None)
        'untitled'
    """
    normalised = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", ascii_only.lower())
    return "-".join(words)[:SLUG_MAX_LENGTH].strip("-") or "untitled"


def stem(document: dict) -> str:
    """Return the ``<id>__<slug>`` filename stem for a document.

    The id leads so files stay stable and greppable even when a title changes;
    the slug follows so the folder is readable.

    Args:
        document: A Reader document.

    Returns:
        The filename stem, without extension.

    Example:
        >>> stem({"id": "01abc", "title": "Deep Work"})
        '01abc__deep-work'
    """
    return f"{document['id']}__{slug(document.get('title'))}"


def find_existing(directory: Path, document_id: str) -> Optional[Path]:
    """Return the file already stored for a document id, if any.

    Matching on the id prefix means a retitled document is recognised rather
    than downloaded a second time under a new slug.

    Args:
        directory: Folder to search.
        document_id: The Reader document id.

    Returns:
        The first matching path, or ``None``.

    Example:
        Skip work that is already on disk::

            if find_existing(resources_dir, document["id"]):
                return
    """
    matches = sorted(directory.glob(f"{document_id}__*"))
    return matches[0] if matches else None


def write_metadata(directory: Path, document: dict, resource: dict) -> Path:
    """Write the metadata product for a document.

    ``html_content`` is stripped before writing: it is already saved under
    ``resources/`` and would otherwise duplicate megabytes into every metadata
    file.

    Args:
        directory: The ``metadata`` folder.
        document: A Reader document.
        resource: Outcome record from :func:`download_resource`.

    Returns:
        The path written.

    Example:
        Record a document and where its content came from::

            write_metadata(metadata_dir, document, {"origin": "source_url"})
    """
    payload = {
        "queried_at": _now(),
        "resource": resource,
        "document": {k: v for k, v in document.items() if k != "html_content"},
    }
    path = directory / f"{stem(document)}.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def render_highlights(document: dict, highlights: list) -> str:
    """Return the Markdown body for a document's highlights.

    Args:
        document: A Reader document, used for the header.
        highlights: Highlight dictionaries from the classic export endpoint.

    Returns:
        Markdown text ending in a single newline.

    Example:
        >>> body = render_highlights(
        ...     {"title": "Deep Work", "author": "Cal Newport"},
        ...     [{"text": "Focus is a skill.", "note": "Practise it."}],
        ... )
        >>> print(body, end="")
        # Deep Work
        <BLANKLINE>
        **Author:** Cal Newport
        <BLANKLINE>
        > Focus is a skill.
        <BLANKLINE>
        **Note:** Practise it.
    """
    lines = [f"# {document.get('title') or 'Untitled'}", ""]

    if document.get("author"):
        lines.append(f"**Author:** {document['author']}")
    if document.get("source_url"):
        lines.append(f"**Source:** {document['source_url']}")
    lines.append("")

    for highlight in highlights:
        text = (highlight.get("text") or "").strip()
        if not text:
            continue
        lines.extend(f"> {line}" if line else ">" for line in text.splitlines())
        note = (highlight.get("note") or "").strip()
        if note:
            lines.extend(["", f"**Note:** {note}"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_highlights(directory: Path, document: dict, highlights: list) -> Optional[Path]:
    """Write the highlights product, skipping documents that have none.

    Args:
        directory: The ``highlights`` folder.
        document: A Reader document.
        highlights: Highlight dictionaries for that document.

    Returns:
        The path written, or ``None`` when there was nothing to write.

    Example:
        Write only where highlights exist::

            path = write_highlights(highlights_dir, document, found)
    """
    if not any((highlight.get("text") or "").strip() for highlight in highlights):
        return None

    path = directory / f"{stem(document)}.md"
    path.write_text(render_highlights(document, highlights), encoding="utf-8")
    return path


def _extension(content_type: Optional[str], source_url: Optional[str]) -> str:
    """Choose a file extension for downloaded content.

    Args:
        content_type: The response ``Content-Type`` header, if any.
        source_url: The URL fetched, used as a fallback hint.

    Returns:
        A dotted extension, defaulting to ``.bin``.

    Example:
        >>> _extension("application/pdf; charset=binary", None)
        '.pdf'
        >>> _extension(None, "https://example.com/paper.pdf")
        '.pdf'
        >>> _extension(None, "https://example.com/thing")
        '.bin'
    """
    base = (content_type or "").split(";")[0].strip().lower()
    if base in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[base]

    suffix = Path(urlparse(source_url or "").path).suffix.lower()
    return suffix if suffix in KNOWN_SUFFIXES else ".bin"


def _download_original(directory: Path, document: dict, source_url: str) -> dict:
    """Stream the live original document to disk.

    Args:
        directory: The ``resources`` folder.
        document: A Reader document.
        source_url: The URL to fetch.

    Returns:
        A resource record describing what was written.

    Raises:
        requests.RequestException: If the host cannot be reached or refuses.

    Example:
        Fetch one original::

            record = _download_original(resources_dir, document, document["source_url"])
    """
    response = requests.get(
        source_url,
        headers={"User-Agent": USER_AGENT},
        timeout=RESOURCE_TIMEOUT,
        stream=True,
    )
    response.raise_for_status()

    path = directory / f"{stem(document)}{_extension(response.headers.get('Content-Type'), source_url)}"
    with path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            handle.write(chunk)

    return {"file": path.name, "origin": "source_url", "fetched_at": _now()}


def _write_readwise_copy(directory: Path, document: dict) -> dict:
    """Fall back to the snapshot Readwise stored when it ingested the document.

    Args:
        directory: The ``resources`` folder.
        document: A Reader document carrying ``html_content``.

    Returns:
        A resource record; ``origin`` is ``unavailable`` when even the snapshot
        is missing.

    Example:
        Use the backstop::

            record = _write_readwise_copy(resources_dir, document)
    """
    html = document.get("html_content")
    if not html:
        return {"file": None, "origin": "unavailable", "fetched_at": _now()}

    path = directory / f"{stem(document)}.html"
    path.write_text(html, encoding="utf-8")
    return {"file": path.name, "origin": "readwise_html", "fetched_at": _now()}


def download_resource(directory: Path, document: dict) -> dict:
    """Save the document content, preferring the live original.

    The original at ``source_url`` is authoritative, but links rot, paywalls
    appear and hosts time out. Any such failure falls back to Readwise's stored
    snapshot so the run still produces a file, and the record says which source
    won.

    Args:
        directory: The ``resources`` folder.
        document: A Reader document.

    Returns:
        A record with ``file``, ``origin`` and ``fetched_at``.

    Example:
        Fetch content with an automatic backstop::

            record = download_resource(resources_dir, document)
            assert record["origin"] in {"source_url", "readwise_html", "unavailable"}
    """
    source_url = document.get("source_url")
    if source_url:
        try:
            return _download_original(directory, document, source_url)
        except requests.RequestException:
            pass
    return _write_readwise_copy(directory, document)
