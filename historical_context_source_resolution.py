"""Resolve saved grounding redirects and verify wording against fetched pages.

Network access occurs only in :func:`resolve_sources`.  Runtime validation of
the saved manifest is dependency-free and never performs retrieval.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

RESOLUTION_SCHEMA_VERSION = 1
RESOLUTION_POLICY_VERSION = (
    "saved-grounding-redirect-resolution-v2-transient-query-redaction"
)
LEGACY_RESOLUTION_POLICY_VERSIONS = frozenset({
    "saved-grounding-redirect-resolution-v1",
})
RESOLUTION_FILENAME = "historical_context_source_resolution.json"
MAXIMUM_BODY_BYTES = 5 * 1024 * 1024
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_SIGNED_QUERY_PREFIXES = (
    "x-amz-",
    "x-goog-",
)
_SIGNED_QUERY_KEYS = frozenset({
    "awsaccesskeyid",
    "googleaccessid",
    "signature",
})


def is_google_grounding_url(url: str) -> bool:
    """Recognise Google's exact HTTPS endpoint without importing retrieval tools."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.netloc == _REDIRECT_HOST
            # Google's opaque URL-safe tokens may retain base64 padding.
            and re.fullmatch(r"/grounding-api-redirect/[A-Za-z0-9_-]+={0,2}", parsed.path) is not None
            and not parsed.fragment
        )
    except ValueError:
        return False


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitise_transient_redirect_url(value: Any) -> tuple[str, bool]:
    """Remove provider signing credentials from one observed retrieval URL.

    The source identity remains the original provider redirect.  Resolved URLs
    are transport diagnostics only, so retaining short-lived signing material
    adds no evidential value and makes an otherwise public audit unsafe to
    distribute.
    """
    text = str(value or "")
    parsed = urlsplit(text)
    query_fields = parsed.query.split("&") if parsed.query else []
    query_keys = [
        unquote_plus(field.partition("=")[0]).casefold()
        for field in query_fields
    ]
    if not any(
        key in {"awsaccesskeyid", "googleaccessid"}
        or key.startswith(_SIGNED_QUERY_PREFIXES)
        for key in query_keys
    ):
        return text, False
    retained: list[str] = []
    removed = False
    for field, key in zip(query_fields, query_keys):
        if (
            key in _SIGNED_QUERY_KEYS
            or key.startswith(_SIGNED_QUERY_PREFIXES)
        ):
            removed = True
        else:
            retained.append(field)
    if not removed:
        return text, False
    return (
        urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "&".join(retained),
            parsed.fragment,
        )),
        True,
    )


def sanitise_resolution_manifest(
    resolution: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Return a deterministic credential-free saved-resolution manifest."""
    if (
        not isinstance(resolution, dict)
        or resolution.get("schema_version") != RESOLUTION_SCHEMA_VERSION
        or resolution.get("policy_version")
        not in LEGACY_RESOLUTION_POLICY_VERSIONS | {RESOLUTION_POLICY_VERSION}
    ):
        raise RuntimeError(
            "historical-context source resolution cannot be sanitised"
        )
    result = json.loads(json.dumps(resolution))
    changed = 0
    for row in result.get("items", {}).values():
        if not isinstance(row, dict):
            raise RuntimeError(
                "historical-context source resolution item is invalid"
            )
        if row.get("final_url") is not None:
            final_url, modified = _sanitise_transient_redirect_url(
                row["final_url"]
            )
            row["final_url"] = final_url
            changed += int(modified)
        redirects = row.get("redirect_chain")
        if not isinstance(redirects, list):
            raise RuntimeError(
                "historical-context source resolution redirect chain is invalid"
            )
        sanitised_redirects: list[str] = []
        for redirect in redirects:
            sanitised, modified = _sanitise_transient_redirect_url(redirect)
            sanitised_redirects.append(sanitised)
            changed += int(modified)
        row["redirect_chain"] = sanitised_redirects
    result["policy_version"] = RESOLUTION_POLICY_VERSION
    return result, changed


def _tokens(value: Any) -> list[str]:
    text = " ".join(str(value or "").split()).casefold().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", text)


def _normalised_text(value: Any) -> str:
    return " ".join(str(value or "").replace("’", "'").replace("“", '"').replace("”", '"').split())


def _sequence_index(haystack: list[str], needle: list[str]) -> int | None:
    if not needle or len(needle) > len(haystack):
        return None
    first = needle[0]
    for index, token in enumerate(haystack[: len(haystack) - len(needle) + 1]):
        if token == first and haystack[index:index + len(needle)] == needle:
            return index
    return None


def _wording_match(page_text: str, packet: dict[str, Any]) -> dict[str, Any]:
    normalised_page = _normalised_text(page_text)
    page_tokens = _tokens(page_text)
    for field in ("quote_text", "verified_text"):
        wording = _normalised_text(packet.get(field))
        if not wording:
            continue
        position = normalised_page.casefold().find(wording.casefold())
        if position >= 0:
            passage = normalised_page[max(0, position - 80):position + len(wording) + 80]
            return {
                "kind": "full_text",
                "field": field,
                "coverage": 1.0,
                "matched_passage": passage,
                "matched_passage_sha256": _sha256(passage.encode()),
            }
        words = _tokens(wording)
        index = _sequence_index(page_tokens, words)
        if index is not None:
            start = max(0, index - 12)
            end = min(len(page_tokens), index + len(words) + 12)
            passage = " ".join(page_tokens[start:end])
            return {
                "kind": "full_normalised_tokens",
                "field": field,
                "coverage": 1.0,
                "matched_passage": passage,
                "matched_passage_sha256": _sha256(passage.encode()),
            }
        if len(words) >= 16:
            minimum = max(12, int(len(words) * 0.75))
            for size in range(len(words) - 1, minimum - 1, -1):
                for offset in range(0, len(words) - size + 1):
                    index = _sequence_index(page_tokens, words[offset:offset + size])
                    if index is None:
                        continue
                    start = max(0, index - 12)
                    end = min(len(page_tokens), index + size + 12)
                    passage = " ".join(page_tokens[start:end])
                    return {
                        "kind": "partial_normalised_tokens",
                        "field": field,
                        "coverage": round(size / len(words), 6),
                        "matched_passage": passage,
                        "matched_passage_sha256": _sha256(passage.encode()),
                    }
    return {"kind": "none", "field": None, "coverage": 0.0,
            "matched_passage": None, "matched_passage_sha256": None}


def _page_text(content: bytes, content_type: str) -> tuple[str, str | None]:
    if "html" in content_type.casefold():
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "lxml")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()
        title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else None
        return " ".join(soup.get_text(" ", strip=True).split()), title
    if content_type.casefold().startswith("text/"):
        return " ".join(content.decode("utf-8", errors="replace").split()), None
    return "", None


def _fetch_one(
    url: str,
    quote_ids: list[str],
    packets: dict[str, dict[str, Any]],
    request: Callable[..., Any],
) -> dict[str, Any]:
    base = {
        "original_url": url,
        "original_url_sha256": _sha256(url.encode()),
        "quote_ids": quote_ids,
        "retrieved_at": _utc_now(),
    }
    try:
        # Runtime corpus validation uses only saved resolution data. Keep the
        # offline research/parser stack behind the explicit retrieval boundary.
        from public_source_fetch import fetch_public

        response = fetch_public(url, request=request, max_bytes=MAXIMUM_BODY_BYTES)
        content = bytearray()
        truncated = False
        for block in response.iter_content(64 * 1024):
            if not block:
                continue
            remaining = MAXIMUM_BODY_BYTES - len(content)
            if remaining <= 0:
                truncated = True
                break
            content.extend(block[:remaining])
            if len(block) > remaining:
                truncated = True
                break
        content_type = str(response.headers.get("content-type") or "")
        page_text, page_title = _page_text(bytes(content), content_type)
        matches = {
            quote_id: _wording_match(page_text, packets[quote_id])
            for quote_id in quote_ids
        }
        final_url, _ = _sanitise_transient_redirect_url(response.url)
        redirect_chain = [
            _sanitise_transient_redirect_url(row.url)[0]
            for row in response.history
        ] + [final_url]
        return {
            **base,
            "status": "resolved" if response.ok else "http_error",
            "http_status": int(response.status_code),
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "content_type": content_type,
            "captured_body_bytes": len(content),
            "body_truncated": truncated,
            "captured_body_sha256": _sha256(bytes(content)),
            "page_text_sha256": _sha256(page_text.encode()) if page_text else None,
            "page_title": page_title,
            "wording_matches": matches,
            "error": None,
        }
    except Exception as exc:
        return {
            **base,
            "status": "request_failed",
            "http_status": None,
            "final_url": None,
            "redirect_chain": [],
            "content_type": None,
            "captured_body_bytes": 0,
            "body_truncated": False,
            "captured_body_sha256": None,
            "page_text_sha256": None,
            "page_title": None,
            "wording_matches": {
                quote_id: {"kind": "none", "field": None, "coverage": 0.0,
                           "matched_passage": None, "matched_passage_sha256": None}
                for quote_id in quote_ids
            },
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


def source_scope(
    packets: dict[str, dict[str, Any]],
    quote_ids: set[str],
) -> dict[str, list[str]]:
    """Return saved grounding redirects indexed by their canonical quote IDs."""
    urls: dict[str, set[str]] = {}
    for quote_id in sorted(quote_ids):
        for source in packets[quote_id].get("sources", []):
            url = str(source.get("url") or "").strip()
            parsed = urlsplit(url)
            if (
                is_google_grounding_url(url)
            ):
                urls.setdefault(url, set()).add(quote_id)
    return {url: sorted(ids) for url, ids in sorted(urls.items())}


def resolve_sources(
    path: Path,
    packets: dict[str, dict[str, Any]],
    quote_ids: set[str],
    *,
    workers: int = 1,
    request: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Resolve a bounded saved-redirect scope with resumable atomic checkpoints."""
    if workers not in {1, 2}:
        raise ValueError("source resolution permits only one or two workers")
    if request is None:
        import requests

        request = requests.get
    scope = source_scope(packets, quote_ids)
    existing: dict[str, Any] = {}
    if path.exists():
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(candidate, dict)
            and candidate.get("schema_version") == RESOLUTION_SCHEMA_VERSION
            and candidate.get("policy_version") == RESOLUTION_POLICY_VERSION
        ):
            existing = candidate.get("items", {})
    items = {
        key: value for key, value in existing.items()
        if value.get("original_url") in scope
        and value.get("quote_ids") == scope[value["original_url"]]
    }
    lock = threading.Lock()

    def save() -> None:
        payload = _resolution_payload(scope, quote_ids, items)
        from historical_context_formatter import atomic_write_json

        atomic_write_json(path, payload)

    pending = [url for url in scope if _sha256(url.encode()) not in items]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_one, url, scope[url], packets, request): url
            for url in pending
        }
        completed_since_save = 0
        for future in as_completed(futures):
            row = future.result()
            key = row["original_url_sha256"]
            with lock:
                items[key] = row
                completed_since_save += 1
                if completed_since_save >= 10:
                    save()
                    completed_since_save = 0
    save()
    result = json.loads(path.read_text(encoding="utf-8"))
    validate_resolution(result, packets)
    return result


def _resolution_payload(
    scope: dict[str, list[str]],
    quote_ids: set[str],
    items: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "policy_version": RESOLUTION_POLICY_VERSION,
        "scope": "post-recovery-no-reliable-source-packets",
        "scope_quote_count": len(quote_ids),
        "scope_quote_ids_sha256": _sha256(("\n".join(sorted(quote_ids)) + "\n").encode()),
        "source_scope_sha256": _sha256(_canonical_json(scope)),
        "expected_url_count": len(scope),
        "completed_url_count": len(items),
        "complete": len(items) == len(scope),
        "items": dict(sorted(items.items())),
    }


def validate_resolution(
    resolution: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> None:
    """Validate saved resolution integrity without any network operation."""
    if (
        not isinstance(resolution, dict)
        or resolution.get("schema_version") != RESOLUTION_SCHEMA_VERSION
        or resolution.get("policy_version") != RESOLUTION_POLICY_VERSION
        or not resolution.get("complete")
        or resolution.get("completed_url_count") != resolution.get("expected_url_count")
        or resolution.get("completed_url_count") != len(resolution.get("items", {}))
    ):
        raise RuntimeError("historical-context source resolution is incomplete or incompatible")
    for key, row in resolution["items"].items():
        if key != _sha256(str(row.get("original_url") or "").encode()):
            raise RuntimeError("historical-context source resolution URL identity differs")
        if any(quote_id not in packets for quote_id in row.get("quote_ids", [])):
            raise RuntimeError("historical-context source resolution has unknown quote ID")
        if any(
            str(row.get("original_url") or "") not in {
                str(source.get("url") or "").strip()
                for source in packets[quote_id].get("sources", [])
            }
            for quote_id in row.get("quote_ids", [])
        ):
            raise RuntimeError(
                "historical-context source resolution is not backed by a packet URL"
            )
        if set(row.get("wording_matches", {})) != set(row.get("quote_ids", [])):
            raise RuntimeError("historical-context source resolution match coverage differs")
        for resolved_url in [
            row.get("final_url"),
            *row.get("redirect_chain", []),
        ]:
            if resolved_url is None:
                continue
            sanitised_url, modified = _sanitise_transient_redirect_url(
                resolved_url
            )
            if modified or sanitised_url != resolved_url:
                raise RuntimeError(
                    "historical-context source resolution retains transient "
                    "provider credentials"
                )
        for match in row["wording_matches"].values():
            passage = match.get("matched_passage")
            if passage is not None and match.get("matched_passage_sha256") != _sha256(passage.encode()):
                raise RuntimeError("historical-context source resolution passage hash differs")
