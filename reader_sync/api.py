"""HTTP access to the Readwise Reader (v3) and Readwise classic (v2) APIs.

This is the only module that performs Readwise network calls. Both LIST endpoints
allow 20 requests per minute per token, so every request is paced accordingly.
"""

import os
import time
from typing import Iterator, Optional

import requests

READER_LIST_URL = "https://readwise.io/api/v3/list/"
EXPORT_URL = "https://readwise.io/api/v2/export/"

# Both LIST endpoints are limited to 20 requests/minute per access token.
MIN_SECONDS_BETWEEN_REQUESTS = 3.0
MAX_RETRIES = 5
REQUEST_TIMEOUT = 60


def read_token() -> str:
    """Return the Readwise API token from the environment.

    Returns:
        The token stored in ``READWISE_API_TOKEN``.

    Raises:
        RuntimeError: If the variable is unset or empty.

    Example:
        Read the token before starting a run::

            token = read_token()
    """
    token = os.getenv("READWISE_API_TOKEN")
    if not token:
        raise RuntimeError("READWISE_API_TOKEN is not set")
    return token


def _sleep_until_allowed(last_request_at: float) -> float:
    """Block until the next request may be sent.

    Args:
        last_request_at: ``time.monotonic()`` value of the previous request,
            or ``0.0`` when no request has been made yet.

    Returns:
        The monotonic timestamp at which the caller may send its request.

    Example:
        Pace a pair of requests::

            stamp = _sleep_until_allowed(0.0)
            stamp = _sleep_until_allowed(stamp)
    """
    elapsed = time.monotonic() - last_request_at
    if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
    return time.monotonic()


def _retry_delay(response: requests.Response, attempt: int) -> float:
    """Return how long to wait after a rate-limited response.

    Honours the server's ``Retry-After`` header when present, otherwise backs
    off exponentially.

    Args:
        response: The ``429`` response received.
        attempt: Zero-based retry counter.

    Returns:
        Seconds to wait before retrying.

    Example:
        >>> class Fake:
        ...     headers = {"Retry-After": "12"}
        >>> _retry_delay(Fake(), 0)
        12.0
    """
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.strip().isdigit():
        return float(retry_after.strip())
    return float(min(2 ** (attempt + 2), 120))


def get_pages(url: str, token: str, params: Optional[dict] = None) -> Iterator[dict]:
    """Yield each page of a paginated Readwise response.

    Follows ``nextPageCursor`` until the API stops returning one, pacing requests
    to stay inside the rate limit and retrying rate-limited requests.

    Args:
        url: Endpoint to page through.
        token: Readwise API token.
        params: Query parameters sent with every request.

    Yields:
        The decoded JSON body of each page.

    Raises:
        requests.HTTPError: For any non-2xx response other than a retried 429.

    Example:
        Count the documents in a library::

            total = sum(len(page["results"]) for page in get_pages(READER_LIST_URL, token))
    """
    query = dict(params or {})
    headers = {"Authorization": f"Token {token}"}
    cursor = None
    last_request_at = 0.0

    while True:
        if cursor:
            query["pageCursor"] = cursor

        for attempt in range(MAX_RETRIES):
            last_request_at = _sleep_until_allowed(last_request_at)
            response = requests.get(
                url, params=query, headers=headers, timeout=REQUEST_TIMEOUT
            )
            if response.status_code != 429:
                break
            time.sleep(_retry_delay(response, attempt))

        response.raise_for_status()
        page = response.json()
        yield page

        cursor = page.get("nextPageCursor")
        if not cursor:
            return


def iter_documents(
    token: str, tag: Optional[str] = None, updated_after: Optional[str] = None
) -> Iterator[dict]:
    """Yield Reader documents, newest page first.

    Args:
        token: Readwise API token.
        tag: Restrict to documents carrying this tag. The API parameter is
            ``tag`` (singular); the plural form is silently ignored by Readwise.
        updated_after: ISO 8601 timestamp; only documents changed after it are
            returned.

    Yields:
        One document dictionary at a time, including ``html_content``.

    Example:
        Pull every favourite::

            favourites = list(iter_documents(token, tag="favorite"))
    """
    params = {"withHtmlContent": "true"}
    if tag:
        params["tag"] = tag
    if updated_after:
        params["updatedAfter"] = updated_after

    for page in get_pages(READER_LIST_URL, token, params):
        yield from page.get("results", [])


def iter_highlight_books(
    token: str, updated_after: Optional[str] = None
) -> Iterator[dict]:
    """Yield highlight groups from the classic export endpoint.

    Each group is one source document with its highlights already attached.
    Groups originating in Reader carry an ``external_id`` matching the Reader
    document id.

    Args:
        token: Readwise API token.
        updated_after: ISO 8601 timestamp; only groups changed after it are
            returned.

    Yields:
        One group dictionary at a time.

    Example:
        Index Reader highlights by document id::

            by_document = {
                book["external_id"]: book["highlights"]
                for book in iter_highlight_books(token)
                if book.get("external_id")
            }
    """
    params = {}
    if updated_after:
        params["updatedAfter"] = updated_after

    for page in get_pages(EXPORT_URL, token, params):
        yield from page.get("results", [])
