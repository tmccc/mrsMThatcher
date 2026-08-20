#!/usr/bin/env python3
"""Endpoint-aware X error classification and reply-create rejection proofs.

HTTP status is not a remote-write outcome contract.  This module therefore
keeps ordinary API-error classification separate from the much narrower proof
that one exact reply create was rejected.  A proof can be issued only from a
strictly parsed X error object, the exact ``POST /2/tweets`` route, an actual
reply payload, and the consumed transaction authority which reached transport.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from remote_write_transport_journal import (
    _ConsumedXResponse,
    _claim_consumed_x_response_for_reply_rejection,
    _source_binding_matches_current_receipt,
)


X_API_ERROR_REPLY_TARGET_RESTRICTED = "reply_target_restricted"
X_API_ERROR_REPLY_TARGET_UNAVAILABLE = "reply_target_missing_or_inaccessible"
X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE = "lookup_target_missing_or_inaccessible"
X_API_ERROR_GLOBAL_DENIAL = "global_authentication_or_application_denial"
X_API_ERROR_ENDPOINT_NOT_FOUND = "endpoint_or_unclassified_not_found"
X_API_ERROR_OTHER = "other"

_MAX_ERROR_RESPONSE_BYTES = 128 * 1024
_MAX_ERROR_RESPONSE_NODES = 4096
_MAX_ERROR_RESPONSE_DEPTH = 20
_MAX_ERROR_MESSAGES = 64
_MAX_ERROR_MESSAGE_CHARS = 8192
_ROOT_ERROR_FIELDS = frozenset(
    {"detail", "message", "reason", "status", "title", "type", "errors"}
)
_NESTED_ERROR_FIELDS = frozenset(
    {"detail", "message", "reason", "status", "title", "type"}
)
_PROVED_REPLY_REJECTION_MESSAGES = frozenset(
    {
        (
            "you attempted to reply to a tweet that is deleted or not "
            "visible to you."
        ),
        (
            "you can only reply to or quote posts where you are mentioned "
            "or are the author."
        ),
    }
)
_PROVED_REPLY_REJECTION_TYPES = frozenset(
    {
        "about:blank",
        "https://api.x.com/2/problems/not-authorized-for-resource",
    }
)
_POST_ID_RE = re.compile(r"\d{1,30}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VALIDATED_RESPONSE_SECRET = object()
_REJECTION_PROOF_SECRET = object()
_proof_lock = threading.Lock()
_issued_rejection_proofs: dict[int, tuple[object, ...]] = {}


class XErrorResponseValidationError(ValueError):
    """An HTTP response body is not one bounded, strict X error object."""


class ValidatedXErrorResponse:
    """One strict JSON error response and its recognised message fields."""

    __slots__ = (
        "__pid",
        "__status_code",
        "__body_sha256",
        "__messages",
        "__narrow_rejection_envelope",
    )

    def __init__(
        self,
        secret: object,
        *,
        status_code: int,
        body_sha256: str,
        messages: tuple[str, ...],
        narrow_rejection_envelope: bool,
    ) -> None:
        """Construct only through this module's strict response parser."""

        if secret is not _VALIDATED_RESPONSE_SECRET:
            raise XErrorResponseValidationError(
                "validated X error responses cannot be constructed directly"
            )
        self.__pid = os.getpid()
        self.__status_code = status_code
        self.__body_sha256 = body_sha256
        self.__messages = messages
        self.__narrow_rejection_envelope = narrow_rejection_envelope

    def _matches_process_and_status(self, status_code: object) -> bool:
        return (
            os.getpid() == self.__pid
            and type(status_code) is int
            and status_code == self.__status_code
        )

    def _candidate_messages(self) -> tuple[str, ...]:
        if os.getpid() != self.__pid:
            return ()
        return self.__messages

    def _body_sha256_value(self) -> str:
        if os.getpid() != self.__pid:
            return ""
        return self.__body_sha256

    def _is_narrow_rejection_envelope(self) -> bool:
        """Return whether the body contains one error and no conflicting claim."""

        return bool(
            os.getpid() == self.__pid
            and self.__narrow_rejection_envelope is True
        )

    def __reduce__(self) -> object:
        raise TypeError("validated X error responses are not serialisable")


class DeterministicReplyCreateRejectionProof:
    """Opaque, process-local proof bound to one consumed reply transaction."""

    __slots__ = (
        "__pid",
        "__classification",
        "__status_code",
        "__response_sha256",
        "__transaction_identity",
        "__payload_sha256",
        "__target_id",
        "__payload_bytes",
    )

    def __init__(
        self,
        secret: object,
        *,
        classification: str,
        status_code: int,
        response_sha256: str,
        transaction_identity: tuple[object, ...],
        payload_sha256: str,
        target_id: str,
        payload_bytes: bytes,
    ) -> None:
        """Construct only through the consumed-response proof issuer."""

        if secret is not _REJECTION_PROOF_SECRET:
            raise XErrorResponseValidationError(
                "reply-create rejection proofs cannot be constructed directly"
            )
        self.__pid = os.getpid()
        self.__classification = classification
        self.__status_code = status_code
        self.__response_sha256 = response_sha256
        self.__transaction_identity = transaction_identity
        self.__payload_sha256 = payload_sha256
        self.__target_id = target_id
        self.__payload_bytes = payload_bytes

    def __reduce__(self) -> object:
        raise TypeError("reply-create rejection proofs are not serialisable")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise XErrorResponseValidationError(
                f"X error response contains duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise XErrorResponseValidationError(
        f"X error response contains invalid JSON constant: {value}"
    )


def _validate_bounded_json(value: object) -> None:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_ERROR_RESPONSE_NODES:
            raise XErrorResponseValidationError("X error response is too complex")
        if depth > _MAX_ERROR_RESPONSE_DEPTH:
            raise XErrorResponseValidationError("X error response is too deeply nested")
        if item is None or type(item) in {bool, int}:
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise XErrorResponseValidationError(
                    "X error response contains a non-finite number"
                )
            return
        if type(item) is str:
            if len(item) > _MAX_ERROR_RESPONSE_BYTES:
                raise XErrorResponseValidationError(
                    "X error response contains an oversized string"
                )
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or not key:
                    raise XErrorResponseValidationError(
                        "X error response keys must be non-empty strings"
                    )
                visit(child, depth + 1)
            return
        raise XErrorResponseValidationError(
            f"X error response contains unsupported value type: {type(item).__name__}"
        )

    visit(value, 0)


def _validated_error_messages(
    document: Mapping[str, Any],
    *,
    status_code: int,
) -> tuple[tuple[str, ...], bool]:
    unknown_root_fields = set(document).difference(_ROOT_ERROR_FIELDS)
    if unknown_root_fields:
        raise XErrorResponseValidationError(
            "X error response contains fields outside the bounded error schema"
        )
    if "status" in document and (
        type(document["status"]) is not int
        or document["status"] != status_code
    ):
        raise XErrorResponseValidationError(
            "X error response status does not match its HTTP status"
        )
    for key in ("title", "type"):
        if key in document and type(document[key]) is not str:
            raise XErrorResponseValidationError(
                f"X error response {key} must be a string"
            )

    messages: list[str] = []
    substantive_messages: list[str] = []
    titles: list[str] = []
    problem_types: list[str] = []

    def add_message_fields(container: Mapping[str, Any]) -> None:
        title = container.get("title")
        if title is not None:
            if (
                type(title) is not str
                or not title
                or len(title) > _MAX_ERROR_MESSAGE_CHARS
            ):
                raise XErrorResponseValidationError(
                    "X error response title is not one bounded message"
                )
            messages.append(title)
            titles.append(title)
            if len(messages) > _MAX_ERROR_MESSAGES:
                raise XErrorResponseValidationError(
                    "X error response contains too many messages"
                )
        for key in ("message", "detail", "reason"):
            if key not in container:
                continue
            value = container[key]
            if type(value) is not str or not value or len(value) > _MAX_ERROR_MESSAGE_CHARS:
                raise XErrorResponseValidationError(
                    f"X error response {key} is not one bounded message"
                )
            messages.append(value)
            substantive_messages.append(value)
            if len(messages) > _MAX_ERROR_MESSAGES:
                raise XErrorResponseValidationError(
                    "X error response contains too many messages"
                )

    # The legacy classifier inspected the complete error rendering and gave
    # recognised global-denial language precedence over target-specific
    # detail.  Preserve that precedence for the semantically meaningful title
    # field (while deliberately excluding the URI-like ``type`` field).
    add_message_fields(document)
    if "type" in document:
        problem_types.append(document["type"])
    if "errors" in document:
        errors = document["errors"]
        if type(errors) is not list or not errors or len(errors) > _MAX_ERROR_MESSAGES:
            raise XErrorResponseValidationError(
                "X error response errors must be one bounded non-empty list"
            )
        for item in errors:
            if type(item) is not dict:
                raise XErrorResponseValidationError(
                    "X error response errors entries must be objects"
                )
            if set(item).difference(_NESTED_ERROR_FIELDS):
                raise XErrorResponseValidationError(
                    "X error response contains nested fields outside the "
                    "bounded error schema"
                )
            if "status" in item and (
                type(item["status"]) is not int
                or item["status"] != status_code
            ):
                raise XErrorResponseValidationError(
                    "nested X error status does not match its HTTP status"
                )
            if "type" in item and type(item["type"]) is not str:
                raise XErrorResponseValidationError(
                    "nested X error type must be a string"
                )
            if "type" in item:
                problem_types.append(item["type"])
            add_message_fields(item)
    root_has_substantive = any(
        key in document for key in ("message", "detail", "reason")
    )
    errors = document.get("errors")
    narrow_shape = bool(
        len(substantive_messages) == 1
        and substantive_messages[0].casefold()
        in _PROVED_REPLY_REJECTION_MESSAGES
        and all(
            problem_type.casefold() in _PROVED_REPLY_REJECTION_TYPES
            for problem_type in problem_types
        )
        and all(
            title.casefold()
            in {"forbidden", "not found", "authorization error"}
            for title in titles
        )
        and (
            errors is None
            or (
                type(errors) is list
                and len(errors) == 1
                and not root_has_substantive
            )
        )
    )
    return tuple(messages), narrow_shape


def parse_validated_x_error_response(
    body: bytes,
    *,
    status_code: int,
) -> ValidatedXErrorResponse:
    """Parse one bounded strict JSON object using the X error-field schema."""

    if type(body) is not bytes or type(status_code) is not int:
        raise XErrorResponseValidationError("X error response input is invalid")
    if not body or len(body) > _MAX_ERROR_RESPONSE_BYTES:
        raise XErrorResponseValidationError(
            "X error response is empty or oversized"
        )
    try:
        body_text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise XErrorResponseValidationError(
            "X error response is not strict UTF-8"
        ) from exc
    try:
        document = json.loads(
            body_text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except XErrorResponseValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise XErrorResponseValidationError(
            "X error response is not strict JSON"
        ) from exc
    if type(document) is not dict:
        raise XErrorResponseValidationError(
            "X error response must be one JSON object"
        )
    _validate_bounded_json(document)
    messages, narrow_rejection_envelope = _validated_error_messages(
        document,
        status_code=status_code,
    )
    return ValidatedXErrorResponse(
        _VALIDATED_RESPONSE_SECRET,
        status_code=status_code,
        body_sha256=hashlib.sha256(body).hexdigest(),
        messages=messages,
        narrow_rejection_envelope=narrow_rejection_envelope,
    )


def _error_request_path(error: object) -> str:
    raw = str(getattr(error, "request_path", "") or "")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    return parsed.path or raw.split("?", 1)[0]


def _error_is_tweet_lookup(error: object) -> bool:
    return (
        str(getattr(error, "request_method", "") or "").upper() == "GET"
        and re.fullmatch(r"/2/tweets/\d+", _error_request_path(error)) is not None
    )


def _error_is_post_create(error: object) -> bool:
    return (
        str(getattr(error, "request_method", "") or "").upper() == "POST"
        and _error_request_path(error) == "/2/tweets"
    )


def _error_candidate_messages(error: object) -> tuple[str, ...]:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "x_error_response", None)
    if isinstance(response, ValidatedXErrorResponse):
        if not response._matches_process_and_status(status_code):
            return ()
        messages = response._candidate_messages()
        if getattr(error, "x_error_message_fallback", True) is True:
            return (*messages, str(error))
        return messages
    if getattr(error, "x_error_message_fallback", True) is False:
        return ()
    return (str(error),)


def classify_x_api_error(error: object) -> str:
    """Classify X failures using endpoint context and recognised messages."""

    if getattr(error, "service", None) != "x":
        return X_API_ERROR_OTHER

    status_code = getattr(error, "status_code", None)
    messages = tuple(message.casefold() for message in _error_candidate_messages(error))
    is_lookup = _error_is_tweet_lookup(error)
    is_post_create = _error_is_post_create(error)

    global_denial_markers = (
        "invalid or expired token",
        "could not authenticate you",
        "authentication credentials",
        "client forbidden",
        "this application is not permitted",
        "application is not permitted",
        "unsupported authentication",
    )
    if status_code in {401, 403} and any(
        marker in message
        for message in messages
        for marker in global_denial_markers
    ):
        return X_API_ERROR_GLOBAL_DENIAL

    reply_restriction_markers = (
        "reply to this conversation is not allowed",
        "not been mentioned or otherwise engaged by the author",
        "not allowed to reply",
        "only reply to or quote posts where you are mentioned or are the author",
        "author has restricted who can reply",
    )
    target_unavailable_markers = (
        "tweet that is deleted or not visible to you",
        "post that is deleted or not visible to you",
        "tweet is deleted or not visible",
        "post is deleted or not visible",
        "tweet is unavailable",
        "post is unavailable",
        "could not find tweet",
        "could not find post",
        "tweet not found",
        "post not found",
    )

    if status_code == 403 and any(
        marker in message
        for message in messages
        for marker in reply_restriction_markers
    ):
        if is_post_create or not getattr(error, "request_path", None):
            return X_API_ERROR_REPLY_TARGET_RESTRICTED

    if status_code in {403, 404} and any(
        marker in message
        for message in messages
        for marker in target_unavailable_markers
    ):
        if is_post_create or not getattr(error, "request_path", None):
            return X_API_ERROR_REPLY_TARGET_UNAVAILABLE
        if is_lookup:
            return X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE

    if status_code == 404:
        if is_lookup:
            return X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE
        return X_API_ERROR_ENDPOINT_NOT_FOUND

    if status_code == 403:
        return X_API_ERROR_GLOBAL_DENIAL
    return X_API_ERROR_OTHER


def _canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes | None:
    if type(payload) is not dict:
        return None
    try:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if decoded != payload or type(decoded) is not dict:
        return None
    return encoded


class _ValidatedReplyCreateError:
    """Minimal classifier input built only from one strict response body."""

    def __init__(
        self,
        *,
        status_code: int,
        response: ValidatedXErrorResponse,
    ) -> None:
        self.service = "x"
        self.status_code = status_code
        self.request_method = "POST"
        self.request_path = "/2/tweets"
        self.x_error_response = response
        self.x_error_message_fallback = False

    def __str__(self) -> str:
        return "strict validated X reply-create error"


def _prove_reply_create_rejection_from_actual_response(
    *,
    raw_body: bytes,
    status_code: int,
    payload: Mapping[str, Any],
    authority: object,
    consumed_response: _ConsumedXResponse,
    actual_response: object,
) -> tuple[
    ValidatedXErrorResponse | None,
    DeterministicReplyCreateRejectionProof | None,
]:
    """Parse and possibly prove rejection inside the request coordinator."""

    try:
        validated = parse_validated_x_error_response(
            raw_body,
            status_code=status_code,
        )
    except XErrorResponseValidationError:
        return None, None
    error = _ValidatedReplyCreateError(
        status_code=status_code,
        response=validated,
    )
    proof = _issue_reply_create_rejection_from_consumed_response(
        error,
        payload=payload,
        authority=authority,
        consumed_response=consumed_response,
        actual_response=actual_response,
    )
    return validated, proof


def _issue_reply_create_rejection_from_consumed_response(
    error: object,
    *,
    payload: Mapping[str, Any],
    authority: object,
    consumed_response: _ConsumedXResponse,
    actual_response: object,
) -> DeterministicReplyCreateRejectionProof | None:
    """Issue one registered proof for the response to an exact consumed attempt."""

    response = getattr(error, "x_error_response", None)
    status_code = getattr(error, "status_code", None)
    if (
        getattr(error, "service", None) != "x"
        or getattr(error, "request_method", None) != "POST"
        or getattr(error, "request_path", None) != "/2/tweets"
        or not isinstance(response, ValidatedXErrorResponse)
        or not response._matches_process_and_status(status_code)
        or not response._is_narrow_rejection_envelope()
        or classify_x_api_error(error)
        not in {
            X_API_ERROR_REPLY_TARGET_RESTRICTED,
            X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
        }
    ):
        return None
    reply = payload.get("reply") if type(payload) is dict else None
    target_id = reply.get("in_reply_to_tweet_id") if type(reply) is dict else None
    if (
        type(reply) is not dict
        or set(reply) != {"in_reply_to_tweet_id"}
        or type(target_id) is not str
        or _POST_ID_RE.fullmatch(target_id) is None
        or getattr(authority, "lifecycle_state", None) != "attempting"
        or getattr(authority, "lane", None) != "conversational_reply"
    ):
        return None
    payload_bytes = _canonical_payload_bytes(payload)
    if payload_bytes is None:
        return None
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    if payload_sha256 != getattr(authority, "payload_sha256", None):
        return None
    transaction_identity = (
        getattr(authority, "transaction_id", None),
        str(Path(str(getattr(authority, "journal_path", ""))).absolute()),
        getattr(authority, "journal_sha256", None),
        getattr(authority, "journal_device", None),
        getattr(authority, "journal_inode", None),
        getattr(authority, "journal_ctime_ns", None),
        str(Path(str(getattr(authority, "fence_path", ""))).absolute()),
        getattr(authority, "fence_sha256", None),
        getattr(authority, "fence_device", None),
        getattr(authority, "fence_inode", None),
        getattr(authority, "fence_ctime_ns", None),
        getattr(authority, "lane", None),
        getattr(authority, "source_receipt_basename", None),
        getattr(authority, "source_validator_id", None),
        getattr(authority, "source_binding_identity", None),
        id(authority),
    )
    strings = (
        transaction_identity[0],
        transaction_identity[1],
        transaction_identity[2],
        transaction_identity[6],
        transaction_identity[7],
        transaction_identity[11],
        transaction_identity[12],
        transaction_identity[13],
    )
    integers = (
        transaction_identity[3],
        transaction_identity[4],
        transaction_identity[5],
        transaction_identity[8],
        transaction_identity[9],
        transaction_identity[10],
        transaction_identity[14],
        transaction_identity[15],
    )
    if (
        any(type(item) is not str or not item for item in strings)
        or any(type(item) is not int or item < 0 for item in integers)
        or _SHA256_RE.fullmatch(str(transaction_identity[0])) is None
        or _SHA256_RE.fullmatch(str(transaction_identity[2])) is None
        or _SHA256_RE.fullmatch(str(transaction_identity[7])) is None
    ):
        return None
    classification = classify_x_api_error(error)
    source_binding = _claim_consumed_x_response_for_reply_rejection(
        consumed_response,
        authority=authority,
        response=actual_response,
        status_code=status_code,
        body_sha256=response._body_sha256_value(),
    )
    if source_binding is None:
        return None
    proof: DeterministicReplyCreateRejectionProof | None = None
    try:
        proof = DeterministicReplyCreateRejectionProof(
            _REJECTION_PROOF_SECRET,
            classification=classification,
            status_code=status_code,
            response_sha256=response._body_sha256_value(),
            transaction_identity=transaction_identity,
            payload_sha256=payload_sha256,
            target_id=target_id,
            payload_bytes=payload_bytes,
        )
        registration = (
            proof,
            classification,
            status_code,
            response._body_sha256_value(),
            transaction_identity,
            payload_sha256,
            target_id,
            payload_bytes,
            "coordinator_pending",
            authority,
            source_binding,
        )
        with _proof_lock:
            _issued_rejection_proofs[id(proof)] = registration
        return proof
    except BaseException:
        if proof is not None:
            invalidate_reply_create_rejection_proof(proof)
        raise


def _registered_proof_matches(
    registration: tuple[object, ...] | None,
    proof: DeterministicReplyCreateRejectionProof,
) -> bool:
    return bool(
        registration is not None
        and len(registration) == 11
        and registration[0] is proof
        and os.getpid() == proof._DeterministicReplyCreateRejectionProof__pid
        and proof._DeterministicReplyCreateRejectionProof__classification
        == registration[1]
        and proof._DeterministicReplyCreateRejectionProof__status_code
        == registration[2]
        and proof._DeterministicReplyCreateRejectionProof__response_sha256
        == registration[3]
        and proof._DeterministicReplyCreateRejectionProof__transaction_identity
        == registration[4]
        and proof._DeterministicReplyCreateRejectionProof__payload_sha256
        == registration[5]
        and proof._DeterministicReplyCreateRejectionProof__target_id
        == registration[6]
        and proof._DeterministicReplyCreateRejectionProof__payload_bytes
        == registration[7]
        and id(registration[9])
        == proof._DeterministicReplyCreateRejectionProof__transaction_identity[15]
        and id(registration[10])
        == proof._DeterministicReplyCreateRejectionProof__transaction_identity[14]
    )


def _activate_coordinator_reply_create_rejection_proof(
    proof: object,
    *,
    authority: object,
) -> bool:
    """Activate a pending proof only after the request caller receives it."""

    if not isinstance(proof, DeterministicReplyCreateRejectionProof):
        return False
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if (
            not _registered_proof_matches(registration, proof)
            or registration is None
            or registration[8] != "coordinator_pending"
            or registration[9] is not authority
        ):
            return False
        _issued_rejection_proofs[id(proof)] = (
            *registration[:8],
            "issued",
            *registration[9:],
        )
    return True


def begin_reply_create_rejection_transport_retirement(
    proof: object,
    *,
    authority: object,
    source_binding: object,
    payload_sha256: str,
    target_id: str,
) -> bool:
    """Claim one issued proof immediately before exact journal retirement."""

    if not isinstance(proof, DeterministicReplyCreateRejectionProof):
        return False
    transaction_identity = (
        getattr(authority, "transaction_id", None),
        str(Path(str(getattr(authority, "journal_path", ""))).absolute()),
        getattr(authority, "journal_sha256", None),
        getattr(authority, "journal_device", None),
        getattr(authority, "journal_inode", None),
        getattr(authority, "journal_ctime_ns", None),
        str(Path(str(getattr(authority, "fence_path", ""))).absolute()),
        getattr(authority, "fence_sha256", None),
        getattr(authority, "fence_device", None),
        getattr(authority, "fence_inode", None),
        getattr(authority, "fence_ctime_ns", None),
        getattr(authority, "lane", None),
        getattr(authority, "source_receipt_basename", None),
        getattr(authority, "source_validator_id", None),
        getattr(authority, "source_binding_identity", None),
        id(authority),
    )
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if (
            not _registered_proof_matches(registration, proof)
            or registration is None
            or registration[8] != "issued"
            or registration[1]
            not in {
                X_API_ERROR_REPLY_TARGET_RESTRICTED,
                X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
            }
            or registration[2] not in {403, 404}
            or _SHA256_RE.fullmatch(str(registration[3])) is None
            or registration[4] != transaction_identity
            or registration[5] != payload_sha256
            or registration[6] != target_id
            or registration[9] is not authority
            or registration[10] is not source_binding
        ):
            return False
        _issued_rejection_proofs[id(proof)] = (
            *registration[:8],
            "transport_retiring",
            *registration[9:],
        )
    return True


def complete_reply_create_rejection_transport_retirement(proof: object) -> bool:
    """Mark an exactly claimed proof usable for source-receipt retirement."""

    if not isinstance(proof, DeterministicReplyCreateRejectionProof):
        return False
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if (
            not _registered_proof_matches(registration, proof)
            or registration is None
            or registration[8] != "transport_retiring"
        ):
            return False
        _issued_rejection_proofs[id(proof)] = (
            *registration[:8],
            "transport_retired",
            *registration[9:],
        )
    return True


def invalidate_reply_create_rejection_proof(proof: object) -> None:
    """Make a claimed proof non-reusable after a failed local transition."""

    if not isinstance(proof, DeterministicReplyCreateRejectionProof):
        return
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if registration is not None and registration[0] is proof:
            _issued_rejection_proofs.pop(id(proof), None)


def reply_create_rejection_payload(proof: object) -> dict[str, Any] | None:
    """Return a copy of the exact proved-rejected payload for fail-closed logging."""

    if (
        not isinstance(proof, DeterministicReplyCreateRejectionProof)
        or os.getpid() != proof._DeterministicReplyCreateRejectionProof__pid
    ):
        return None
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if (
            not _registered_proof_matches(registration, proof)
            or registration is None
            or registration[8] != "transport_retired"
        ):
            return None
    try:
        value = json.loads(
            proof._DeterministicReplyCreateRejectionProof__payload_bytes.decode(
                "utf-8"
            )
        )
    except (UnicodeError, json.JSONDecodeError):
        return None
    return value if type(value) is dict else None


def claim_reply_create_rejection_for_receipt_retirement(
    proof: object,
    *,
    target_id: str,
    receipt_path: Path,
    receipt: Mapping[str, Any],
) -> bool:
    """Consume a transport-retired proof before deleting its source receipt."""

    if not isinstance(proof, DeterministicReplyCreateRejectionProof):
        return False
    with _proof_lock:
        registration = _issued_rejection_proofs.get(id(proof))
        if (
            not _registered_proof_matches(registration, proof)
            or registration is None
            or registration[8] != "transport_retired"
            or registration[6] != target_id
            or not _source_binding_matches_current_receipt(
                registration[10],
                receipt_path=receipt_path,
                expected_receipt=receipt,
            )
        ):
            return False
        _issued_rejection_proofs.pop(id(proof), None)
    return True


def reset_reply_create_rejection_proofs_for_tests() -> None:
    """Clear process-local proof issuance state for isolated tests."""

    with _proof_lock:
        _issued_rejection_proofs.clear()


def _reply_create_rejection_proof_registry_is_empty() -> bool:
    """Return whether test reconfiguration cannot strand an issued proof."""

    with _proof_lock:
        return not _issued_rejection_proofs
