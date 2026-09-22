"""Own bounded single-call model transport, retries and provider error metadata.

Each root call supplies current transport settings and runtime boundaries.
Retry control delegates transport-failure classification while preserving prior
429 evidence, exception causes and health-progress completion. This module only
imports the standard library and performs no runtime access.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

from mrs_provider_request_records import (
    RequestRecordingError,
    new_call_id,
    record_provider_request,
    serialise_request_body,
)


@dataclass(frozen=True)
class ReplyModelTransport:
    """Own bounded provider requests and their retry/error metadata."""
    log: logging.Logger
    model: str
    reasoning_effort: str
    monotonic: Callable
    require_remote_operation_unpaused: Callable
    report_bot_health_progress: Callable
    requests: object
    base_url: str
    api_key: str = field(repr=False)
    sleep: Callable
    now_epoch: Callable
    error_type: type[Exception]
    request_record_directory: Path | None = None
    log_event: Callable | None = None

    def _emit(self, event: str, **fields: object) -> None:
        """Emit non-authoritative lifecycle telemetry without affecting transport."""

        if self.log_event is None:
            return
        try:
            self.log_event(event, **fields)
        except Exception:
            try:
                self.log.exception(
                    "Failed to emit provider request lifecycle event=%s call_id=%s",
                    event,
                    fields.get("call_id"),
                )
            except Exception:
                pass

    def definite_connection_failure_before_transmission(
        self,
        error: BaseException,
    ) -> bool:
        """Return whether the failure proves the request was never transmitted."""
        if isinstance(error, self.requests.ConnectTimeout):
            return True
        if not isinstance(error, self.requests.ConnectionError):
            return False
        current: BaseException | None = error
        while current is not None:
            if type(current).__name__ in {
                "NewConnectionError",
                "NameResolutionError",
                "ConnectionRefusedError",
                "gaierror",
            }:
                return True
            current = current.__cause__ or current.__context__
        return False

    def error(
        self,
        message: str,
        *,
        category: str,
        status_code: int | None = None,
        reset_epoch: int | None = None,
        retry_after_seconds: int | None = None,
        request_attempt_count: int = 1,
        call_id: str | None = None,
    ) -> Exception:
        """Build the current API exception with provider accounting metadata."""
        error = self.error_type(
            message,
            service="openai",
            status_code=status_code,
            reset_epoch=reset_epoch,
        )
        error.error_category = category
        error.retry_after_seconds = retry_after_seconds
        error.request_attempt_count = request_attempt_count
        error.call_id = call_id
        return error

    def retry_metadata(self, response: object) -> tuple[int | None, int | None]:
        """Return bounded Retry-After metadata for provider cooldown accounting."""

        headers = getattr(response, "headers", {})
        if not isinstance(headers, Mapping):
            headers = {}
        current = self.now_epoch()
        delays: list[int] = []
        raw = str(headers.get("Retry-After") or "").strip()
        if raw:
            try:
                numeric = float(raw)
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(raw)
                    if parsed.tzinfo is not None:
                        numeric = parsed.timestamp() - current
                    else:
                        numeric = float("nan")
                except (TypeError, ValueError, OverflowError):
                    numeric = float("nan")
            if math.isfinite(numeric) and numeric >= 0:
                # Bound durable cooldown metadata, never turn a long delay into an
                # immediate retry merely because it exceeds our accepted range.
                delays.append(min(math.ceil(numeric), 7 * 24 * 60 * 60))
        for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
            raw_reset = str(headers.get(key) or "").strip()
            parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", raw_reset)
            if parts and "".join(number + unit for number, unit in parts) == raw_reset:
                delay = sum(float(number) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for number, unit in parts)
                if math.isfinite(delay):
                    delays.append(min(math.ceil(delay), 7 * 24 * 60 * 60))
        seconds = max(delays) if delays else None
        return (current + seconds if seconds is not None else None, seconds)

    def call(
        self,
        *,
        request: dict[str, object],
        timeout_seconds: int,
        lane: str,
        target_id: str,
    ) -> dict[str, object]:
        """Send one executable Responses request, retrying only proved non-execution."""

        call_id = new_call_id()
        try:
            request_body = serialise_request_body(request)
            if self.request_record_directory is not None:
                record = record_provider_request(
                    self.request_record_directory,
                    request=request,
                    request_body=request_body,
                    call_id=call_id,
                    lane=lane,
                    target_post_id=target_id,
                    endpoint_path="/responses",
                    timeout_seconds=timeout_seconds,
                )
        except RequestRecordingError as exc:
            exc.call_id = call_id
            self._emit(
                "provider_request_recording_failed",
                call_id=call_id,
                lane=lane,
                target_id=target_id,
                request_attempt_count=0,
                failure_category="local_request_recording",
            )
            raise
        if self.request_record_directory is not None:
            self._emit(
                "provider_request_prepared",
                call_id=call_id,
                lane=lane,
                target_id=target_id,
                captured_at=record["captured_at"],
                request_body_sha256=record["request_body_sha256"],
                request_body_byte_length=record["request_body_byte_length"],
                record_version=record["record_version"],
            )

        self.log.info(
            "Calling single-call reply provider=OpenAI model=%s "
            "reasoning_effort=%s lane=%s target_id=%s",
            self.model,
            self.reasoning_effort,
            lane,
            target_id,
        )
        started = self.monotonic()
        first_429_seen = False
        first_429_retry_metadata: tuple[int | None, int | None] = (None, None)
        for attempt in (1, 2):
            self.require_remote_operation_unpaused(
                f"OpenAI single-call reply target {target_id}"
            )
            self._emit(
                "provider_request_attempt_started",
                call_id=call_id,
                attempt_number=attempt,
                lane=lane,
                target_id=target_id,
            )
            self.report_bot_health_progress("ai_call")
            try:
                response = self.requests.post(
                    f"{self.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    data=request_body,
                    timeout=timeout_seconds,
                    allow_redirects=False,
                )
            except self.requests.RequestException as exc:
                if (
                    attempt == 1
                    and self.definite_connection_failure_before_transmission(exc)
                ):
                    self._emit(
                        "provider_request_attempt_outcome",
                        call_id=call_id,
                        attempt_number=attempt,
                        lane=lane,
                        target_id=target_id,
                        outcome="known_pre_transmission_failure",
                    )
                    self.log.warning(
                        "OpenAI single-call reply had a definite pre-transmission "
                        "connection failure; retrying once target_id=%s",
                        target_id,
                    )
                    continue
                raise self._transport_error(
                    exc, attempt=attempt, first_429_seen=first_429_seen,
                    first_429_retry_metadata=first_429_retry_metadata,
                    call_id=call_id,
                ) from exc
            finally:
                self.report_bot_health_progress("ai_call")
            self._emit(
                "provider_request_attempt_outcome",
                call_id=call_id,
                attempt_number=attempt,
                lane=lane,
                target_id=target_id,
                outcome="response_received",
                provider_status_code=getattr(response, "status_code", None),
            )
            if response.status_code == 429:
                if attempt == 1:
                    first_429_seen = True
                    with self._closing_response(response):
                        first_429_retry_metadata = self.retry_metadata(response)
                        self.log.warning(
                            "OpenAI single-call reply returned pre-execution HTTP %s; "
                            "respecting bounded retry delay target_id=%s",
                            response.status_code,
                            target_id,
                        )
                    # A one-second maximum keeps the bot responsive. Longer or
                    # unknown provider delays become durable cooldowns upstream.
                    delay = first_429_retry_metadata[1]
                    if delay is not None and delay <= 1:
                        self.sleep(delay)
                        continue
                    raise self.error(
                        "OpenAI rate limit requires candidate deferral",
                        category="provider_http_429", status_code=429,
                        reset_epoch=first_429_retry_metadata[0],
                        retry_after_seconds=delay, request_attempt_count=attempt,
                        call_id=call_id,
                    )
            data = self._decode_response(
                response,
                attempt=attempt,
                first_429_seen=first_429_seen,
                first_429_retry_metadata=first_429_retry_metadata,
                call_id=call_id,
            )
            result = {
                "response": data,
                "latency_ms": max(0, round((self.monotonic() - started) * 1000)),
                "request_attempt_count": attempt,
                "call_id": call_id,
            }
            if first_429_seen:
                result.update(
                    {
                        "provider_status_code": 429,
                        "provider_reset_epoch": first_429_retry_metadata[0],
                        "provider_retry_after_seconds": first_429_retry_metadata[1],
                    }
                )
            return result
        raise AssertionError("unreachable OpenAI request retry state")

    def _transport_error(
        self,
        exc: Exception,
        *,
        attempt: int,
        first_429_seen: bool,
        first_429_retry_metadata: tuple[int | None, int | None],
        call_id: str,
    ) -> Exception:
        """Classify a failed request while retaining earlier rate-limit evidence."""
        self._emit(
            "provider_request_attempt_outcome",
            call_id=call_id,
            attempt_number=attempt,
            outcome="ambiguous_transport_outcome",
        )
        if first_429_seen:
            reset_epoch, retry_after_seconds = first_429_retry_metadata
            return self.error(
                "OpenAI single-call reply transport failed after an "
                "earlier HTTP 429",
                category=(
                    "provider_ambiguous_timeout"
                    if isinstance(exc, self.requests.Timeout)
                    else "provider_transport"
                ),
                status_code=429,
                reset_epoch=reset_epoch,
                retry_after_seconds=retry_after_seconds,
                request_attempt_count=attempt,
                call_id=call_id,
            )
        return self.error(
            "OpenAI single-call reply transport failed",
            category=(
                "provider_ambiguous_timeout"
                if isinstance(exc, self.requests.Timeout)
                else "provider_transport"
            ),
            request_attempt_count=attempt,
            call_id=call_id,
        )

    @contextmanager
    def _closing_response(self, response: object) -> Iterator[None]:
        """Close an acquired response after reads, including unexpected failures."""

        try:
            yield
        finally:
            close_response = getattr(response, "close", None)
            if callable(close_response):
                close_response()

    def _envelope_error(
        self,
        message: str,
        *,
        attempt: int,
        first_429_seen: bool,
        first_429_retry_metadata: tuple[int | None, int | None],
        call_id: str,
    ) -> Exception:
        """Build a decoding/shape error after cleanup, preserving prior rate limits."""
        return self.error(
            message,
            category="provider_envelope",
            status_code=429 if first_429_seen else None,
            reset_epoch=first_429_retry_metadata[0] if first_429_seen else None,
            retry_after_seconds=first_429_retry_metadata[1] if first_429_seen else None,
            request_attempt_count=attempt,
            call_id=call_id,
        )

    def _decode_response(
        self,
        response: object,
        *,
        attempt: int,
        first_429_seen: bool,
        first_429_retry_metadata: tuple[int | None, int | None],
        call_id: str,
    ) -> dict[str, object]:
        """Decode a response and close it before constructing provider errors."""

        json_error: ValueError | TypeError | None = None
        try:
            with self._closing_response(response):
                http_error = not 200 <= response.status_code < 300
                if http_error:
                    observed_status_code = response.status_code
                    reset_epoch, retry_after_seconds = self.retry_metadata(response)
                    status_code = observed_status_code
                    if first_429_seen:
                        status_code = 429
                        if observed_status_code != 429 or reset_epoch is None:
                            reset_epoch, retry_after_seconds = first_429_retry_metadata
                else:
                    try:
                        data = response.json()
                    except (ValueError, TypeError) as exc:
                        json_error = exc
                        raise
        except (ValueError, TypeError) as exc:
            # Cleanup and metadata errors keep their own identity/category.
            # Re-raising the decoder error through cleanup also retains it as
            # context if closing or the provider error factory itself fails.
            if exc is not json_error:
                raise
            raise self._envelope_error(
                "OpenAI single-call reply returned malformed JSON",
                attempt=attempt,
                first_429_seen=first_429_seen,
                first_429_retry_metadata=first_429_retry_metadata,
                call_id=call_id,
            ) from exc
        if http_error:
            raise self.error(
                (
                    f"OpenAI single-call reply returned HTTP {observed_status_code}"
                    + (
                        " after an earlier HTTP 429"
                        if observed_status_code != status_code
                        else ""
                    )
                ),
                category=f"provider_http_{observed_status_code}",
                status_code=status_code,
                reset_epoch=reset_epoch,
                retry_after_seconds=retry_after_seconds,
                request_attempt_count=attempt,
                call_id=call_id,
            )
        if not isinstance(data, dict):
            raise self._envelope_error(
                "OpenAI single-call reply response is not an object",
                attempt=attempt,
                first_429_seen=first_429_seen,
                first_429_retry_metadata=first_429_retry_metadata,
                call_id=call_id,
            )
        return data
