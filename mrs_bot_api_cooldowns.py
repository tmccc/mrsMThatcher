"""Own API cooldown scopes, error windows and rate-limit/repeated-error policy.

The root creates an inert ApiCooldowns owner with current settings, classifier,
clock, datetime, logger and persistence for each call. Recording calls owned
window pruning and rate-limit calculation directly. Scope routing, coercion,
references, separate clock samples and mutation/log/save order stay unchanged.

Calls mutate explicit caller state and log, saving ordinarily only after a 429
or the repeated-error threshold. No caller state is retained. Configuration,
error semantics and durable persistence keep their existing authority. Import
uses only the standard library and performs no runtime access.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_state_generation import StateCommitProof


@dataclass(frozen=True)
class ApiCooldowns:
    """Record and expire scoped API cooldowns using current runtime boundaries."""

    datetime: type
    log: logging.Logger
    now_epoch: Callable[[], int]
    error_window_seconds: int
    rate_limit_seconds: int
    repeated_error_seconds: int
    maximum_openai_errors: int
    maximum_x_errors: int
    reply_not_allowed: Callable[[Exception], bool]
    save_state: Callable[[dict], StateCommitProof]

    def active(self, state: dict, *, scope: str = 'api') -> bool:
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

        if until <= self.now_epoch():
            return False

        until_human = self.datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
        self.log.warning("%s active until %s: %s", label, until_human, reason)
        return True


    def clear_expired(self, state: dict) -> bool:
        """Clear expired API cooldowns."""
        changed = False
        current = self.now_epoch()

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

            self.log.info(
                "Clearing expired %s. until_epoch=%s reason=%s",
                label,
                until,
                state.get(reason_key, ""),
            )
            state[until_key] = 0
            state[reason_key] = ""
            changed = True

        return changed


    def prune_epochs(self, epochs: list[int]) -> list[int]:
        """Prune error epochs."""
        cutoff = self.now_epoch() - self.error_window_seconds
        pruned = [int(e) for e in epochs if int(e) >= cutoff]
        self.log.debug("Pruned error epochs from %d to %d", len(epochs), len(pruned))
        return pruned


    def rate_limit_until(self, current: int, reset_epoch: int | None) -> int:
        """Return the cooldown until for rate limit."""
        if reset_epoch and reset_epoch > current:
            return reset_epoch + 60
        return current + self.rate_limit_seconds


    def record_error(
        self, state: dict, error: Exception, service: str, *, scope: str = 'api',
    ) -> None:
        """Record API error."""
        if service == "x" and scope == "write" and self.reply_not_allowed(error):
            self.log.warning(
                "Not recording terminal target-specific X reply restriction in the transient write-error window: %s",
                error,
            )
            return

        current = self.now_epoch()

        if service == "x" and scope == "quote":
            key = "quote_x_error_epochs"
            max_errors = self.maximum_x_errors
            cooldown_until_key = "quote_api_cooldown_until_epoch"
            cooldown_reason_key = "quote_api_cooldown_reason"
        elif service == "x" and scope == "write":
            key = "x_write_error_epochs"
            max_errors = self.maximum_x_errors
            cooldown_until_key = "x_write_api_cooldown_until_epoch"
            cooldown_reason_key = "x_write_api_cooldown_reason"
        elif service == "x":
            key = "x_error_epochs"
            max_errors = self.maximum_x_errors
            cooldown_until_key = "api_cooldown_until_epoch"
            cooldown_reason_key = "api_cooldown_reason"
        elif service == "openai":
            key = "openai_error_epochs"
            max_errors = self.maximum_openai_errors
            cooldown_until_key = "openai_api_cooldown_until_epoch"
            cooldown_reason_key = "openai_api_cooldown_reason"
        else:
            raise ValueError(f"unsupported API error service: {service}")

        epochs = self.prune_epochs(state.get(key, []))
        epochs.append(current)
        state[key] = epochs

        status_code = getattr(error, "status_code", None)
        reset_epoch = getattr(error, "reset_epoch", None)
        diagnostic_event = getattr(error, "diagnostic_event", None)
        diagnostic_sha256 = getattr(error, "diagnostic_sha256", None)

        if diagnostic_event is not None or diagnostic_sha256 is not None:
            self.log.warning(
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
            self.log.warning(
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
            state[cooldown_until_key] = self.rate_limit_until(current, reset_epoch)
            state[cooldown_reason_key] = f"{scope}/{service} returned 429/rate limit" if scope != "api" else f"{service} returned 429/rate limit"
            self.log.error(
                "Entering API cooldown after 429 until %s",
                self.datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.save_state(state)
            return

        if len(epochs) >= max_errors:
            state[cooldown_until_key] = current + self.repeated_error_seconds
            state[cooldown_reason_key] = (
                f"too many {scope}/{service} API errors in the last hour"
                if scope != "api"
                else f"too many {service} API errors in the last hour"
            )
            self.log.error(
                "Entering API cooldown after repeated errors until %s",
                self.datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.save_state(state)
