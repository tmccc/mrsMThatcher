"""Own bounded tweet-create classification and canonical response diagnostics.

XCreateDiagnostics binds current ID validation, logger and clock per invocation,
then uses fixed limits and sibling value helpers directly. It preserves evidence
bounds, native failures, field evaluation order and canonical JSON/hash ordering.
Instances retain these capabilities without retaining caller state. Import and
construction perform no file, environment, provider, clock or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import base64
import hashlib
import json
import math
from typing import Any


X_CREATE_RESPONSE_ANOMALY_EVENT = "X_CREATE_RESPONSE_ANOMALY_V1"
X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION = 1
X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES = 16 * 1024
X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS = (
    "request-id",
    "traceparent",
    "x-correlation-id",
    "x-request-id",
    "x-transaction-id",
)
_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS = 512
_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS = 256
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT = 64
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS = 256


def _x_create_diagnostic_json_type(value: object) -> str:
    """Return a stable JSON-oriented type name for decoded evidence."""

    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) in {int, float}:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return f"python:{type(value).__module__}.{type(value).__qualname__}"


def _bounded_x_create_diagnostic_text(
    value: object,
    *,
    maximum_characters: int,
) -> str | None:
    """Return one bounded control-free diagnostic string, or no value."""

    if value is None:
        return None
    text = str(value)
    if (
        len(text) > maximum_characters
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text)
    ):
        return None
    return text


def _x_create_response_elapsed_ms(
    response: requests.Response,
) -> int | None:
    """Return a finite non-negative Requests elapsed duration in milliseconds."""

    try:
        seconds = float(response.elapsed.total_seconds())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return int(round(seconds * 1000))



@dataclass(frozen=True)
class XCreateDiagnostics:
    """Own bounded create-response evidence and its canonical diagnostic event."""

    valid_post_id: Callable[[object], bool]
    log: Any
    now_epoch: Callable[[], int]

    def anomaly_reason(self, decoded: object) -> str | None:
        """Return why one decoded tweet-create response cannot confirm success."""

        if not isinstance(decoded, dict):
            return "decoded_top_level_not_object"
        if "data" not in decoded:
            return "data_absent"
        data = decoded["data"]
        if not isinstance(data, dict):
            return "data_not_object"
        if "id" not in data:
            return "data_id_absent"
        post_id = data["id"]
        if type(post_id) is str and not post_id.strip():
            return "data_id_blank"
        if not self.valid_post_id(post_id):
            return "data_id_non_numeric"
        return None


    def emit_anomaly(
        self,
        *,
        response: requests.Response,
        transport_authority: TransportAuthority,
        request_payload: dict,
        reason: str,
        raw_body: bytes,
        json_decode_succeeded: bool,
        decoded: object,
        json_error: json.JSONDecodeError | None = None,
    ) -> dict[str, object]:
        """Emit one canonical, bounded ERROR event for an anomalous tweet create.

        ``diagnostic_sha256`` hashes the canonical UTF-8 JSON object before that
        hash field is added.  The complete cached response body is included only
        when it is no larger than ``X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES``.
        """

        raw_body = bytes(raw_body)
        raw_body_complete = (
            len(raw_body) <= X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES
        )
        safe_correlation_headers: dict[str, str] = {}
        for header_name in X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS:
            header_value = _bounded_x_create_diagnostic_text(
                response.headers.get(header_name),
                maximum_characters=_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS,
            )
            if header_value is not None:
                safe_correlation_headers[header_name] = header_value

        target_id: str | None = None
        reply = request_payload.get("reply")
        if isinstance(reply, dict) and type(reply.get("in_reply_to_tweet_id")) is str:
            target_id = reply["in_reply_to_tweet_id"]

        decoded_top_level_type = (
            _x_create_diagnostic_json_type(decoded)
            if json_decode_succeeded
            else None
        )
        decoded_top_level_keys: list[str] | None = None
        decoded_top_level_keys_complete: bool | None = None
        if json_decode_succeeded and isinstance(decoded, dict):
            keys = list(decoded.keys())
            if (
                len(keys) <= _X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT
                and all(
                    type(key) is str
                    and len(key) <= _X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS
                    for key in keys
                )
            ):
                decoded_top_level_keys = sorted(keys)
                decoded_top_level_keys_complete = True
            else:
                decoded_top_level_keys_complete = False

        data_present = (
            json_decode_succeeded
            and isinstance(decoded, dict)
            and "data" in decoded
        )
        data_value = decoded["data"] if data_present else None
        data_type = (
            _x_create_diagnostic_json_type(data_value) if data_present else None
        )
        data_is_object = isinstance(data_value, dict) if data_present else False
        data_id_present = data_is_object and "id" in data_value
        data_id_value = data_value["id"] if data_id_present else None
        data_text_present = data_is_object and "text" in data_value
        data_text_value = data_value["text"] if data_text_present else None
        edit_history_present = data_is_object and "edit_history_tweet_ids" in data_value
        edit_history_value = (
            data_value["edit_history_tweet_ids"] if edit_history_present else None
        )

        diagnostic_data_id_value: object = None
        data_id_value_complete: bool | None = None
        if data_id_present:
            if type(data_id_value) is str:
                if len(data_id_value) <= _X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS:
                    diagnostic_data_id_value = data_id_value
                    data_id_value_complete = True
                else:
                    data_id_value_complete = False
            elif data_id_value is None or type(data_id_value) in {bool, int}:
                diagnostic_data_id_value = data_id_value
                data_id_value_complete = True
            elif type(data_id_value) is float and math.isfinite(data_id_value):
                diagnostic_data_id_value = data_id_value
                data_id_value_complete = True
            else:
                data_id_value_complete = False

        event_without_hash: dict[str, object] = {
            "canonical_payload_sha256": transport_authority.payload_sha256,
            "content_length_header": _bounded_x_create_diagnostic_text(
                response.headers.get("Content-Length"),
                maximum_characters=_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS,
            ),
            "content_type": _bounded_x_create_diagnostic_text(
                response.headers.get("Content-Type"),
                maximum_characters=_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS,
            ),
            "data_id_character_length": (
                len(data_id_value) if type(data_id_value) is str else None
            ),
            "data_id_type": (
                _x_create_diagnostic_json_type(data_id_value)
                if data_id_present
                else None
            ),
            "data_id_value": diagnostic_data_id_value,
            "data_id_value_complete": data_id_value_complete,
            "data_text_character_length": (
                len(data_text_value) if type(data_text_value) is str else None
            ),
            "data_text_type": (
                _x_create_diagnostic_json_type(data_text_value)
                if data_text_present
                else None
            ),
            "data_type": data_type,
            "decoded_top_level_keys": decoded_top_level_keys,
            "decoded_top_level_keys_complete": decoded_top_level_keys_complete,
            "decoded_top_level_type": decoded_top_level_type,
            "edit_history_tweet_ids_count": (
                len(edit_history_value) if type(edit_history_value) is list else None
            ),
            "edit_history_tweet_ids_type": (
                _x_create_diagnostic_json_type(edit_history_value)
                if edit_history_present
                else None
            ),
            "event": X_CREATE_RESPONSE_ANOMALY_EVENT,
            "http_status": int(response.status_code),
            "json_decode_succeeded": json_decode_succeeded,
            "json_error_position": (
                json_error.pos
                if json_error is not None and type(getattr(json_error, "pos", None)) is int
                else None
            ),
            "json_error_type": type(json_error).__name__ if json_error is not None else None,
            "raw_body_base64": (
                base64.b64encode(raw_body).decode("ascii")
                if raw_body_complete
                else None
            ),
            "raw_body_complete": raw_body_complete,
            "raw_body_encoding": "base64" if raw_body_complete else None,
            "raw_body_length": len(raw_body),
            "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
            "reason": reason,
            "recorded_at": self.now_epoch(),
            "request_method": "POST",
            "request_path": "/2/tweets",
            "response_elapsed_ms": _x_create_response_elapsed_ms(response),
            "response_encoding": _bounded_x_create_diagnostic_text(
                response.encoding,
                maximum_characters=_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS,
            ),
            "safe_correlation_headers": safe_correlation_headers,
            "schema_version": X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION,
            "target_id": target_id,
            "transaction_id": transport_authority.transaction_id,
            "transport_lane": transport_authority.lane,
        }
        canonical_without_hash = json.dumps(
            event_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        diagnostic_sha256 = hashlib.sha256(canonical_without_hash).hexdigest()
        event = {**event_without_hash, "diagnostic_sha256": diagnostic_sha256}
        canonical_event = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self.log.error("%s %s", X_CREATE_RESPONSE_ANOMALY_EVENT, canonical_event)
        return event


def raise_x_create_anomaly_outcome(
    reason: str,
    *,
    diagnostic: dict[str, object],
    response: object,
    decoded: object,
    request_method: str,
    request_path: str,
    ambiguous_outcome: type[Exception],
    json_error: json.JSONDecodeError | None = None,
) -> None:
    """Raise the ambiguous outcome described by already emitted anomaly evidence."""
    if reason == "json_decode_error":
        outcome_summary = (
            "X may have accepted the post but returned a non-JSON "
            "successful response"
        )
    elif reason == "decoded_top_level_not_object":
        outcome_summary = (
            "X may have accepted the post but its successful response "
            f"was not a JSON object: {type(decoded).__name__}"
        )
    else:
        outcome_summary = (
            "X may have accepted the post but its successful response "
            "could not confirm a valid numeric data.id"
        )
    error = ambiguous_outcome(
        f"{outcome_summary}; "
        f"reason={reason} "
        f"diagnostic_event={X_CREATE_RESPONSE_ANOMALY_EVENT} "
        f"diagnostic_sha256={diagnostic['diagnostic_sha256']}",
        service="x",
        status_code=response.status_code,
        request_method=request_method,
        request_path=request_path,
        response_body_length=diagnostic["raw_body_length"],
        response_body_sha256=diagnostic["raw_body_sha256"],
        diagnostic_sha256=diagnostic["diagnostic_sha256"],
        diagnostic_event=X_CREATE_RESPONSE_ANOMALY_EVENT,
    )
    if json_error is not None:
        raise error from json_error
    raise error
