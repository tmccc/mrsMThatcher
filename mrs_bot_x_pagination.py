"""Read bounded X pages and classify cursor rejection through current dependencies.

Two explicit root adapters supply the current exception classes, JSON module,
classifier and logger on each call. Exact original bodies retain structured
message precedence, bounded head recovery, token history, strict page validation,
record references, callback order, partial-result metadata and logging. Runtime
requests and cursor/page effects use only the supplied callbacks; authentication,
bearer selection, discovery state and persistence keep their existing authority.
No constants or classes move. This standard-library-only owner retains no
callbacks, configuration, clients or state and performs no import-time file,
environment, provider, clock or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from types import ModuleType


def api_error_is_invalid_pagination_cursor(
    error: BaseException,
    *,
    ApiError: type[Exception],
    json: ModuleType,
) -> bool:
    """Recognise only X 400 responses which specifically reject a cursor."""
    if not isinstance(error, ApiError):
        return False
    if error.service != "x" or error.status_code != 400:
        return False
    message = str(error)
    candidate_messages: list[str] = []
    json_start = message.find("{")
    if json_start >= 0:
        try:
            payload = json.loads(message[json_start:])
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, dict):
                errors = [errors]
            if isinstance(errors, list):
                for item in errors:
                    if not isinstance(item, dict):
                        continue
                    for key in ("message", "detail", "reason"):
                        value = item.get(key)
                        if isinstance(value, str):
                            candidate_messages.append(value)
            for key in ("message", "detail", "reason"):
                value = payload.get(key)
                if isinstance(value, str):
                    candidate_messages.append(value)
            if not candidate_messages:
                return False
    if not candidate_messages:
        candidate_messages = [message]

    def explicitly_rejects_cursor(candidate: str) -> bool:
        candidate = candidate.casefold()
        names_cursor = any(
            marker in candidate
            for marker in (
                "pagination_token",
                "pagination token",
                "next_token",
                "next token",
                "pagination cursor",
            )
        )
        rejects_cursor = any(
            marker in candidate
            for marker in (
                "invalid",
                "expired",
                "malformed",
                "not valid",
                "not recognised",
                "not recognized",
            )
        )
        return names_cursor and rejects_cursor

    return any(explicitly_rejects_cursor(item) for item in candidate_messages)


def x_paginated_get(
    request_func,
    path: str,
    params: dict,
    *,
    max_pages: int,
    label: str,
    on_invalid_cursor = None,
    on_repeated_cursor = None,
    on_page = None,
    should_request_cursor = None,
    initial_requested_tokens: set[str] | None = None,
    retry_invalid_cursor_from_head: bool = True,
    ApiError: type[Exception],
    PaginationCursorProtocolError: type[Exception],
    api_error_is_invalid_pagination_cursor: Callable[[BaseException], bool],
    log: Logger,
) -> dict:
    """
    Read bounded pages from an X API collection endpoint.

    A cursor-specific HTTP 400 gets one recovery from the original collection
    head. The caller clears its durable saved cursor before that retry. Other
    client errors remain fail-closed. Every validated page is passed to
    ``on_page`` before a repeated returned token terminates traversal. By
    default the repeated token is then rejected before it can be requested
    twice. A caller may instead supply ``on_repeated_cursor`` to retain the
    bounded partial result and stop normally. ``should_request_cursor`` may
    optionally stop before a continuation request as a bounded partial success.
    """
    base_params = dict(params)
    recovered_invalid_cursor = False
    cursor_state_invalidated = False
    requested_tokens: set[str] = set(initial_requested_tokens or set())
    repeated_token_detected = False
    cursor_request_suppressed = False

    def invalidate_cursor_state() -> None:
        nonlocal cursor_state_invalidated
        if cursor_state_invalidated:
            return
        if on_invalid_cursor is not None:
            on_invalid_cursor()
        cursor_state_invalidated = True

    while True:
        combined: dict[str, object] = {"data": []}
        users_by_id: dict[str, dict] = {}
        media_by_key: dict[str, dict] = {}
        next_token = ""
        pages_fetched = 0
        restart_from_head = False

        for page in range(1, max(1, int(max_pages)) + 1):
            page_params = dict(base_params)
            if next_token:
                page_params["pagination_token"] = next_token
            request_token = str(page_params.get("pagination_token") or "")
            if request_token:
                if request_token in requested_tokens:
                    if on_repeated_cursor is None:
                        invalidate_cursor_state()
                        raise PaginationCursorProtocolError(
                            f"X {label} repeated pagination token before request",
                            service="x",
                        )
                    on_repeated_cursor(
                        request_token,
                        pages_fetched,
                        len(combined["data"]),
                    )
                    repeated_token_detected = True
                    next_token = ""
                    break
                if (
                    should_request_cursor is not None
                    and not should_request_cursor(request_token)
                ):
                    cursor_request_suppressed = True
                    next_token = ""
                    break
                requested_tokens.add(request_token)

            try:
                result = request_func(path, page_params)
            except ApiError as exc:
                if (
                    request_token
                    and not recovered_invalid_cursor
                    and api_error_is_invalid_pagination_cursor(exc)
                ):
                    invalidate_cursor_state()
                    if not retry_invalid_cursor_from_head:
                        raise
                    recovered_invalid_cursor = True
                    base_params.pop("pagination_token", None)
                    restart_from_head = True
                    log.warning(
                        "X %s rejected a pagination cursor; cleared the saved "
                        "cursor and retrying once from the collection head",
                        label,
                    )
                    break
                raise

            if not isinstance(result, dict):
                raise ApiError(f"X {label} returned a malformed paginated response object", service="x")
            page_data = result.get("data", [])
            includes = result.get("includes", {})
            meta = result.get("meta", {})
            if not isinstance(page_data, list) or any(not isinstance(item, dict) for item in page_data):
                raise ApiError(f"X {label} returned malformed paginated response data", service="x")
            if not isinstance(includes, dict):
                raise ApiError(f"X {label} returned malformed paginated response includes", service="x")
            users = includes.get("users", [])
            media_items = includes.get("media", [])
            if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
                raise ApiError(f"X {label} returned malformed paginated response users", service="x")
            if not isinstance(media_items, list) or any(not isinstance(media, dict) for media in media_items):
                raise ApiError(f"X {label} returned malformed paginated response media", service="x")
            if not isinstance(meta, dict):
                raise ApiError(f"X {label} returned malformed paginated response meta", service="x")
            pages_fetched = page
            combined["data"].extend(page_data)

            for user in users:
                user_id = str(user.get("id", ""))
                if user_id:
                    users_by_id[user_id] = user

            for media in media_items:
                media_key = str(media.get("media_key", ""))
                if media_key:
                    media_by_key[media_key] = media

            next_token = str(meta.get("next_token", "") or "")
            log.info(
                "Fetched %s page %d/%d items=%d next_token=%s",
                label,
                page,
                max_pages,
                len(page_data) if isinstance(page_data, list) else 0,
                bool(next_token),
            )
            if on_page is not None:
                on_page(
                    page_data,
                    includes,
                    next_token,
                    request_token,
                    pages_fetched,
                )
            if next_token and next_token in requested_tokens:
                if on_repeated_cursor is None:
                    invalidate_cursor_state()
                    raise PaginationCursorProtocolError(
                        f"X {label} returned a repeated pagination token",
                        service="x",
                    )
                on_repeated_cursor(
                    next_token,
                    pages_fetched,
                    len(combined["data"]),
                )
                repeated_token_detected = True
                next_token = ""
                break
            if not next_token:
                break

        if restart_from_head:
            continue
        break

    includes: dict[str, list[dict]] = {}
    if users_by_id:
        includes["users"] = list(users_by_id.values())
    if media_by_key:
        includes["media"] = list(media_by_key.values())
    if includes:
        combined["includes"] = includes
    combined["_pagination"] = {
        "pages_fetched": pages_fetched,
        "truncated": (
            bool(next_token)
            or repeated_token_detected
            or cursor_request_suppressed
        ),
        "next_token": next_token or None,
        "invalid_cursor_recovered": recovered_invalid_cursor,
        "repeated_token_detected": repeated_token_detected,
    }
    if cursor_request_suppressed:
        combined["_pagination"]["cursor_request_suppressed"] = True
    if next_token:
        pagination_log = log.info if label == "mentions" else log.warning
        pagination_log(
            "Pagination truncated for %s after %d page(s); more results remain",
            label,
            pages_fetched,
        )

    return combined
