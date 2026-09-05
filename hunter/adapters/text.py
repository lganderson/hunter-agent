"""Shared URL and HTML normalization for source adapters."""

import re
import html
from functools import lru_cache
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from .. import storage


@lru_cache(maxsize=16_384)
def normalize_url(value):
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    scheme = "https" if parsed.scheme.lower() in {"http", "https", ""} else parsed.scheme.lower()
    ignored_query_prefixes = {"utm_"}
    ignored_query_keys = set()
    if re.search(r"/jobs/results/[^/]+", parsed.path):
        ignored_query_keys.update({"q", "page", "location"})
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(tuple(ignored_query_prefixes)) and key.lower() not in ignored_query_keys
    ]
    normalized = parsed._replace(
        scheme=scheme,
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/"),
        query=urlencode(query, doseq=True),
        fragment="",
    )
    return urlunparse(normalized)


def clean_html_text(value):
    value = re.sub(r"<(br|p|li|div|section|article|h[1-6])\b[^>]*>", " ", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return storage.clean(html.unescape(value))
