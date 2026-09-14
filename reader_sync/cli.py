"""Command line entry point for the Reader sync pipeline."""

import argparse
import sys
from pathlib import Path
from typing import Optional

import requests

from . import api, pipeline

DEFAULT_TAG = "favorite"
DEFAULT_ROOT = Path("documents")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser.

    Returns:
        A parser exposing the ``sync`` command.

    Example:
        >>> args = build_parser().parse_args(["sync", "--limit", "5"])
        >>> args.tag, args.limit
        ('favorite', 5)
    """
    parser = argparse.ArgumentParser(
        prog="reader-sync",
        description="Sync tagged Readwise Reader documents into a local folder.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    sync = subcommands.add_parser("sync", help="Download documents that are missing.")
    sync.add_argument(
        "--tag", default=DEFAULT_TAG, help=f"Tag to filter on (default: {DEFAULT_TAG})."
    )
    sync.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="Output folder."
    )
    sync.add_argument(
        "--full", action="store_true", help="Ignore watermarks and re-fetch everything."
    )
    sync.add_argument("--limit", type=int, help="Process at most this many documents.")
    sync.add_argument(
        "--no-resources",
        dest="with_resources",
        action="store_false",
        help="Skip downloading document content.",
    )
    sync.add_argument(
        "--force-resources",
        action="store_true",
        help="Re-download content that is already on disk.",
    )
    return parser


def report(counts: dict) -> None:
    """Print a one-line summary, staying silent when nothing changed.

    Args:
        counts: Summary returned by :func:`reader_sync.pipeline.sync`.

    Example:
        >>> report({"documents": 0, "resources": 0, "highlights": 0})
        >>> report({"documents": 2, "resources": 1, "highlights": 1})
        2 documents, 1 resources, 1 highlight files
    """
    if not any(counts.values()):
        return
    print(
        f"{counts['documents']} documents, "
        f"{counts['resources']} resources, "
        f"{counts['highlights']} highlight files"
    )


def main(argv: Optional[list] = None) -> None:
    """Run the CLI.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``.

    Raises:
        SystemExit: With a message when the token is missing or a request fails.

    Example:
        Sync favourites from a shell::

            uv run python -m reader_sync sync --tag favorite
    """
    args = build_parser().parse_args(argv)

    try:
        counts = pipeline.sync(
            root=args.root,
            token=api.read_token(),
            tag=args.tag,
            full=args.full,
            limit=args.limit,
            with_resources=args.with_resources,
            force_resources=args.force_resources,
        )
    except (RuntimeError, requests.RequestException) as error:
        sys.exit(f"reader-sync: {error}")

    report(counts)
