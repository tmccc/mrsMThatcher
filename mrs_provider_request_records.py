"""Persist and validate exact provider request bodies without runtime side effects.

The bot calls :func:`record_provider_request` only at its final HTTP transport
boundary.  Digest and prospective-export tools use the bounded reader and
discovery helpers; importing this module performs no filesystem or environment
access.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tempfile
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUEST_RECORD_VERSION = 1
MAX_REQUEST_BODY_BYTES = 32 * 1024 * 1024
MAX_REQUEST_RECORD_BYTES = 2 * MAX_REQUEST_BODY_BYTES + 2 * 1024 * 1024
MAX_COMPRESSED_RECORD_BYTES = MAX_REQUEST_RECORD_BYTES + 1024 * 1024
CALL_ID_RE_CHARS = frozenset("0123456789abcdef-")


class RequestRecordingError(RuntimeError):
    """Report a local capture failure before any provider attempt begins."""

    error_category = "local_request_recording"
    request_attempt_count = 0
    model_call_count = 0


class InvalidRequestRecord(ValueError):
    """Report a corrupt, unsafe, oversized, or hash-mismatched capture."""


def utc_now_text() -> str:
    """Return a timezone-aware UTC timestamp in canonical ``Z`` form."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_call_id() -> str:
    """Return a locally generated unique logical provider-call identifier."""

    return str(uuid.uuid4())


def serialise_request_body(request: Mapping[str, Any]) -> bytes:
    """Return the exact JSON bytes used for capture and HTTP transmission."""

    try:
        body = json.dumps(request, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RequestRecordingError("provider request is not serialisable JSON") from exc
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise RequestRecordingError("provider request exceeds the supported sender limit")
    return body


def request_context_ids(request: Mapping[str, Any]) -> dict[str, str | None]:
    """Extract already-recorded post identities without rebuilding request content."""

    model_input = request.get("input")
    payload_text: object = model_input
    if isinstance(model_input, list) and model_input:
        message = model_input[0]
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, Mapping) and first.get("type") == "input_text":
                    payload_text = first.get("text")
    if not isinstance(payload_text, str):
        return {}
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    identities = payload.get("identities") if isinstance(payload, Mapping) else None
    if not isinstance(identities, Mapping):
        return {}
    result: dict[str, str | None] = {}
    for name in ("root_post_id", "parent_post_id", "subject_post_id"):
        value = identities.get(name)
        if value is None:
            result[name] = None
        elif isinstance(value, str) and value:
            result[name] = value
    return result


def _gzip_record_bytes(record: Mapping[str, Any]) -> bytes:
    """Return a deterministic losslessly compressed JSON record."""

    raw = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > MAX_REQUEST_RECORD_BYTES:
        raise RequestRecordingError("provider request record exceeds its framing limit")
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as handle:
        handle.write(raw)
    compressed = output.getvalue()
    if len(compressed) > MAX_COMPRESSED_RECORD_BYTES:
        raise RequestRecordingError("compressed provider request record exceeds its limit")
    return compressed


def _ensure_private_directory(directory: Path) -> None:
    """Create or validate the private single-owner capture directory."""

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RequestRecordingError("provider request capture directory is not owner-private")


def _fsync_directory(directory: Path) -> None:
    """Make one directory-entry publication durable."""

    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def record_provider_request(
    directory: Path,
    *,
    request: Mapping[str, Any],
    request_body: bytes,
    call_id: str,
    lane: str,
    target_post_id: str,
    endpoint_path: str,
    timeout_seconds: int,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Durably publish one complete request record before provider transmission."""

    try:
        if not _valid_call_id(call_id):
            raise RequestRecordingError("invalid provider request call ID")
        if request_body.decode("utf-8").encode("utf-8") != request_body:
            raise RequestRecordingError("provider request body is not canonical UTF-8")
        digest = hashlib.sha256(request_body).hexdigest()
        record: dict[str, Any] = {
            "record_version": REQUEST_RECORD_VERSION,
            "call_id": call_id,
            "captured_at": captured_at or utc_now_text(),
            "lane": lane,
            "target_post_id": target_post_id,
            "request_body_utf8": request_body.decode("utf-8"),
            "request_body_sha256": digest,
            "request_body_byte_length": len(request_body),
            "transport": {
                "endpoint_path": endpoint_path,
                "timeout_seconds": timeout_seconds,
            },
        }
        for key, value in request_context_ids(request).items():
            if value is not None:
                record[key] = value
        if any(
            key in record
            for key in ("root_post_id", "parent_post_id", "subject_post_id")
        ):
            record["context_id_provenance"] = "final_request.input.identities"
        data = _gzip_record_bytes(record)
        _ensure_private_directory(directory)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{call_id}.", suffix=".tmp", dir=directory,
        )
        temporary = Path(temporary_name)
        final = directory / f"{call_id}.json.gz"
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                if handle.write(data) != len(data):
                    raise OSError("short provider request record write")
                handle.flush()
                os.fsync(handle.fileno())
            # The UUID name is unique. Refuse even an extraordinary collision
            # rather than replacing evidence already retained under that name.
            os.link(temporary, final, follow_symlinks=False)
            temporary.unlink()
            _fsync_directory(directory)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        return record
    except RequestRecordingError:
        raise
    except BaseException as exc:
        raise RequestRecordingError("failed to durably record provider request") from exc


def _valid_call_id(value: object) -> bool:
    """Return whether a value is a safe UUID-shaped capture basename."""

    return (
        isinstance(value, str)
        and 32 <= len(value) <= 64
        and set(value) <= CALL_ID_RE_CHARS
        and "/" not in value
    )


def request_record_path(directory: Path, call_id: str) -> Path:
    """Resolve a call ID to its confined authoritative capture pathname."""

    if not _valid_call_id(call_id):
        raise InvalidRequestRecord("invalid provider request call ID")
    return directory / f"{call_id}.json.gz"


def read_provider_request_record(path: Path) -> dict[str, Any]:
    """Read and fully verify one bounded owner-private gzip JSON capture."""

    try:
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise InvalidRequestRecord("provider request capture directory is unavailable") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o077
    ):
        raise InvalidRequestRecord("provider request capture directory is not owner-private")
    try:
        info = path.lstat()
    except OSError as exc:
        raise InvalidRequestRecord(f"provider request record is unavailable: {path.name}") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size > MAX_COMPRESSED_RECORD_BYTES
    ):
        raise InvalidRequestRecord(f"unsafe or oversized provider request record: {path.name}")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != info.st_dev
            or opened.st_ino != info.st_ino
            or opened.st_size != info.st_size
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise InvalidRequestRecord("provider request record changed while opening")
        compressed = b""
        while len(compressed) <= MAX_COMPRESSED_RECORD_BYTES:
            block = os.read(
                descriptor,
                min(64 * 1024, MAX_COMPRESSED_RECORD_BYTES + 1 - len(compressed)),
            )
            if not block:
                break
            compressed += block
        if len(compressed) != opened.st_size:
            raise InvalidRequestRecord("provider request record read was incomplete")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as zipped:
            raw = zipped.read(MAX_REQUEST_RECORD_BYTES + 1)
            if len(raw) > MAX_REQUEST_RECORD_BYTES or zipped.read(1):
                raise InvalidRequestRecord("provider request record exceeds its framing limit")
        value = json.loads(raw.decode("utf-8"))
    except InvalidRequestRecord:
        raise
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError, zlib.error) as exc:
        raise InvalidRequestRecord(f"corrupt provider request record: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict) or value.get("record_version") != REQUEST_RECORD_VERSION:
        raise InvalidRequestRecord("unsupported provider request record format")
    call_id = value.get("call_id")
    if not _valid_call_id(call_id) or path.name != f"{call_id}.json.gz":
        raise InvalidRequestRecord("provider request record identity mismatch")
    body = value.get("request_body_utf8")
    if not isinstance(body, str):
        raise InvalidRequestRecord("provider request body is unavailable")
    try:
        body_bytes = body.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidRequestRecord("provider request body is invalid UTF-8") from exc
    if len(body_bytes) > MAX_REQUEST_BODY_BYTES:
        raise InvalidRequestRecord("provider request body exceeds the sender limit")
    if value.get("request_body_byte_length") != len(body_bytes):
        raise InvalidRequestRecord("provider request body length mismatch")
    if value.get("request_body_sha256") != hashlib.sha256(body_bytes).hexdigest():
        raise InvalidRequestRecord("provider request body hash mismatch")
    return value


def discover_request_record_paths(directory: Path) -> Iterable[Path]:
    """Yield confined capture files without following directory or file links."""

    try:
        directory_info = directory.lstat()
    except FileNotFoundError:
        return ()
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or directory_info.st_uid != os.geteuid()
        or stat.S_IMODE(directory_info.st_mode) & 0o077
    ):
        raise InvalidRequestRecord(
            "provider request capture path is not an owner-private directory"
        )
    return tuple(
        path for path in directory.iterdir()
        if path.name.endswith(".json.gz")
        and _valid_call_id(path.name[:-8])
    )
