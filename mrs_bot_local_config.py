"""Strict runtime JSON and local-configuration loading/coercion.

LocalConfiguration owns stable reading and the complete override-validation
transaction, using fixed local JSON, identity and coercion helpers. Each root
call binds current paths, limits, errors, defaults and runtime validation without
reading a file. Applying overrides remains outside this owner. Import performs
no runtime work and instances retain no caller documents or descriptors.
"""
from __future__ import annotations

import copy
import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from logging import Logger
from pathlib import Path
from types import ModuleType


def load_strict_runtime_json(
    handle_or_document,
    *,
    label: str,
    parse_floats_as_decimal: bool = False,
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
) -> object:
    """Coerce JSON types; numeric bounds are checked on the complete config."""
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
        return value

    if isinstance(current_value, float):
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number, not a boolean")
        return float(value)

    if isinstance(current_value, str):
        if type(value) is not str:
            raise ValueError(f"{key} must be a JSON string")
        if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
            raise ValueError(f"{key} contains unsafe control characters")
        if key not in {
            "MEME_POST_TEXT",
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


@dataclass(frozen=True)
class LocalConfiguration:
    """Own stable configuration reading and atomic override validation."""

    config_file: Path
    maximum_bytes: int
    error_type: type[Exception]
    os: ModuleType
    stat: ModuleType
    source_defaults: dict[str, object]
    log: Logger
    validate_runtime_values: Callable[[dict[str, object]], list[str]]

    def read_snapshot(self) -> bytes | None:
        """Read one optional regular local-config file without following links."""

        config_path = self.os.path.abspath(self.os.fspath(self.config_file))
        try:
            before_path = self.os.lstat(config_path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self.error_type(
                f"Failed to inspect local config file {self.config_file}: {exc}"
            ) from exc
        if not self.stat.S_ISREG(before_path.st_mode):
            raise self.error_type(
                f"Local config file {self.config_file} must be a regular file"
            )

        nofollow = getattr(self.os, "O_NOFOLLOW", 0)
        nonblock = getattr(self.os, "O_NONBLOCK", 0)
        if not nofollow or not nonblock:
            raise self.error_type(
                "Local config requires O_NOFOLLOW and O_NONBLOCK support"
            )
        try:
            descriptor = self.os.open(
                config_path,
                self.os.O_RDONLY | getattr(self.os, "O_CLOEXEC", 0) | nofollow | nonblock,
            )
        except OSError as exc:
            raise self.error_type(
                f"Local config file {self.config_file} changed before it was opened: {exc}"
            ) from exc
        try:
            try:
                before_fd = self.os.fstat(descriptor)
                if (
                    not self.stat.S_ISREG(before_fd.st_mode)
                    or _local_config_stat_identity(before_path)
                    != _local_config_stat_identity(before_fd)
                ):
                    raise self.error_type(
                        f"Local config file {self.config_file} changed while it was opened"
                    )
                if before_fd.st_size > self.maximum_bytes:
                    raise self.error_type(
                        f"Local config file {self.config_file} exceeds "
                        f"{self.maximum_bytes} bytes"
                    )

                chunks: list[bytes] = []
                observed = 0
                while observed <= self.maximum_bytes:
                    chunk = self.os.read(
                        descriptor,
                        min(8192, self.maximum_bytes + 1 - observed),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    observed += len(chunk)
                document = b"".join(chunks)
                middle_fd = self.os.fstat(descriptor)
                repeated_document = self.os.pread(descriptor, before_fd.st_size + 1, 0)
                after_fd = self.os.fstat(descriptor)
                try:
                    after_path = self.os.lstat(config_path)
                except OSError as exc:
                    raise self.error_type(
                        f"Local config file {self.config_file} disappeared while it was read"
                    ) from exc
            finally:
                self.os.close(descriptor)
        except self.error_type:
            raise
        except OSError as exc:
            raise self.error_type(
                f"Local config file {self.config_file} could not be read as a stable snapshot: {exc}"
            ) from exc

        if len(document) != before_fd.st_size or len(document) > self.maximum_bytes:
            raise self.error_type(
                f"Local config file {self.config_file} length changed while it was read"
            )
        if document != repeated_document:
            raise self.error_type(
                f"Local config file {self.config_file} bytes changed while it was read"
            )
        expected_identity = _local_config_stat_identity(before_fd)
        if any(
            _local_config_stat_identity(observed_stat) != expected_identity
            for observed_stat in (middle_fd, after_fd, after_path)
        ):
            raise self.error_type(
                f"Local config file {self.config_file} identity changed while it was read"
            )
        return document

    def load_overrides(self) -> dict[str, object] | None:
        """Read and validate local overrides without mutating runtime globals."""

        document = self.read_snapshot()
        if document is None:
            return None
        try:
            data = load_strict_runtime_json(document, label="local config")
        except Exception as exc:
            raise self.error_type(f"Failed to read local config file {self.config_file}: {exc}") from exc

        if not isinstance(data, dict):
            raise self.error_type(f"Local config file {self.config_file} must contain a JSON object")

        if "reply_strategy" in data:
            raise self.error_type(
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
                raise self.error_type(
                    "Local config contains both the retired xAI and current "
                    "OpenAI provider error limits"
                )
            data = dict(data)
            data[current_provider_limit] = data.pop(legacy_provider_limit)
            self.log.warning(
                "Migrating retired local config key %s to %s",
                legacy_provider_limit,
                current_provider_limit,
            )

        proposed: dict[str, object] = {}
        coercion_errors: list[str] = []
        # Existing installations may retain these settings after runtime retirement.
        # Their values cannot enable the removed feature; unknown keys still fail.
        retired_generated_keys = {
            "ENABLE_GENERATED_IMAGE_POOL",
            "GENERATED_IMAGE_DIR",
            "GENERATED_IMAGE_GLOB",
            "GENERATED_IMAGE_ANALYSIS_FILE",
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
            "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING",
            "ENABLE_GENERATED_IDENTITY_POLICY_SCORING",
            "GENERATED_IDENTITY_AUDIT_FILE",
            "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
            "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
        }

        for key, value in data.items():
            if key in retired_generated_keys:
                self.log.info("Ignoring retired generated-image runtime setting %s", key)
                continue
            if key not in self.source_defaults:
                raise self.error_type(
                    f"Unsupported local config key {key!r} in {self.config_file}; "
                    "refusing to ignore a possible safety-setting typo"
                )

            try:
                coerced = _coerce_local_config_value(
                    key,
                    value,
                    self.source_defaults[key],
                )
            except Exception as exc:
                self.log.error("Rejecting local config due to invalid override %s=%r: %s", key, value, exc)
                coercion_errors.append(f"{key}: {exc}")
                continue

            proposed[key] = coerced

        if coercion_errors:
            raise self.error_type(
                f"Invalid local config {self.config_file}: " + "; ".join(coercion_errors)
            )

        if proposed:
            candidate = copy.deepcopy(self.source_defaults)
            candidate.update(proposed)
            validation_errors = self.validate_runtime_values(candidate)
            if validation_errors:
                raise self.error_type(
                    f"Invalid local config {self.config_file}: " + "; ".join(validation_errors)
                )

        return proposed
