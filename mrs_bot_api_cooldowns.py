"""Apply API cooldown and error-window policy through current root dependencies.

Five explicit root adapters supply settings, limits, classification, clock,
datetime, logger and persistence on each call. Original bodies preserve scope
routing, coercion, references, clock samples and mutation/log/save order. Calls
may mutate the supplied state, log, and perform the original ordinary save only
after a 429 or the repeated-error threshold. Configuration, error semantics and
durable persistence retain their existing authority. This standard-library-only
owner retains no callbacks, configuration, clients or state and performs no
import-time file, environment, provider, clock or RNG work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable


def in_api_cooldown(
    state: dict,
    *,
    scope: str = 'api',
    datetime: type,
    log: logging.Logger,
    now_epoch: Callable[[], int],
) -> bool:
    """Return the in API cooldown."""
    if scope == "quote":
        until = int(state.get("quote_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("quote_api_cooldown_reason", "Quote API cooldown")
        label = "Quote API cooldown"
    elif scope == "openai":
        until = int(state.get("openai_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("openai_api_cooldown_reason", "OpenAI API cooldown")
        label = "OpenAI API cooldown"
    elif scope == "write":
        until = int(state.get("x_write_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("x_write_api_cooldown_reason", "X write API cooldown")
        label = "X write API cooldown"
    else:
        until = int(state.get("api_cooldown_until_epoch", 0) or 0)
        reason = state.get("api_cooldown_reason", "X read API cooldown")
        label = "X read API cooldown"

    if until <= now_epoch():
        return False

    until_human = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
    log.warning("%s active until %s: %s", label, until_human, reason)
    return True


def clear_expired_api_cooldowns(
    state: dict,
    *,
    log: logging.Logger,
    now_epoch: Callable[[], int],
) -> bool:
    """Clear expired API cooldowns."""
    changed = False
    current = now_epoch()

    for until_key, reason_key, label in (
        ("api_cooldown_until_epoch", "api_cooldown_reason", "X read API cooldown"),
        ("x_write_api_cooldown_until_epoch", "x_write_api_cooldown_reason", "X write API cooldown"),
        (
            "openai_api_cooldown_until_epoch",
            "openai_api_cooldown_reason",
            "OpenAI API cooldown",
        ),
        ("quote_api_cooldown_until_epoch", "quote_api_cooldown_reason", "Quote API cooldown"),
    ):
        until = int(state.get(until_key, 0) or 0)
        if until <= 0 or until > current:
            continue

        log.info(
            "Clearing expired %s. until_epoch=%s reason=%s",
            label,
            until,
            state.get(reason_key, ""),
        )
        state[until_key] = 0
        state[reason_key] = ""
        changed = True

    return changed


def prune_error_epochs(
    epochs: list[int],
    *,
    ERROR_WINDOW_SECONDS: int,
    log: logging.Logger,
    now_epoch: Callable[[], int],
) -> list[int]:
    """Prune error epochs."""
    cutoff = now_epoch() - ERROR_WINDOW_SECONDS
    pruned = [int(e) for e in epochs if int(e) >= cutoff]
    log.debug("Pruned error epochs from %d to %d", len(epochs), len(pruned))
    return pruned


def cooldown_until_for_rate_limit(
    current: int,
    reset_epoch: int | None,
    *,
    COOLDOWN_AFTER_429_SECONDS: int,
) -> int:
    """Return the cooldown until for rate limit."""
    if reset_epoch and reset_epoch > current:
        return reset_epoch + 60
    return current + COOLDOWN_AFTER_429_SECONDS


def record_api_error(
    state: dict,
    error: Exception,
    service: str,
    *,
    scope: str = 'api',
    COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS: int,
    MAX_OPENAI_ERRORS_PER_WINDOW: int,
    MAX_X_ERRORS_PER_WINDOW: int,
    api_error_is_reply_not_allowed: Callable[[Exception], bool],
    cooldown_until_for_rate_limit: Callable[[int, int | None], int],
    datetime: type,
    log: logging.Logger,
    now_epoch: Callable[[], int],
    prune_error_epochs: Callable[[list[int]], list[int]],
    save_state: Callable[[dict], None],
) -> None:
    """Record API error."""
    if service == "x" and scope == "write" and api_error_is_reply_not_allowed(error):
        log.warning(
            "Not recording terminal target-specific X reply restriction in the transient write-error window: %s",
            error,
        )
        return

    current = now_epoch()

    if service == "x" and scope == "quote":
        key = "quote_x_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "quote_api_cooldown_until_epoch"
        cooldown_reason_key = "quote_api_cooldown_reason"
    elif service == "x" and scope == "write":
        key = "x_write_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "x_write_api_cooldown_until_epoch"
        cooldown_reason_key = "x_write_api_cooldown_reason"
    elif service == "x":
        key = "x_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "api_cooldown_until_epoch"
        cooldown_reason_key = "api_cooldown_reason"
    elif service == "openai":
        key = "openai_error_epochs"
        max_errors = MAX_OPENAI_ERRORS_PER_WINDOW
        cooldown_until_key = "openai_api_cooldown_until_epoch"
        cooldown_reason_key = "openai_api_cooldown_reason"
    else:
        raise ValueError(f"unsupported API error service: {service}")

    epochs = prune_error_epochs(state.get(key, []))
    epochs.append(current)
    state[key] = epochs

    status_code = getattr(error, "status_code", None)
    reset_epoch = getattr(error, "reset_epoch", None)
    diagnostic_event = getattr(error, "diagnostic_event", None)
    diagnostic_sha256 = getattr(error, "diagnostic_sha256", None)

    if diagnostic_event is not None or diagnostic_sha256 is not None:
        log.warning(
            "Recorded %s API error. status_code=%s errors_in_window=%d/%d "
            "reset_epoch=%s diagnostic_event=%s diagnostic_sha256=%s error=%s",
            f"{scope}/{service}" if scope != "api" else service,
            status_code,
            len(epochs),
            max_errors,
            reset_epoch,
            diagnostic_event,
            diagnostic_sha256,
            error,
        )
    else:
        log.warning(
            "Recorded %s API error. status_code=%s errors_in_window=%d/%d "
            "reset_epoch=%s error=%s",
            f"{scope}/{service}" if scope != "api" else service,
            status_code,
            len(epochs),
            max_errors,
            reset_epoch,
            error,
        )

    if status_code == 429:
        state[cooldown_until_key] = cooldown_until_for_rate_limit(current, reset_epoch)
        state[cooldown_reason_key] = f"{scope}/{service} returned 429/rate limit" if scope != "api" else f"{service} returned 429/rate limit"
        log.error(
            "Entering API cooldown after 429 until %s",
            datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        save_state(state)
        return

    if len(epochs) >= max_errors:
        state[cooldown_until_key] = current + COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS
        state[cooldown_reason_key] = (
            f"too many {scope}/{service} API errors in the last hour"
            if scope != "api"
            else f"too many {service} API errors in the last hour"
        )
        log.error(
            "Entering API cooldown after repeated errors until %s",
            datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        save_state(state)
