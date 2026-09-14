"""Watermarks that let each run fetch only what changed since the last one."""

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

STATE_FILENAME = ".sync_state.json"


def state_path(root: Path) -> Path:
    """Return the location of the sync state file.

    Args:
        root: The documents root folder.

    Returns:
        Path to the state file inside ``root``.

    Example:
        >>> state_path(Path("documents")).name
        '.sync_state.json'
    """
    return root / STATE_FILENAME


def load_state(root: Path, tag: Optional[str]) -> dict:
    """Return the saved watermarks for this tag.

    An empty dictionary is returned when no state exists, when it cannot be
    parsed, or when the previous run used a different tag. Reusing a watermark
    across tags would silently hide documents that the new tag should return.

    Args:
        root: The documents root folder.
        tag: The tag this run filters on.

    Returns:
        The stored state, or ``{}`` when it must not be reused.

    Example:
        Resume a favourites sync::

            state = load_state(Path("documents"), "favorite")
            since = state.get("documents_updated_after")
    """
    path = state_path(root)
    if not path.exists():
        return {}

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if state.get("tag") != tag:
        return {}
    return state


def save_state(
    root: Path,
    tag: Optional[str],
    documents_updated_after: Optional[str],
    highlights_updated_after: Optional[str],
) -> Path:
    """Persist the watermarks for the next run.

    Args:
        root: The documents root folder.
        tag: The tag this run filtered on.
        documents_updated_after: Newest document timestamp seen this run.
        highlights_updated_after: Newest highlight-group timestamp seen this run.

    Returns:
        The path written.

    Example:
        Record progress after a clean run::

            save_state(Path("documents"), "favorite", newest_doc, newest_highlight)
    """
    payload = {
        "tag": tag,
        "documents_updated_after": documents_updated_after,
        "highlights_updated_after": highlights_updated_after,
    }
    path = state_path(root)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def newest_timestamp(stamps: Iterable[Optional[str]]) -> Optional[str]:
    """Return the latest of a series of ISO 8601 timestamps.

    The newest value actually seen in a response is a safer watermark than the
    wall clock: it cannot skip records written while the run was in progress.

    Args:
        stamps: Timestamp strings, possibly containing blanks.

    Returns:
        The latest timestamp, or ``None`` when none are usable.

    Example:
        >>> newest_timestamp(["2026-01-02T00:00:00+00:00", "2026-03-04T00:00:00+00:00"])
        '2026-03-04T00:00:00+00:00'
        >>> newest_timestamp([None, ""]) is None
        True
    """
    usable = []
    for stamp in stamps:
        if not stamp:
            continue
        try:
            datetime.fromisoformat(stamp)
        except ValueError:
            continue
        usable.append(stamp)

    return max(usable, key=datetime.fromisoformat) if usable else None
