"""Durable, network-free obligations for optional historical-context replies.

The outbox records a confirmed main post separately from the lifecycle of its
optional context reply.  It intentionally knows nothing about X, reply
formatting, or the existing transactional reply receipts.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator


SCHEMA_VERSION = 1

MAIN_POST_CONFIRMED = "main_post_confirmed"
CONTEXT_REPLY_PENDING = "context_reply_pending"
CONTEXT_REPLY_ATTEMPTING = "context_reply_attempting"
FAILED_RETRYABLE = "context_reply_failed_retryable"
FAILED_TERMINAL = "context_reply_failed_terminal"
CONFIRMED = "context_reply_confirmed"
NOT_REQUIRED = "context_reply_not_required"

CONTEXT_REPLY_STATES = frozenset(
    {
        CONTEXT_REPLY_PENDING,
        CONTEXT_REPLY_ATTEMPTING,
        FAILED_RETRYABLE,
        FAILED_TERMINAL,
        CONFIRMED,
        NOT_REQUIRED,
    }
)
ATTEMPTABLE_STATES = frozenset({CONTEXT_REPLY_PENDING, FAILED_RETRYABLE})
DUE_STATES = ATTEMPTABLE_STATES | {CONTEXT_REPLY_ATTEMPTING}

MAX_ATTEMPTS_LIMIT = 20
MAX_BACKOFF_SECONDS_LIMIT = 7 * 24 * 60 * 60
MAX_EPOCH = 253_402_300_799
MAX_QUOTE_TEXT_LENGTH = 10_000
MAX_ERROR_LENGTH = 2_000
MAX_REASON_LENGTH = 1_000
MAX_DUE_LIMIT = 1_000
MAX_OUTBOX_OBLIGATIONS = 1_000
TERMINAL_RETENTION_LIMIT = 500

FINAL_STATES = frozenset({FAILED_TERMINAL, CONFIRMED, NOT_REQUIRED})

_POST_ID_RE = re.compile(r"\d{1,30}\Z")
_QUOTE_ID_RE = re.compile(r"[0-9a-f]{64}\Z")


class OutboxValidationError(RuntimeError):
    """The durable outbox document is malformed or internally inconsistent."""


class OutboxConflictError(RuntimeError):
    """A requested enqueue or transition conflicts with durable state."""


class OutboxPolicyMismatchError(OutboxConflictError):
    """The configured retry policy differs from the durable policy."""


class OutboxCapacityError(OutboxConflictError):
    """The outbox cannot accept another obligation without losing active work."""


class OutboxWorkerBusy(OutboxConflictError):
    """Another process or thread currently owns context-reply execution."""


def _atomic_write_json(path: Path, value: Any) -> None:
    """Atomically replace *path*, fsyncing both the file and its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OutboxValidationError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> Any:
    raise OutboxValidationError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_loads(payload: str) -> Any:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except OutboxValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise OutboxValidationError("outbox is not valid JSON") from exc


def _is_int(value: Any) -> bool:
    return type(value) is int


def _valid_epoch(value: Any) -> bool:
    return _is_int(value) and 0 <= value <= MAX_EPOCH


def _valid_text(value: Any, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum_length
        and "\x00" not in value
    )


def _normalise_post_id(value: Any, field: str) -> str:
    if _is_int(value):
        value = str(value)
    if not isinstance(value, str) or not _POST_ID_RE.fullmatch(value):
        raise ValueError(f"{field} must be a 1-30 digit post id")
    return value


def _require_epoch(value: Any, field: str) -> int:
    if not _valid_epoch(value):
        raise ValueError(f"{field} must be an integer Unix epoch in range")
    return value


def _require_attempt_number(value: Any) -> int:
    if not _is_int(value) or not 1 <= value <= MAX_ATTEMPTS_LIMIT:
        raise ValueError(
            f"attempt_number must be an integer from 1 to {MAX_ATTEMPTS_LIMIT}"
        )
    return value


def _require_quote_id(value: Any) -> str:
    if not isinstance(value, str) or not _QUOTE_ID_RE.fullmatch(value):
        raise ValueError("quote_id must be 64 lowercase hexadecimal characters")
    return value


def _require_quote_text(value: Any) -> str:
    if not _valid_text(value, MAX_QUOTE_TEXT_LENGTH):
        raise ValueError(
            f"quote_text must be nonempty, NUL-free, and at most "
            f"{MAX_QUOTE_TEXT_LENGTH} characters"
        )
    return value


def _require_reason(value: Any) -> str:
    if not _valid_text(value, MAX_REASON_LENGTH):
        raise ValueError(
            f"reason must be nonempty, NUL-free, and at most "
            f"{MAX_REASON_LENGTH} characters"
        )
    return value


def _normalise_error(value: BaseException | str) -> str:
    if isinstance(value, BaseException):
        result = f"{type(value).__name__}: {value}"
    elif isinstance(value, str):
        result = value
    else:
        raise ValueError("error must be an exception or string")
    result = result.replace("\x00", "\\x00").strip()
    if not result:
        result = "unspecified historical-context failure"
    suffix = "… [truncated]"
    if len(result) > MAX_ERROR_LENGTH:
        result = result[: MAX_ERROR_LENGTH - len(suffix)] + suffix
    return result


def _backoff_seconds(policy: dict[str, int], attempt_number: int) -> int:
    exponent = max(0, attempt_number - 1)
    return min(
        policy["base_backoff_seconds"] * (2**exponent),
        policy["max_backoff_seconds"],
    )


def _validate_policy(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "max_attempts",
        "base_backoff_seconds",
        "max_backoff_seconds",
    }:
        raise OutboxValidationError("invalid retry_policy fields")
    maximum_attempts = value["max_attempts"]
    base_backoff = value["base_backoff_seconds"]
    maximum_backoff = value["max_backoff_seconds"]
    if not _is_int(maximum_attempts) or not 1 <= maximum_attempts <= MAX_ATTEMPTS_LIMIT:
        raise OutboxValidationError("invalid retry_policy max_attempts")
    if not _is_int(base_backoff) or not 1 <= base_backoff <= MAX_BACKOFF_SECONDS_LIMIT:
        raise OutboxValidationError("invalid retry_policy base_backoff_seconds")
    if (
        not _is_int(maximum_backoff)
        or not base_backoff <= maximum_backoff <= MAX_BACKOFF_SECONDS_LIMIT
    ):
        raise OutboxValidationError("invalid retry_policy max_backoff_seconds")


def _validate_failure(
    value: Any,
    *,
    attempt_count: int,
    updated_epoch: int,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "attempt_number",
        "failed_epoch",
        "error",
    }:
        raise OutboxValidationError("invalid context reply failure fields")
    if value["attempt_number"] != attempt_count:
        raise OutboxValidationError("failure attempt_number does not match attempt_count")
    if not _valid_epoch(value["failed_epoch"]):
        raise OutboxValidationError("invalid context reply failure failed_epoch")
    if value["failed_epoch"] != updated_epoch:
        raise OutboxValidationError("failure failed_epoch does not match updated_epoch")
    if not _valid_text(value["error"], MAX_ERROR_LENGTH):
        raise OutboxValidationError("invalid context reply failure error")


def _validate_payload_identity(value: dict[str, Any]) -> None:
    if not isinstance(value.get("quote_id"), str) or not _QUOTE_ID_RE.fullmatch(
        value["quote_id"]
    ):
        raise OutboxValidationError("invalid context reply quote_id")
    if not _valid_text(value.get("quote_text"), MAX_QUOTE_TEXT_LENGTH):
        raise OutboxValidationError("invalid context reply quote_text")


def _validate_context_reply(
    value: Any,
    *,
    main_post_confirmed_epoch: int,
    policy: dict[str, int],
) -> None:
    if not isinstance(value, dict) or value.get("state") not in CONTEXT_REPLY_STATES:
        raise OutboxValidationError("invalid context reply state")
    state = value["state"]

    if state == NOT_REQUIRED:
        if set(value) != {"state", "reason", "updated_epoch"}:
            raise OutboxValidationError("invalid not_required context reply fields")
        if not _valid_text(value["reason"], MAX_REASON_LENGTH):
            raise OutboxValidationError("invalid not_required reason")
        if (
            not _valid_epoch(value["updated_epoch"])
            or value["updated_epoch"] < main_post_confirmed_epoch
        ):
            raise OutboxValidationError("invalid not_required updated_epoch")
        return

    _validate_payload_identity(value)
    attempt_count = value.get("attempt_count")
    updated_epoch = value.get("updated_epoch")
    if (
        not _is_int(attempt_count)
        or not 0 <= attempt_count <= policy["max_attempts"]
        or not _valid_epoch(updated_epoch)
        or updated_epoch < main_post_confirmed_epoch
    ):
        raise OutboxValidationError("invalid context reply attempt/timestamp metadata")

    if state == CONTEXT_REPLY_PENDING:
        if set(value) != {
            "state",
            "quote_id",
            "quote_text",
            "attempt_count",
            "next_attempt_epoch",
            "backoff_seconds",
            "updated_epoch",
        }:
            raise OutboxValidationError("invalid pending context reply fields")
        if (
            attempt_count != 0
            or not _is_int(value["backoff_seconds"])
            or value["backoff_seconds"] != 0
            or not _valid_epoch(value["next_attempt_epoch"])
            or value["next_attempt_epoch"] != main_post_confirmed_epoch
            or updated_epoch != main_post_confirmed_epoch
        ):
            raise OutboxValidationError("invalid pending context reply metadata")
        return

    if state == CONTEXT_REPLY_ATTEMPTING:
        permitted = {
            "state",
            "quote_id",
            "quote_text",
            "attempt_count",
            "started_epoch",
            "updated_epoch",
        }
        if "previous_failure" in value:
            permitted.add("previous_failure")
        if set(value) != permitted:
            raise OutboxValidationError("invalid attempting context reply fields")
        if (
            not 1 <= attempt_count <= policy["max_attempts"]
            or not _valid_epoch(value["started_epoch"])
            or value["started_epoch"] != updated_epoch
        ):
            raise OutboxValidationError("invalid attempting context reply metadata")
        previous_failure = value.get("previous_failure")
        if attempt_count == 1 and previous_failure is not None:
            raise OutboxValidationError(
                "first context attempt cannot have a previous failure"
            )
        if attempt_count > 1:
            if previous_failure is None:
                raise OutboxValidationError(
                    "retried context attempt must retain its previous failure"
                )
            _validate_failure(
                previous_failure,
                attempt_count=attempt_count - 1,
                updated_epoch=previous_failure.get("failed_epoch"),
            )
            if previous_failure["failed_epoch"] > updated_epoch:
                raise OutboxValidationError(
                    "previous failure occurs after context attempt started"
                )
        return

    if state == FAILED_RETRYABLE:
        if set(value) != {
            "state",
            "quote_id",
            "quote_text",
            "attempt_count",
            "failure",
            "next_attempt_epoch",
            "backoff_seconds",
            "updated_epoch",
        }:
            raise OutboxValidationError("invalid retryable failure fields")
        if not 1 <= attempt_count < policy["max_attempts"]:
            raise OutboxValidationError("invalid retryable failure attempt_count")
        expected_backoff = _backoff_seconds(policy, attempt_count)
        if (
            not _is_int(value["backoff_seconds"])
            or value["backoff_seconds"] != expected_backoff
            or not _valid_epoch(value["next_attempt_epoch"])
            or updated_epoch > MAX_EPOCH - expected_backoff
            or value["next_attempt_epoch"] != updated_epoch + expected_backoff
        ):
            raise OutboxValidationError("invalid retryable scheduling metadata")
        _validate_failure(
            value["failure"],
            attempt_count=attempt_count,
            updated_epoch=updated_epoch,
        )
        return

    if state == FAILED_TERMINAL:
        if set(value) != {
            "state",
            "quote_id",
            "quote_text",
            "attempt_count",
            "failure",
            "updated_epoch",
        }:
            raise OutboxValidationError("invalid terminal failure fields")
        if not 1 <= attempt_count <= policy["max_attempts"]:
            raise OutboxValidationError("invalid terminal failure attempt_count")
        _validate_failure(
            value["failure"],
            attempt_count=attempt_count,
            updated_epoch=updated_epoch,
        )
        return

    if set(value) != {
        "state",
        "quote_id",
        "quote_text",
        "attempt_count",
        "reply_post_id",
        "confirmed_epoch",
        "updated_epoch",
    }:
        raise OutboxValidationError("invalid confirmed context reply fields")
    if (
        not 1 <= attempt_count <= policy["max_attempts"]
        or not isinstance(value["reply_post_id"], str)
        or not _POST_ID_RE.fullmatch(value["reply_post_id"])
        or not _valid_epoch(value["confirmed_epoch"])
        or value["confirmed_epoch"] != updated_epoch
    ):
        raise OutboxValidationError("invalid confirmed context reply metadata")


def _validate_obligation(
    key: str,
    value: Any,
    *,
    policy: dict[str, int],
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "parent_post_id",
        "main_post",
        "context_reply",
    }:
        raise OutboxValidationError("invalid obligation fields")
    if (
        not isinstance(key, str)
        or not _POST_ID_RE.fullmatch(key)
        or value["parent_post_id"] != key
    ):
        raise OutboxValidationError("invalid obligation parent_post_id")
    main_post = value["main_post"]
    if (
        not isinstance(main_post, dict)
        or set(main_post) != {"state", "confirmed_epoch"}
        or main_post["state"] != MAIN_POST_CONFIRMED
        or not _valid_epoch(main_post["confirmed_epoch"])
    ):
        raise OutboxValidationError("invalid confirmed main_post")
    _validate_context_reply(
        value["context_reply"],
        main_post_confirmed_epoch=main_post["confirmed_epoch"],
        policy=policy,
    )


def _validate_document(value: Any) -> None:
    required = {
        "schema_version",
        "retry_policy",
        "obligations",
    }
    allowed = required | {"retired_parent_post_id_floor"}
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        raise OutboxValidationError("invalid outbox document fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise OutboxValidationError("unsupported outbox schema_version")
    _validate_policy(value["retry_policy"])
    obligations = value["obligations"]
    if not isinstance(obligations, dict):
        raise OutboxValidationError("outbox obligations must be an object")
    retired_floor = value.get("retired_parent_post_id_floor", "0")
    if not isinstance(retired_floor, str) or not _POST_ID_RE.fullmatch(retired_floor):
        raise OutboxValidationError("invalid retired parent-post id floor")
    for key, obligation in obligations.items():
        _validate_obligation(key, obligation, policy=value["retry_policy"])
        if int(key) <= int(retired_floor):
            raise OutboxValidationError(
                "active obligation is not newer than the retired parent-post floor"
            )


class HistoricalContextOutbox:
    """Atomic durable store for confirmed-main-post context obligations."""

    def __init__(
        self,
        path: Path | str,
        *,
        max_attempts: int = 5,
        base_backoff_seconds: int = 60,
        max_backoff_seconds: int = 3_600,
    ):
        """Initialise an outbox at *path* with bounded retry settings."""
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self.worker_lock_path = self.path.with_name(f"{self.path.name}.worker.lock")
        self._policy = {
            "max_attempts": max_attempts,
            "base_backoff_seconds": base_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
        }
        try:
            _validate_policy(self._policy)
        except OutboxValidationError as exc:
            raise ValueError(str(exc)) from exc

    @property
    def max_attempts(self) -> int:
        """Return the durable retry-attempt ceiling."""
        return self._policy["max_attempts"]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @contextmanager
    def worker_lock(self) -> Iterator[None]:
        """Hold the single nonblocking worker lease across remote reply work."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.worker_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as exc:
                raise OutboxWorkerBusy(
                    "historical-context outbox worker is already active"
                ) from exc
            yield
        finally:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _empty_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "retry_policy": dict(self._policy),
            "retired_parent_post_id_floor": "0",
            "obligations": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_document()
        try:
            payload = self.path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise OutboxValidationError("outbox is not valid UTF-8") from exc
        value = _strict_json_loads(payload)
        _validate_document(value)
        if value["retry_policy"] != self._policy:
            raise OutboxPolicyMismatchError(
                "configured retry policy conflicts with durable outbox policy"
            )
        return value

    def _write_unlocked(self, document: dict[str, Any]) -> None:
        _validate_document(document)
        _atomic_write_json(self.path, document)

    @staticmethod
    def _prune_terminal_unlocked(
        document: dict[str, Any],
        *,
        reserve_slots: int,
    ) -> list[str]:
        """Bound retained terminal records without ever deleting active work."""
        if not _is_int(reserve_slots) or reserve_slots < 0:
            raise ValueError("reserve_slots must be a non-negative integer")
        obligations = document["obligations"]
        terminal = [
            (
                int(obligation["context_reply"]["updated_epoch"]),
                int(parent_id),
                parent_id,
            )
            for parent_id, obligation in obligations.items()
            if obligation["context_reply"]["state"] in FINAL_STATES
        ]
        remove_count = max(0, len(terminal) - TERMINAL_RETENTION_LIMIT)
        remaining_after_retention = len(obligations) - remove_count
        remove_count += max(
            0,
            remaining_after_retention + reserve_slots - MAX_OUTBOX_OBLIGATIONS,
        )
        remove_count = min(remove_count, len(terminal))

        # The scalar retirement floor can represent only a numeric prefix.
        # Pruning a newer terminal item while an older active item remains
        # would advance the floor past legitimate work and make the next read
        # fail validation.  Limit deletion to the all-terminal prefix; the
        # overall obligation ceiling still bounds storage when an older active
        # item temporarily prevents terminal-retention pruning.
        removable_prefix: list[tuple[int, int, str]] = []
        for parent_id, obligation in sorted(
            obligations.items(),
            key=lambda item: int(item[0]),
        ):
            if obligation["context_reply"]["state"] not in FINAL_STATES:
                break
            removable_prefix.append(
                (
                    int(obligation["context_reply"]["updated_epoch"]),
                    int(parent_id),
                    parent_id,
                )
            )
        removed = [
            parent_id
            for _epoch, _numeric_id, parent_id in removable_prefix[:remove_count]
        ]
        for parent_id in removed:
            del obligations[parent_id]
        if removed:
            document["retired_parent_post_id_floor"] = str(
                max(
                    int(document.get("retired_parent_post_id_floor", "0")),
                    *(int(parent_id) for parent_id in removed),
                )
            )
        if len(obligations) + reserve_slots > MAX_OUTBOX_OBLIGATIONS:
            raise OutboxCapacityError(
                "historical-context outbox is full of active obligations"
            )
        return removed

    @staticmethod
    def _copy(value: Any) -> Any:
        return copy.deepcopy(value)

    def snapshot(self) -> dict[str, Any]:
        """Return a validated detached snapshot without creating the JSON file."""
        with self._locked():
            return self._copy(self._read_unlocked())

    def verify_writable(self) -> None:
        """Verify a slot can be durably reserved before a main post is sent."""
        with self._locked():
            document = self._read_unlocked()
            self._prune_terminal_unlocked(document, reserve_slots=1)
            self._write_unlocked(document)

    def get(self, parent_post_id: str | int) -> dict[str, Any] | None:
        """Return one detached obligation, or ``None`` if it is unknown."""
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        with self._locked():
            value = self._read_unlocked()["obligations"].get(parent_id)
            return None if value is None else self._copy(value)

    def enqueue(
        self,
        parent_post_id: str | int,
        *,
        main_post_confirmed_epoch: int,
        quote_id: str | None = None,
        quote_text: str | None = None,
        not_required_reason: str = "no_context_reply_requested",
    ) -> dict[str, Any]:
        """Idempotently record a confirmed main post and its optional context."""
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        confirmed_epoch = _require_epoch(
            main_post_confirmed_epoch,
            "main_post_confirmed_epoch",
        )
        has_quote_id = quote_id is not None
        has_quote_text = quote_text is not None
        if has_quote_id != has_quote_text:
            raise ValueError("quote_id and quote_text must be provided together")

        if has_quote_id:
            canonical_quote_id = _require_quote_id(quote_id)
            canonical_quote_text = _require_quote_text(quote_text)
            if not_required_reason != "no_context_reply_requested":
                raise ValueError(
                    "not_required_reason cannot be customised when context is pending"
                )
            context_reply = {
                "state": CONTEXT_REPLY_PENDING,
                "quote_id": canonical_quote_id,
                "quote_text": canonical_quote_text,
                "attempt_count": 0,
                "next_attempt_epoch": confirmed_epoch,
                "backoff_seconds": 0,
                "updated_epoch": confirmed_epoch,
            }
        else:
            context_reply = {
                "state": NOT_REQUIRED,
                "reason": _require_reason(not_required_reason),
                "updated_epoch": confirmed_epoch,
            }

        proposed = {
            "parent_post_id": parent_id,
            "main_post": {
                "state": MAIN_POST_CONFIRMED,
                "confirmed_epoch": confirmed_epoch,
            },
            "context_reply": context_reply,
        }

        with self._locked():
            document = self._read_unlocked()
            existing = document["obligations"].get(parent_id)
            if existing is not None:
                if existing["main_post"] != proposed["main_post"]:
                    raise OutboxConflictError(
                        "parent post confirmation conflicts with durable obligation"
                    )
                current_context = existing["context_reply"]
                if current_context["state"] == NOT_REQUIRED:
                    # A final no-reply decision deliberately has no quote payload.
                    # Replaying the main-post enqueue must never recreate it.
                    same_identity = True
                elif context_reply["state"] == NOT_REQUIRED:
                    # A later configuration change must not erase an already-durable
                    # pending/retryable obligation. Preserve it for the worker, which
                    # can make a separately recorded terminal or no-reply decision.
                    same_identity = True
                else:
                    same_identity = (
                        current_context.get("quote_id") == context_reply["quote_id"]
                        and current_context.get("quote_text") == context_reply["quote_text"]
                    )
                if not same_identity:
                    raise OutboxConflictError(
                        "context reply identity conflicts with durable obligation"
                    )
                return self._copy(existing)
            if int(parent_id) <= int(
                document.get("retired_parent_post_id_floor", "0")
            ):
                raise OutboxConflictError(
                    "parent post was already retired from the bounded outbox"
                )
            self._prune_terminal_unlocked(document, reserve_slots=1)
            document["obligations"][parent_id] = proposed
            self._write_unlocked(document)
            return self._copy(proposed)

    def due(self, now_epoch: int, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return due or interrupted obligations ordered by schedule and parent id."""
        now = _require_epoch(now_epoch, "now_epoch")
        if not _is_int(limit) or not 1 <= limit <= MAX_DUE_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {MAX_DUE_LIMIT}")
        with self._locked():
            obligations = self._read_unlocked()["obligations"].values()
            due_items = [
                obligation
                for obligation in obligations
                if (
                    obligation["context_reply"]["state"]
                    == CONTEXT_REPLY_ATTEMPTING
                    or (
                        obligation["context_reply"]["state"]
                        in ATTEMPTABLE_STATES
                        and obligation["context_reply"]["next_attempt_epoch"] <= now
                    )
                )
            ]
            due_items.sort(
                key=lambda obligation: (
                    obligation["context_reply"].get(
                        "next_attempt_epoch",
                        obligation["context_reply"]["updated_epoch"],
                    ),
                    int(obligation["parent_post_id"]),
                )
            )
            return self._copy(due_items[:limit])

    def next_attempt_number(self, parent_post_id: str | int) -> int:
        """Return the next legal attempt number for an attemptable obligation."""
        obligation = self._require_obligation(parent_post_id)
        context_reply = obligation["context_reply"]
        if context_reply["state"] == CONTEXT_REPLY_ATTEMPTING:
            raise OutboxConflictError(
                "context reply attempt is already durably claimed"
            )
        if context_reply["state"] not in ATTEMPTABLE_STATES:
            raise OutboxConflictError("context reply is not attemptable")
        return context_reply["attempt_count"] + 1

    def claim_attempt(
        self,
        parent_post_id: str | int,
        *,
        started_epoch: int,
    ) -> dict[str, Any]:
        """Durably claim one attempt before formatting or remote reply work."""
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        epoch = _require_epoch(started_epoch, "started_epoch")
        with self._locked():
            document = self._read_unlocked()
            obligation = document["obligations"].get(parent_id)
            if obligation is None:
                raise OutboxConflictError("unknown parent post obligation")
            context_reply = obligation["context_reply"]
            if context_reply["state"] == CONTEXT_REPLY_ATTEMPTING:
                raise OutboxConflictError(
                    "context reply attempt is already durably claimed"
                )
            if context_reply["state"] not in ATTEMPTABLE_STATES:
                raise OutboxConflictError("context reply is not attemptable")
            attempt_number = context_reply["attempt_count"] + 1
            if attempt_number > self._policy["max_attempts"]:
                raise OutboxConflictError("context reply attempt limit exceeded")
            if epoch < context_reply["updated_epoch"]:
                raise ValueError("started_epoch precedes the previous context update")
            if epoch < context_reply["next_attempt_epoch"]:
                raise ValueError("context reply retry is not due")
            claimed = {
                "state": CONTEXT_REPLY_ATTEMPTING,
                **self._attempt_identity(context_reply),
                "attempt_count": attempt_number,
                "started_epoch": epoch,
                "updated_epoch": epoch,
            }
            if context_reply["state"] == FAILED_RETRYABLE:
                claimed["previous_failure"] = copy.deepcopy(
                    context_reply["failure"]
                )
            obligation["context_reply"] = claimed
            self._write_unlocked(document)
            return self._copy(obligation)

    def _require_obligation(self, parent_post_id: str | int) -> dict[str, Any]:
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        with self._locked():
            obligation = self._read_unlocked()["obligations"].get(parent_id)
            if obligation is None:
                raise OutboxConflictError("unknown parent post obligation")
            return self._copy(obligation)

    @staticmethod
    def _attempt_identity(context_reply: dict[str, Any]) -> dict[str, str]:
        return {
            "quote_id": context_reply["quote_id"],
            "quote_text": context_reply["quote_text"],
        }

    def _record_attempt_outcome(
        self,
        parent_post_id: str | int,
        *,
        attempt_number: int,
        target_state: str,
        event_epoch: int,
        error: BaseException | str | None = None,
        reply_post_id: str | int | None = None,
    ) -> dict[str, Any]:
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        attempt = _require_attempt_number(attempt_number)
        epoch = _require_epoch(event_epoch, "event_epoch")
        error_text = _normalise_error(error) if error is not None else None
        canonical_reply_id = (
            _normalise_post_id(reply_post_id, "reply_post_id")
            if reply_post_id is not None
            else None
        )

        with self._locked():
            document = self._read_unlocked()
            obligation = document["obligations"].get(parent_id)
            if obligation is None:
                raise OutboxConflictError("unknown parent post obligation")
            context_reply = obligation["context_reply"]

            if context_reply["state"] == target_state and context_reply.get(
                "attempt_count"
            ) == attempt:
                desired = self._build_attempt_outcome(
                    context_reply,
                    attempt_number=attempt,
                    target_state=target_state,
                    event_epoch=epoch,
                    error_text=error_text,
                    reply_post_id=canonical_reply_id,
                )
                if context_reply != desired:
                    raise OutboxConflictError(
                        "duplicate transition has conflicting outcome metadata"
                    )
                return self._copy(obligation)

            current_attempt = context_reply.get("attempt_count")
            if _is_int(current_attempt) and current_attempt > attempt:
                return self._copy(obligation)
            if context_reply["state"] != CONTEXT_REPLY_ATTEMPTING:
                raise OutboxConflictError(
                    "context reply outcome requires a durably claimed attempt"
                )
            if attempt != context_reply["attempt_count"]:
                raise OutboxConflictError(
                    "context reply outcome does not match the claimed attempt"
                )
            if attempt > self._policy["max_attempts"]:
                raise OutboxConflictError("context reply attempt limit exceeded")
            if (
                target_state == FAILED_RETRYABLE
                and attempt >= self._policy["max_attempts"]
            ):
                raise OutboxConflictError(
                    "final permitted attempt must be recorded as terminal or confirmed"
                )
            if epoch < obligation["main_post"]["confirmed_epoch"]:
                raise ValueError("event_epoch precedes main post confirmation")
            if epoch < context_reply["updated_epoch"]:
                raise ValueError("event_epoch precedes the previous context update")

            obligation["context_reply"] = self._build_attempt_outcome(
                context_reply,
                attempt_number=attempt,
                target_state=target_state,
                event_epoch=epoch,
                error_text=error_text,
                reply_post_id=canonical_reply_id,
            )
            self._write_unlocked(document)
            return self._copy(obligation)

    def _build_attempt_outcome(
        self,
        context_reply: dict[str, Any],
        *,
        attempt_number: int,
        target_state: str,
        event_epoch: int,
        error_text: str | None,
        reply_post_id: str | None,
    ) -> dict[str, Any]:
        identity = self._attempt_identity(context_reply)
        if target_state == FAILED_RETRYABLE:
            if error_text is None or reply_post_id is not None:
                raise ValueError("retryable failure requires only error metadata")
            backoff = _backoff_seconds(self._policy, attempt_number)
            if event_epoch > MAX_EPOCH - backoff:
                raise ValueError("retry schedule exceeds supported epoch range")
            return {
                "state": FAILED_RETRYABLE,
                **identity,
                "attempt_count": attempt_number,
                "failure": {
                    "attempt_number": attempt_number,
                    "failed_epoch": event_epoch,
                    "error": error_text,
                },
                "next_attempt_epoch": event_epoch + backoff,
                "backoff_seconds": backoff,
                "updated_epoch": event_epoch,
            }
        if target_state == FAILED_TERMINAL:
            if error_text is None or reply_post_id is not None:
                raise ValueError("terminal failure requires only error metadata")
            return {
                "state": FAILED_TERMINAL,
                **identity,
                "attempt_count": attempt_number,
                "failure": {
                    "attempt_number": attempt_number,
                    "failed_epoch": event_epoch,
                    "error": error_text,
                },
                "updated_epoch": event_epoch,
            }
        if target_state != CONFIRMED:
            raise ValueError(f"unsupported target state: {target_state}")
        if error_text is not None or reply_post_id is None:
            raise ValueError("confirmed outcome requires only reply_post_id metadata")
        return {
            "state": CONFIRMED,
            **identity,
            "attempt_count": attempt_number,
            "reply_post_id": reply_post_id,
            "confirmed_epoch": event_epoch,
            "updated_epoch": event_epoch,
        }

    def record_retryable_failure(
        self,
        parent_post_id: str | int,
        *,
        attempt_number: int,
        error: BaseException | str,
        failed_epoch: int,
    ) -> dict[str, Any]:
        """Record one definite retryable failure with bounded exponential delay."""
        return self._record_attempt_outcome(
            parent_post_id,
            attempt_number=attempt_number,
            target_state=FAILED_RETRYABLE,
            event_epoch=failed_epoch,
            error=error,
        )

    def record_terminal_failure(
        self,
        parent_post_id: str | int,
        *,
        attempt_number: int,
        error: BaseException | str,
        failed_epoch: int,
    ) -> dict[str, Any]:
        """Record one definite terminal failure and retain it for inspection."""
        return self._record_attempt_outcome(
            parent_post_id,
            attempt_number=attempt_number,
            target_state=FAILED_TERMINAL,
            event_epoch=failed_epoch,
            error=error,
        )

    def record_confirmed(
        self,
        parent_post_id: str | int,
        *,
        attempt_number: int,
        reply_post_id: str | int,
        confirmed_epoch: int,
    ) -> dict[str, Any]:
        """Record the independently confirmed context reply."""
        return self._record_attempt_outcome(
            parent_post_id,
            attempt_number=attempt_number,
            target_state=CONFIRMED,
            event_epoch=confirmed_epoch,
            reply_post_id=reply_post_id,
        )

    def mark_not_required(
        self,
        parent_post_id: str | int,
        *,
        reason: str,
        decided_epoch: int,
    ) -> dict[str, Any]:
        """Stop an unattempted pending context reply when preparation declines it."""
        parent_id = _normalise_post_id(parent_post_id, "parent_post_id")
        canonical_reason = _require_reason(reason)
        epoch = _require_epoch(decided_epoch, "decided_epoch")
        desired = {
            "state": NOT_REQUIRED,
            "reason": canonical_reason,
            "updated_epoch": epoch,
        }
        with self._locked():
            document = self._read_unlocked()
            obligation = document["obligations"].get(parent_id)
            if obligation is None:
                raise OutboxConflictError("unknown parent post obligation")
            context_reply = obligation["context_reply"]
            if context_reply["state"] == NOT_REQUIRED:
                if context_reply != desired:
                    raise OutboxConflictError(
                        "duplicate not_required transition has conflicting metadata"
                    )
                return self._copy(obligation)
            first_claimed_attempt = (
                context_reply["state"] == CONTEXT_REPLY_ATTEMPTING
                and context_reply["attempt_count"] == 1
                and "previous_failure" not in context_reply
            )
            if not first_claimed_attempt:
                raise OutboxConflictError(
                    "only a durably claimed first context decision can become not_required"
                )
            if epoch < context_reply["updated_epoch"]:
                raise ValueError("decided_epoch precedes the claimed context attempt")
            obligation["context_reply"] = desired
            self._write_unlocked(document)
            return self._copy(obligation)


__all__ = [
    "ATTEMPTABLE_STATES",
    "CONFIRMED",
    "CONTEXT_REPLY_ATTEMPTING",
    "CONTEXT_REPLY_PENDING",
    "CONTEXT_REPLY_STATES",
    "DUE_STATES",
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
    "HistoricalContextOutbox",
    "MAIN_POST_CONFIRMED",
    "MAX_OUTBOX_OBLIGATIONS",
    "NOT_REQUIRED",
    "OutboxCapacityError",
    "OutboxConflictError",
    "OutboxPolicyMismatchError",
    "OutboxValidationError",
    "OutboxWorkerBusy",
    "TERMINAL_RETENTION_LIMIT",
]
