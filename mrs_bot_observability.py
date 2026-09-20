"""Logging and descriptive observability for already authoritative bot outcomes.

Managed handler marking/removal share their fixed marker within this owner.
Fixed path construction, diagnostic encoding and redaction use local imports.
The root supplies current runtime boundaries on each call; descriptive event
callbacks remain late-bound. Import performs no runtime work.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any


_MANAGED_LOG_HANDLER_ATTR = "_mrs_mthatcher_managed_handler"


def remove_managed_log_handlers(
    logger: logging.Logger,
) -> None:
    """Detach and close handlers installed by this module."""
    for handler in list(logger.handlers):
        if not getattr(handler, _MANAGED_LOG_HANDLER_ATTR, False):
            continue
        logger.removeHandler(handler)
        handler.close()


def mark_managed_log_handler(
    handler: logging.Handler,
    kind: str,
) -> logging.Handler:
    """Mark a logging handler as owned by this module."""
    setattr(handler, _MANAGED_LOG_HANDLER_ATTR, True)
    setattr(handler, "_mrs_mthatcher_handler_kind", kind)
    return handler


def setup_logging(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
    LOG_FILE: Any,
    PRODUCTION_BASE_DIR: Any,
    PRODUCTION_LOG_BACKUP_COUNT: Any,
    PRODUCTION_LOG_MAX_BYTES: Any,
    RotatingFileHandler: Any,
    logging: Any,
    os: Any,
    path_is_same_or_child: Any,
    sys: Any,
) -> logging.Logger:
    """Configure console and optional rotating-file logging."""
    target_log = Path(log_path).expanduser() if log_path is not None else LOG_FILE
    if configure_file_logging and os.getenv("PYTEST_CURRENT_TEST") and path_is_same_or_child(target_log, PRODUCTION_BASE_DIR):
        raise RuntimeError(f"Refusing to attach pytest process to production log: {target_log}")
    if configure_file_logging:
        target_log.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("mrsMThatcher")
    logger.setLevel(level)
    logger.propagate = False
    remove_managed_log_handlers(logger)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = mark_managed_log_handler(logging.StreamHandler(sys.stdout), "console")
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if configure_file_logging:
        file_handler = mark_managed_log_handler(
            RotatingFileHandler(
                target_log,
                maxBytes=PRODUCTION_LOG_MAX_BYTES,
                backupCount=PRODUCTION_LOG_BACKUP_COUNT,
            ),
            "file",
        )
        setattr(file_handler, "_mrs_mthatcher_log_path", str(target_log.resolve()))
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.debug(
        "Logging initialised. LOG_LEVEL=%s LOG_FILE=%s",
        level_name,
        target_log if configure_file_logging else "<disabled>",
    )
    return logger


def report_bot_health_progress(
    phase: str,
    *,
    paused: bool | None = None,
    remote_write_blocked: bool | None = None,
    loop_started: bool = False,
    loop_completed: bool = False,
    _BOT_HEALTH_REPORTER: Any,
) -> None:
    """Advance observational telemetry without affecting bot operation."""

    reporter = _BOT_HEALTH_REPORTER
    if reporter is None:
        return
    try:
        reporter.progress(
            phase,
            paused=paused,
            remote_write_blocked=remote_write_blocked,
            loop_started=loop_started,
            loop_completed=loop_completed,
        )
    except Exception:
        pass


def redact_secret(value: str, visible: int = 4) -> str:
    """Redact a secret value before it is logged."""
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "<set-but-short>"
    return f"{value[:visible]}...{value[-visible:]}"


def log_json_debug(
    label: str,
    obj: object,
    max_chars: int = 4000,
    *,
    log: Any,
) -> None:
    """Log bounded JSON with recursively redacted credential-like values."""

    sensitive_markers = (
        "secret",
        "token",
        "password",
        "authorization",
        "apikey",
        "bearer",
        "cookie",
        "oauth",
    )

    def sanitise(value: object, seen: set[int], depth: int = 0) -> object:
        if depth > 20:
            return "<maximum-depth>"
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                return "<circular-reference>"
            seen.add(identity)
            try:
                cleaned: dict[str, object] = {}
                for key, item in value.items():
                    key_text = str(key)
                    compact_key = re.sub(r"[^a-z0-9]", "", key_text.lower())
                    if any(marker in compact_key for marker in sensitive_markers):
                        cleaned[key_text] = "[REDACTED]"
                    else:
                        cleaned[key_text] = sanitise(item, seen, depth + 1)
                return cleaned
            finally:
                seen.remove(identity)
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return "<circular-reference>"
            seen.add(identity)
            try:
                return [sanitise(item, seen, depth + 1) for item in value]
            finally:
                seen.remove(identity)
        return value

    try:
        redacted = sanitise(obj, set())
        text = json.dumps(redacted, indent=2, sort_keys=True, default=str)
    except Exception:
        text = "<unserialisable-redacted-payload>"

    if len(text) > max_chars:
        text = text[:max_chars] + "...<truncated>"

    log.debug("%s: %s", label, text)


def state_debug_summary(state: object) -> dict[str, object]:
    """Return state keys and collection sizes without any state values."""
    if not isinstance(state, dict):
        return {"type": type(state).__name__}
    collection_counts = {
        str(key): len(value)
        for key, value in state.items()
        if isinstance(value, (dict, list, tuple, set))
    }
    return {
        "key_count": len(state),
        "keys": sorted(str(key) for key in state),
        "collection_counts": dict(sorted(collection_counts.items())),
    }


def log_event(
    event: str,
    *,
    fields: object,
    log: Any,
) -> None:
    """Emit a stable one-line structured event for digest scripts."""
    payload = {"event": event}
    payload.update(fields)
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(payload)
    log.info("EVENT %s", text)


def _log_descriptive_observability_failure(
    message: str,
    *,
    log: Any,
) -> None:
    try:
        log.error(message, exc_info=True)
    except Exception:
        pass


def emit_account_root_posted(
    *,
    lane: str,
    post_id: object,
    public_text: object = None,
    quote_id: object = None,
    quote_text: object = None,
    image_summary: object = None,
    post_created_at: object = None,
    _log_descriptive_observability_failure: Any,
    log_event: Any,
) -> None:
    """Describe an already durable account root without affecting its outcome."""
    try:
        stable_post_id = str(post_id)
        public = str(public_text).strip() if public_text is not None else ""
        quote = str(quote_text).strip() if quote_text is not None else ""
        summary = str(image_summary).strip() if image_summary is not None else ""
        if public:
            visible_text = public
            visible_text_source = "public_text"
        elif quote:
            visible_text = quote
            visible_text_source = "image_quote_text"
        elif summary:
            visible_text = summary
            visible_text_source = "image_summary"
        else:
            visible_text = None
            visible_text_source = "unavailable"
        log_event(
            "account_root_posted",
            event_version=1,
            lane=str(lane),
            post_id=stable_post_id,
            root_post_id=stable_post_id,
            conversation_id=stable_post_id,
            public_text=public or None,
            visible_text=visible_text,
            visible_text_source=visible_text_source,
            quote_id=(str(quote_id) if quote_id is not None else None),
            quote_text=quote or None,
            image_summary=summary or None,
            post_created_at=(
                str(post_created_at) if post_created_at is not None else None
            ),
            publication_authority="confirmed_transport",
        )
    except Exception:
        # Observability is deliberately downstream of publication authority and
        # can never turn a confirmed post into a failed posting outcome.
        _log_descriptive_observability_failure(
            "Could not emit descriptive account_root_posted observability"
        )


def emit_historical_context_reply_posted(
    *,
    parent_post_id: object,
    reply_post_id: object,
    reply_text: object,
    quote_id: object,
    reply_created_at: object = None,
    _log_descriptive_observability_failure: Any,
    log_event: Any,
) -> None:
    """Describe an already durable historical-context reply without transport."""
    try:
        parent_id = str(parent_post_id)
        log_event(
            "historical_context_reply_posted",
            event_version=1,
            lane="historical_context_reply",
            parent_post_id=parent_id,
            reply_post_id=str(reply_post_id),
            root_post_id=parent_id,
            conversation_id=parent_id,
            reply_text=(str(reply_text) if reply_text is not None else None),
            quote_id=str(quote_id),
            reply_created_at=(
                str(reply_created_at) if reply_created_at is not None else None
            ),
            publication_authority="confirmed_transport",
        )
    except Exception:
        _log_descriptive_observability_failure(
            "Could not emit descriptive historical_context_reply_posted observability"
        )


def emit_historical_context_history_observation(
    item: object,
    *,
    emit_historical_context_reply_posted: Any,
) -> None:
    """Emit the v1 contract only for one validated completed history item."""
    if not isinstance(item, dict) or item.get("status") != "completed":
        return
    required = ("parent_post_id", "reply_post_id", "reply_text", "quote_id")
    if not all(item.get(field) is not None for field in required):
        return
    emit_historical_context_reply_posted(
        parent_post_id=item["parent_post_id"],
        reply_post_id=item["reply_post_id"],
        reply_text=item["reply_text"],
        quote_id=item["quote_id"],
    )


def emit_historical_context_store_observation(
    store: object,
    parent_post_id: object,
    *,
    _log_descriptive_observability_failure: Any,
    emit_historical_context_history_observation: Any,
) -> None:
    """Read completed history for observability without affecting recovery."""
    try:
        history = store.history()
        items = history.get("items") if isinstance(history, dict) else None
        emit_historical_context_history_observation(
            items.get(str(parent_post_id)) if isinstance(items, dict) else None
        )
    except Exception:
        _log_descriptive_observability_failure(
            "Could not emit recovered historical-context observability"
        )


def print_rate_limit_headers(
    response: requests.Response,
    *,
    datetime: Any,
    log: Any,
) -> int | None:
    """Log rate limit headers."""
    log.warning("Rate Limit: %s", response.headers.get("x-rate-limit-limit"))
    log.warning("Remaining: %s", response.headers.get("x-rate-limit-remaining"))

    reset_time = response.headers.get("x-rate-limit-reset")
    if not reset_time:
        return None

    try:
        reset_epoch = int(reset_time)
    except (TypeError, ValueError):
        log.warning("Rate Limit Resets At: %s", reset_time)
        return None

    try:
        reset_time_human = datetime.fromtimestamp(reset_epoch).strftime("%Y-%m-%d %H:%M:%S")
        log.warning("Rate Limit Resets At: %s", reset_time_human)
        return reset_epoch
    except Exception:
        log.warning("Rate Limit Resets At invalid epoch: %s", reset_time)
        return None


def _log_validated_single_call_reply(
    *,
    target_description: str,
    target_id: str,
    reply: object,
    log: Any,
) -> None:
    """Log validated output metadata without retaining exact public prose."""

    text = str(reply)
    encoded = text.encode("utf-8", errors="strict")
    log.info(
        "Generated validated reply to %s %s character_count=%d "
        "utf8_byte_count=%d sha256=%s",
        target_description,
        str(target_id),
        len(text),
        len(encoded),
        hashlib.sha256(encoded).hexdigest(),
    )
