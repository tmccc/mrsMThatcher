"""Strict runtime JSON and local-configuration loading/coercion.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import os
from typing import Any


def load_strict_runtime_json(
    handle_or_document,
    *,
    label: str,
    parse_floats_as_decimal: bool = False,
    Decimal: Any,
    json: Any,
    math: Any,
) -> object:
    """Load one UTF-8 control/config document without ambiguous JSON.

    ``parse_floats_as_decimal`` preserves numeric-token semantics for callers
    which must validate mathematical integrality before any binary rounding.
    """

    def reject_duplicate_names(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains a duplicate object name")
            value[key] = item
        return value

    def reject_nonfinite_constant(value: str) -> object:
        raise ValueError(f"{label} contains a non-finite JSON constant")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite JSON number")
        return parsed

    def parse_finite_decimal(value: str) -> Decimal:
        parsed = Decimal(value)
        if not parsed.is_finite():
            raise ValueError(f"{label} contains a non-finite JSON number")
        return parsed

    if isinstance(handle_or_document, (bytes, str)):
        document = handle_or_document
    else:
        document = handle_or_document.read()
    if isinstance(document, bytes):
        document = document.decode("utf-8", errors="strict")
    elif type(document) is not str:
        raise ValueError(f"{label} reader returned unsupported content")

    return json.loads(
        document,
        object_pairs_hook=reject_duplicate_names,
        parse_constant=reject_nonfinite_constant,
        parse_float=(
            parse_finite_decimal
            if parse_floats_as_decimal
            else parse_finite_float
        ),
    )


def _coerce_local_config_value(
    key: str,
    value: object,
    current_value: object,
    *,
    LOCAL_CONFIG_NON_NEGATIVE_INT_KEYS: Any,
    LOCAL_CONFIG_POSITIVE_INT_KEYS: Any,
    math: Any,
) -> object:
    if isinstance(current_value, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in {"1", "true", "yes", "on"}:
                return True
            if low in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"{key} must be a boolean")

    if isinstance(current_value, int) and not isinstance(current_value, bool):
        if type(value) is not int:
            raise ValueError(f"{key} must be a JSON integer")
        coerced = value
        if key in LOCAL_CONFIG_NON_NEGATIVE_INT_KEYS and coerced < 0:
            raise ValueError(f"{key} must be non-negative")
        if key in LOCAL_CONFIG_POSITIVE_INT_KEYS and coerced <= 0:
            raise ValueError(f"{key} must be positive")
        return coerced

    if isinstance(current_value, float):
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number, not a boolean")
        coerced = float(value)
        if key in {
            "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
            "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
            "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
            "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
        }:
            if not math.isfinite(coerced):
                raise ValueError(f"{key} must be finite")
            if coerced < 0:
                raise ValueError(f"{key} must be non-negative")
        return coerced

    if isinstance(current_value, str):
        if type(value) is not str:
            raise ValueError(f"{key} must be a JSON string")
        if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
            raise ValueError(f"{key} contains unsafe control characters")
        if key not in {
            "MEME_POST_TEXT",
            "engagement_question_notification_output_path",
        }:
            if not value:
                raise ValueError(f"{key} must not be empty")
            if value != value.strip():
                raise ValueError(f"{key} must not have leading or trailing whitespace")
        return value

    return value


def _local_config_stat_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    """Return the file identity which must remain stable for one config read."""

    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_uid,
        file_stat.st_gid,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _read_stable_local_config_bytes(
    *,
    LOCAL_CONFIG_FILE: Any,
    LOCAL_CONFIG_MAX_BYTES: Any,
    LocalConfigError: Any,
    _local_config_stat_identity: Any,
    os: Any,
    stat: Any,
) -> bytes | None:
    """Read one optional regular local-config file without following links."""

    config_path = os.path.abspath(os.fspath(LOCAL_CONFIG_FILE))
    try:
        before_path = os.lstat(config_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalConfigError(
            f"Failed to inspect local config file {LOCAL_CONFIG_FILE}: {exc}"
        ) from exc
    if not stat.S_ISREG(before_path.st_mode):
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} must be a regular file"
        )

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if not nofollow or not nonblock:
        raise LocalConfigError(
            "Local config requires O_NOFOLLOW and O_NONBLOCK support"
        )
    try:
        descriptor = os.open(
            config_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | nonblock,
        )
    except OSError as exc:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} changed before it was opened: {exc}"
        ) from exc
    try:
        try:
            before_fd = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before_fd.st_mode)
                or _local_config_stat_identity(before_path)
                != _local_config_stat_identity(before_fd)
            ):
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} changed while it was opened"
                )
            if before_fd.st_size > LOCAL_CONFIG_MAX_BYTES:
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} exceeds "
                    f"{LOCAL_CONFIG_MAX_BYTES} bytes"
                )

            chunks: list[bytes] = []
            observed = 0
            while observed <= LOCAL_CONFIG_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(8192, LOCAL_CONFIG_MAX_BYTES + 1 - observed),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
            document = b"".join(chunks)
            middle_fd = os.fstat(descriptor)
            repeated_document = os.pread(descriptor, before_fd.st_size + 1, 0)
            after_fd = os.fstat(descriptor)
            try:
                after_path = os.lstat(config_path)
            except OSError as exc:
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} disappeared while it was read"
                ) from exc
        finally:
            os.close(descriptor)
    except LocalConfigError:
        raise
    except OSError as exc:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} could not be read as a stable snapshot: {exc}"
        ) from exc

    if len(document) != before_fd.st_size or len(document) > LOCAL_CONFIG_MAX_BYTES:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} length changed while it was read"
        )
    if document != repeated_document:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} bytes changed while it was read"
        )
    expected_identity = _local_config_stat_identity(before_fd)
    if any(
        _local_config_stat_identity(observed_stat) != expected_identity
        for observed_stat in (middle_fd, after_fd, after_path)
    ):
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} identity changed while it was read"
        )
    return document


def load_validated_local_config_overrides(
    *,
    LOCAL_CONFIG_FILE: Any,
    LocalConfigError: Any,
    SOURCE_DEFAULT_CONFIG_VALUES: Any,
    _coerce_local_config_value: Any,
    _read_stable_local_config_bytes: Any,
    copy: Any,
    load_strict_runtime_json: Any,
    log: Any,
    validate_runtime_config_values: Any,
) -> dict[str, object] | None:
    """Read and validate local overrides without mutating runtime globals."""

    document = _read_stable_local_config_bytes()
    if document is None:
        return None
    try:
        data = load_strict_runtime_json(document, label="local config")
    except Exception as exc:
        raise LocalConfigError(f"Failed to read local config file {LOCAL_CONFIG_FILE}: {exc}") from exc

    if not isinstance(data, dict):
        raise LocalConfigError(f"Local config file {LOCAL_CONFIG_FILE} must contain a JSON object")

    if "reply_strategy" in data:
        raise LocalConfigError(
            "Local config contains retired reply_strategy V1 settings; replace them with "
            "the single_call_reply configuration before activation"
        )

    # This was the global conversational-provider breaker setting immediately
    # before the single-Sol cut-over. Accept it only as an unambiguous upgrade
    # alias; runtime configuration and state use the accurately named OpenAI
    # setting exclusively.
    legacy_provider_limit = "MAX_XAI_ERRORS_PER_WINDOW"
    current_provider_limit = "MAX_OPENAI_ERRORS_PER_WINDOW"
    if legacy_provider_limit in data:
        if current_provider_limit in data:
            raise LocalConfigError(
                "Local config contains both the retired xAI and current "
                "OpenAI provider error limits"
            )
        data = dict(data)
        data[current_provider_limit] = data.pop(legacy_provider_limit)
        log.warning(
            "Migrating retired local config key %s to %s",
            legacy_provider_limit,
            current_provider_limit,
        )

    proposed: dict[str, object] = {}
    coercion_errors: list[str] = []

    for key, value in data.items():
        if key not in SOURCE_DEFAULT_CONFIG_VALUES:
            raise LocalConfigError(
                f"Unsupported local config key {key!r} in {LOCAL_CONFIG_FILE}; "
                "refusing to ignore a possible safety-setting typo"
            )

        try:
            coerced = _coerce_local_config_value(
                key,
                value,
                SOURCE_DEFAULT_CONFIG_VALUES[key],
            )
        except Exception as exc:
            log.error("Rejecting local config due to invalid override %s=%r: %s", key, value, exc)
            coercion_errors.append(f"{key}: {exc}")
            continue

        proposed[key] = coerced

    if coercion_errors:
        raise LocalConfigError(
            f"Invalid local config {LOCAL_CONFIG_FILE}: " + "; ".join(coercion_errors)
        )

    if proposed:
        candidate = copy.deepcopy(SOURCE_DEFAULT_CONFIG_VALUES)
        candidate.update(proposed)
        validation_errors = validate_runtime_config_values(candidate)
        if validation_errors:
            raise LocalConfigError(
                f"Invalid local config {LOCAL_CONFIG_FILE}: " + "; ".join(validation_errors)
            )

    return proposed
