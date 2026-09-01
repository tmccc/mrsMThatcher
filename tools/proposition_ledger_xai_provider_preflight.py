#!/usr/bin/env python3
"""Local-only xAI structured-output preflight for the proposition ledger.

The module keeps provider imports behind an active network-denial context.  It
never samples, streams, lists models, or invokes an HTTP/gRPC transport.  The
canonical schema remains the persistence authority; the provider form is a
pure copy with only proved JSON Schema default expansion and restricted
whole-string regex normalisation.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import http.client
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import struct
import sys
import sysconfig
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_CANONICAL_SHA256 = (
    "eea15c28f5019cea405bfdd65b924ee18c76dc428f16a1fe502592b5cb953d8a"
)
EXPECTED_CANONICAL_VERSION = "proposition-ledger-semantic-delta-v1.1.0"
EXPECTED_XAI_SDK_VERSION = "1.19.0"
EXPECTED_XAI_WHEEL_SHA256 = (
    "4af1a629ad9304d0b05fa052d84ce77b7b61242a6d59d5936b692fec95fb52c1"
)
EXPECTED_RULES_SEMANTIC_SHA256 = (
    "c8e8b6aceabaaf8aa7e6def0903e4332c956c2bfe8b6cd01ae9c719ae3b641c8"
)
EXPECTED_LOCK_SEMANTIC_SHA256 = (
    "950f3ed6b923096a6ace48b9d1fc449828143bbfc6fb0f86a607fa60fa628f2b"
)

STATUS_EXACT = "locally_compatible_exact"
STATUS_POSTVALIDATION = (
    "locally_compatible_with_mandatory_canonical_postvalidation"
)
STATUS_INCOMPATIBLE = "incompatible_with_documented_xai_schema_subset"
STATUS_SDK_UNAVAILABLE = "sdk_local_compilation_unavailable"
STATUS_DEPENDENCY_FAILURE = "dependency_environment_not_reproducible"
STATUS_LOCAL_PROFILE_INCOMPATIBLE = "local_profile_construction_incompatible"

SUPERSEDED_COMPATIBILITY_RECORD_COMMIT = (
    "777b40f67793d6140fa3f923186cf737c89042ad"
)
PRIOR_DISPOSITION = "incompatible_with_documented_xai_schema_subset"
CORRECTION_REASON = (
    "python_jsonschema_regex_engine_divergence_was_misclassified"
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
CANONICAL_SCHEMA_PATH = (
    PROJECT_DIR
    / "proposition_ledger_research/schema/"
    "proposition-ledger-semantic-delta-v1.schema.json"
)
PHASE13_DIR = PROJECT_DIR / "proposition_ledger_research/phase1_3"
PROFILES_PATH = PHASE13_DIR / "xai-provider-profiles.json"
RULES_PATH = PHASE13_DIR / "xai-structured-output-rules.json"
LOCK_PATH = PHASE13_DIR / "xai-sdk-environment-lock.json"

PROVIDER_KEY_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "BRAVE_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "COHERE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_CUSTOM_SEARCH_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_ADMIN_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "XAI_MANAGEMENT_KEY",
    "X_ACCESS_SECRET",
    "X_ACCESS_TOKEN",
    "X_BEARER_TOKEN",
    "X_CONSUMER_KEY",
    "X_CONSUMER_SECRET",
)

SCHEMA_SINGLE_CHILD_KEYS = (
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
)
SCHEMA_MAPPING_CHILD_KEYS = (
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
)
SCHEMA_SEQUENCE_CHILD_KEYS = ("allOf", "anyOf", "oneOf", "prefixItems")
ANNOTATION_KEYS = {
    "$comment",
    "default",
    "deprecated",
    "description",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}
NUMERIC_CONSTRAINTS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
)
STRING_CONSTRAINTS = ("minLength", "maxLength", "pattern", "format")
ARRAY_CONSTRAINTS = (
    "minItems",
    "maxItems",
    "uniqueItems",
    "minContains",
    "maxContains",
)
PROPERTY_CONSTRAINTS = (
    "minProperties",
    "maxProperties",
    "required",
    "additionalProperties",
)
ENFORCED_FORMATS = {
    "date",
    "time",
    "date-time",
    "email",
    "uuid",
    "ipv4",
    "ipv6",
    "uri",
}

PRIVATE_ARTIFACT_NAMES = (
    "run-manifest.json",
    "provider-profile-manifest.json",
    "official-source-provenance.json",
    "xai-structured-output-rules.json",
    "sdk-environment-lock.json",
    "canonical-schema-audit.json",
    "provider-schema.json",
    "provider-schema-transformation-ledger.json",
    "provider-schema-keyword-audit.json",
    "oneof-disjointness-proofs.json",
    "reference-graph-audit.json",
    "semantic-equivalence-audit.json",
    "sdk-compilation-grok-4.3-low.json",
    "sdk-compilation-grok-4.6-low.json",
    "network-denial-audit.json",
    "compatibility-record.json",
    "determinism-audit.json",
    "validation.json",
)
SUBSTANTIVE_ARTIFACT_NAMES = tuple(
    name
    for name in PRIVATE_ARTIFACT_NAMES
    if name not in {"run-manifest.json", "determinism-audit.json"}
)


class PreflightError(RuntimeError):
    """Raised when a local preflight invariant fails."""


class NetworkDenied(PreflightError):
    """Raised for every guarded connection attempt."""


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PreflightError(f"duplicate JSON member: {key!r}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise PreflightError(f"non-finite JSON number: {value}")


def _parse_strict_json_bytes(source: bytes, label: str | Path) -> Any:
    """Parse strict UTF-8 JSON already obtained from a trusted file read."""

    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreflightError(f"non-UTF-8 JSON: {label}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as exc:
        raise PreflightError(f"invalid JSON in {label}: {exc}") from exc


def load_json(path: str | Path) -> Any:
    """Load strict UTF-8 JSON with duplicate/non-finite rejection."""

    return _parse_strict_json_bytes(Path(path).read_bytes(), path)


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic JSON bytes used for substantive digests."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    """Return stable indented UTF-8 JSON bytes with a trailing newline."""

    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for bytes."""

    return hashlib.sha256(value).hexdigest()


def value_sha256(value: Any) -> str:
    """Hash a JSON-compatible value using the canonical local encoding."""

    return sha256_bytes(canonical_json_bytes(value))


def _lexical_absolute(path: str | Path) -> Path:
    """Return an absolute path without resolving away symlink evidence."""

    return Path(os.path.abspath(os.fspath(path)))


def _require_safe_directory(path: str | Path, label: str) -> Path:
    """Require a real directory reached without any symlink component."""

    candidate = _lexical_absolute(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise PreflightError(f"{label} is missing: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PreflightError(f"{label} is not a real non-symlink directory")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PreflightError(f"{label} cannot be resolved safely") from exc
    if resolved != candidate:
        raise PreflightError(f"{label} contains a symlink path component")
    return candidate


def _prepare_safe_output_directory(path: str | Path) -> Path:
    """Create an output directory only beneath a real non-symlink ancestor."""

    candidate = _lexical_absolute(path)
    if candidate.is_symlink():
        raise PreflightError("output directory root is a symlink")
    if not candidate.exists():
        ancestor = candidate.parent
        while not ancestor.exists() and not ancestor.is_symlink():
            if ancestor == ancestor.parent:
                break
            ancestor = ancestor.parent
        _require_safe_directory(ancestor, "output directory ancestor")
        candidate.mkdir(mode=0o700, parents=True, exist_ok=False)
    safe = _require_safe_directory(candidate, "output directory root")
    safe.chmod(0o700)
    return safe


def _read_regular_nonsymlink(path: str | Path, label: str) -> bytes:
    """Read a regular file without following a leaf or directory symlink."""

    candidate = _lexical_absolute(path)
    _require_safe_directory(candidate.parent, f"{label} parent")
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise PreflightError(f"{label} is missing") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PreflightError(f"{label} is not a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise PreflightError(f"{label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise PreflightError(f"{label} changed during safe open")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _load_regular_json(path: str | Path, label: str) -> Any:
    """Load strict JSON only after a regular non-symlink file check."""

    return _parse_strict_json_bytes(_read_regular_nonsymlink(path, label), label)


def _write_private_bytes(path: str | Path, content: bytes, label: str) -> None:
    """Write mode-0600 bytes without following an existing symlink target."""

    candidate = _lexical_absolute(path)
    _require_safe_directory(candidate.parent, f"{label} parent")
    if os.path.lexists(candidate):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PreflightError(f"{label} target is not a regular non-symlink file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags, 0o600)
    except OSError as exc:
        raise PreflightError(f"{label} target could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PreflightError(f"{label} target is not a regular file")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unescape_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def pointer_join(pointer: str, token: str | int) -> str:
    """Append one escaped token to a JSON Pointer."""

    return f"{pointer}/{_escape_pointer(str(token))}"


def resolve_pointer(document: Any, reference: str) -> Any:
    """Resolve a local JSON Pointer reference or raise a preflight error."""

    if reference == "#":
        return document
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise PreflightError(f"non-local or malformed reference: {reference!r}")
    value = document
    for token in reference[2:].split("/"):
        token = _unescape_pointer(token)
        if isinstance(value, Mapping):
            if token not in value:
                raise PreflightError(f"unresolved reference: {reference}")
            value = value[token]
        elif isinstance(value, list) and token.isdigit():
            index = int(token)
            if index >= len(value):
                raise PreflightError(f"unresolved reference: {reference}")
            value = value[index]
        else:
            raise PreflightError(f"unresolved reference: {reference}")
    return value


def iter_document_nodes(value: Any, pointer: str = "") -> Iterable[tuple[str, Any]]:
    """Walk every JSON value in object-key-sorted order."""

    yield pointer, value
    if isinstance(value, Mapping):
        for key in sorted(value):
            yield from iter_document_nodes(value[key], pointer_join(pointer, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_document_nodes(item, pointer_join(pointer, index))


def iter_schema_nodes(
    schema: Any,
    pointer: str = "",
    depth: int = 0,
) -> Iterable[tuple[str, Any, int]]:
    """Walk object/Boolean schema positions, excluding ordinary instance values."""

    if not isinstance(schema, (Mapping, bool)):
        return
    yield pointer, schema, depth
    if not isinstance(schema, Mapping):
        return
    for key in SCHEMA_SINGLE_CHILD_KEYS:
        child = schema.get(key)
        if isinstance(child, (Mapping, bool)):
            yield from iter_schema_nodes(child, pointer_join(pointer, key), depth + 1)
    for key in SCHEMA_MAPPING_CHILD_KEYS:
        children = schema.get(key)
        if isinstance(children, Mapping):
            for name in sorted(children):
                child = children[name]
                if isinstance(child, (Mapping, bool)):
                    yield from iter_schema_nodes(
                        child,
                        pointer_join(pointer_join(pointer, key), name),
                        depth + 1,
                    )
    for key in SCHEMA_SEQUENCE_CHILD_KEYS:
        children = schema.get(key)
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, (Mapping, bool)):
                    yield from iter_schema_nodes(
                        child,
                        pointer_join(pointer_join(pointer, key), index),
                        depth + 1,
                    )


def _type_set(schema: Mapping[str, Any]) -> set[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
        return set(declared)
    if "properties" in schema:
        return {"object"}
    if "items" in schema or "prefixItems" in schema:
        return {"array"}
    if "const" in schema:
        value = schema["const"]
        if value is None:
            return {"null"}
        if isinstance(value, bool):
            return {"boolean"}
        if isinstance(value, int):
            return {"integer", "number"}
        if isinstance(value, float):
            return {"number"}
        if isinstance(value, str):
            return {"string"}
        if isinstance(value, list):
            return {"array"}
        if isinstance(value, Mapping):
            return {"object"}
    return set()


def is_object_schema(schema: Any) -> bool:
    """Return whether a schema node applies object-specific keywords."""

    return isinstance(schema, Mapping) and (
        "object" in _type_set(schema) or "properties" in schema
    )


def is_array_schema(schema: Any) -> bool:
    """Return whether a schema node applies array-specific keywords."""

    return isinstance(schema, Mapping) and (
        "array" in _type_set(schema) or "items" in schema or "prefixItems" in schema
    )


def _is_null_schema(schema: Any) -> bool:
    return isinstance(schema, Mapping) and (
        _type_set(schema) == {"null"} or schema.get("const", object()) is None
    )


def _is_nullable(schema: Mapping[str, Any]) -> bool:
    declared = schema.get("type")
    if isinstance(declared, list) and "null" in declared and len(set(declared)) > 1:
        return True
    for keyword in ("oneOf", "anyOf"):
        alternatives = schema.get(keyword)
        if isinstance(alternatives, list) and len(alternatives) > 1:
            if any(_is_null_schema(item) for item in alternatives) and any(
                not _is_null_schema(item) for item in alternatives
            ):
                return True
    return False


def _schema_children(schema: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for _, child, _ in iter_schema_nodes(schema):
        if child is schema:
            continue
        if isinstance(child, Mapping):
            yield child


def _effective_depth_metrics(schema: Mapping[str, Any]) -> tuple[int, int]:
    """Compute object and array nesting independently while following local refs."""

    def visit(
        current: Mapping[str, Any],
        object_depth: int,
        array_depth: int,
        active: frozenset[int],
    ) -> tuple[int, int]:
        identity = id(current)
        if identity in active:
            return object_depth, array_depth
        active = active | {identity}
        object_depth += int(is_object_schema(current))
        array_depth += int(is_array_schema(current))
        max_object = object_depth
        max_array = array_depth
        reference = current.get("$ref")
        if isinstance(reference, str) and reference.startswith("#"):
            target = resolve_pointer(schema, reference)
            if isinstance(target, Mapping):
                child_depth = visit(target, object_depth, array_depth, active)
                max_object = max(max_object, child_depth[0])
                max_array = max(max_array, child_depth[1])
        for child in _direct_schema_children(current):
            child_depth = visit(child, object_depth, array_depth, active)
            max_object = max(max_object, child_depth[0])
            max_array = max(max_array, child_depth[1])
        return max_object, max_array

    return visit(schema, 0, 0, frozenset())


def _direct_schema_children(schema: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for key in SCHEMA_SINGLE_CHILD_KEYS:
        child = schema.get(key)
        if isinstance(child, Mapping):
            yield child
    for key in SCHEMA_MAPPING_CHILD_KEYS:
        children = schema.get(key)
        if isinstance(children, Mapping):
            for child in children.values():
                if isinstance(child, Mapping):
                    yield child
    for key in SCHEMA_SEQUENCE_CHILD_KEYS:
        children = schema.get(key)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, Mapping):
                    yield child


def _regex_unsupported_features(pattern: str) -> list[str]:
    checks = (
        (r"\\(?:[1-9]|k<)", "backreference"),
        (r"\\[pP]\{", "unicode_property_escape"),
        (r"\\[bB]", "word_boundary"),
        (r"\(\?<?[=!].*", "lookaround"),
        (r"\(\?[imsx-]+[:)]", "inline_modifier"),
        (r"\(\?\(", "conditional_expression"),
    )
    return [name for expression, name in checks if re.search(expression, pattern)]


def _has_explicit_outer_anchors(pattern: str) -> bool:
    """Return whether a pattern uses the canonical leading/trailing anchors."""

    return pattern.startswith("^") and pattern.endswith("$") and not pattern.endswith(
        r"\$"
    )


class _RestrictedPatternSyntaxError(ValueError):
    """A stable failure from the deliberately narrow current-pattern parser."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _RestrictedPatternParser:
    """Parse only the simple ASCII grammar used by the 14 canonical patterns."""

    _LITERALS = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-:"
    )
    _CLASS_LITERALS = _LITERALS | frozenset(".")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.position = 0
        self.atom_count = 0
        self.group_count = 0
        self.character_class_count = 0
        self.quantifier_count = 0

    def consume(self) -> dict[str, int | str]:
        if not self.pattern:
            raise _RestrictedPatternSyntaxError("empty_interior")
        if not self.pattern.isascii():
            raise _RestrictedPatternSyntaxError("non_ascii_syntax_unproved")
        self._parse_sequence(stop=frozenset(), allow_alternation=False)
        if self.position != len(self.pattern):
            character = self.pattern[self.position]
            if character == "|":
                raise _RestrictedPatternSyntaxError("top_level_alternation")
            if character == ")":
                raise _RestrictedPatternSyntaxError("unmatched_closing_group")
            raise _RestrictedPatternSyntaxError("unparsed_syntax")
        return {
            "grammar": "restricted_ascii_identifier_and_language_tag_v1",
            "atom_count": self.atom_count,
            "group_count": self.group_count,
            "character_class_count": self.character_class_count,
            "quantifier_count": self.quantifier_count,
        }

    def _parse_sequence(
        self,
        *,
        stop: frozenset[str],
        allow_alternation: bool,
    ) -> None:
        sequence_atoms = 0
        while self.position < len(self.pattern):
            character = self.pattern[self.position]
            if character in stop or character == "|":
                break
            self._parse_atom()
            sequence_atoms += 1
        if sequence_atoms == 0:
            raise _RestrictedPatternSyntaxError("empty_sequence_or_alternative")
        if self.position < len(self.pattern) and self.pattern[self.position] == "|":
            if not allow_alternation:
                raise _RestrictedPatternSyntaxError("top_level_alternation")
            while self.position < len(self.pattern) and self.pattern[self.position] == "|":
                self.position += 1
                self._parse_sequence(stop=stop, allow_alternation=False)

    def _parse_atom(self) -> None:
        character = self.pattern[self.position]
        if character == "[":
            self._parse_character_class()
        elif character == "(":
            self._parse_group()
        elif character == "\\":
            raise _RestrictedPatternSyntaxError("escape_or_shorthand_unproved")
        elif character == ".":
            raise _RestrictedPatternSyntaxError("dot_wildcard_unproved")
        elif character in "^$":
            raise _RestrictedPatternSyntaxError("interior_anchor")
        elif character in "*+?{}]":
            raise _RestrictedPatternSyntaxError("malformed_or_unproved_quantifier")
        elif character not in self._LITERALS:
            raise _RestrictedPatternSyntaxError("literal_outside_proved_ascii_set")
        else:
            self.position += 1
        self.atom_count += 1
        self._parse_optional_quantifier()

    def _parse_group(self) -> None:
        self.position += 1
        if self.position >= len(self.pattern):
            raise _RestrictedPatternSyntaxError("unclosed_group")
        if self.pattern[self.position] == "?":
            raise _RestrictedPatternSyntaxError("unsupported_group_extension")
        self._parse_sequence(stop=frozenset(")"), allow_alternation=True)
        if self.position >= len(self.pattern) or self.pattern[self.position] != ")":
            raise _RestrictedPatternSyntaxError("unclosed_group")
        self.position += 1
        self.group_count += 1

    def _parse_character_class(self) -> None:
        self.position += 1
        start = self.position
        if self.position < len(self.pattern) and self.pattern[self.position] == "^":
            raise _RestrictedPatternSyntaxError("negated_character_class_unproved")
        while self.position < len(self.pattern) and self.pattern[self.position] != "]":
            character = self.pattern[self.position]
            if character == "\\":
                raise _RestrictedPatternSyntaxError("class_escape_unproved")
            if character in "^$[" or character not in self._CLASS_LITERALS:
                raise _RestrictedPatternSyntaxError("character_class_syntax_unproved")
            self.position += 1
        if self.position >= len(self.pattern):
            raise _RestrictedPatternSyntaxError("unclosed_character_class")
        content = self.pattern[start:self.position]
        if not content:
            raise _RestrictedPatternSyntaxError("empty_character_class")
        for index, character in enumerate(content):
            if character != "-" or index in {0, len(content) - 1}:
                continue
            lower = content[index - 1]
            upper = content[index + 1]
            if not (lower.isalnum() and upper.isalnum() and ord(lower) <= ord(upper)):
                raise _RestrictedPatternSyntaxError("character_class_range_unproved")
        self.position += 1
        self.character_class_count += 1

    def _parse_optional_quantifier(self) -> None:
        if self.position >= len(self.pattern):
            return
        character = self.pattern[self.position]
        if character == "*":
            self.position += 1
            self.quantifier_count += 1
        elif character == "{":
            closing = self.pattern.find("}", self.position + 1)
            if closing < 0:
                raise _RestrictedPatternSyntaxError("unclosed_bounded_quantifier")
            body = self.pattern[self.position + 1:closing]
            parts = body.split(",")
            if len(parts) not in {1, 2} or any(not part.isdigit() for part in parts):
                raise _RestrictedPatternSyntaxError("bounded_quantifier_syntax_unproved")
            bounds = [int(part) for part in parts]
            if len(bounds) == 2 and bounds[0] > bounds[1]:
                raise _RestrictedPatternSyntaxError("bounded_quantifier_range_invalid")
            self.position = closing + 1
            self.quantifier_count += 1
        if self.position < len(self.pattern) and self.pattern[self.position] in "*+?{":
            raise _RestrictedPatternSyntaxError("repeated_or_unproved_quantifier")


def _prove_restricted_xai_provider_pattern(pattern: str) -> dict[str, Any]:
    """Prove that one unanchored pattern is in the exact current ASCII subset."""

    try:
        grammar = _RestrictedPatternParser(pattern).consume()
    except _RestrictedPatternSyntaxError as exc:
        return {
            "proved": False,
            "failure_reason": exc.code,
            "provider_pattern": pattern,
        }
    return {
        "proved": True,
        "failure_reason": None,
        "provider_pattern": pattern,
        "ascii_only": True,
        "escape_free": True,
        "dot_wildcard_absent": True,
        "interior_anchor_absent": True,
        "inline_modifier_absent": True,
        "top_level_alternation_absent": True,
        **grammar,
    }


def _prove_restricted_xai_outer_anchor_removal(pattern: str) -> dict[str, Any]:
    """Prove exact outer-anchor removal for the current simple pattern subset."""

    if not pattern.startswith("^"):
        return {
            "proved": False,
            "failure_reason": "missing_leading_outer_anchor",
            "canonical_pattern": pattern,
        }
    if not pattern.endswith("$"):
        return {
            "proved": False,
            "failure_reason": "missing_trailing_outer_anchor",
            "canonical_pattern": pattern,
        }
    preceding_backslashes = 0
    for character in reversed(pattern[:-1]):
        if character != "\\":
            break
        preceding_backslashes += 1
    if preceding_backslashes % 2:
        return {
            "proved": False,
            "failure_reason": "escaped_final_dollar_not_outer_anchor",
            "canonical_pattern": pattern,
        }
    interior = pattern[1:-1]
    provider_proof = _prove_restricted_xai_provider_pattern(interior)
    if not provider_proof["proved"]:
        return {
            "proved": False,
            "failure_reason": provider_proof["failure_reason"],
            "canonical_pattern": pattern,
            "provider_pattern": interior,
        }
    return {
        "proved": True,
        "failure_reason": None,
        "canonical_pattern": pattern,
        "provider_pattern": interior,
        "first_token_is_unescaped_caret": True,
        "final_token_is_unescaped_dollar": True,
        "interior_nonempty": True,
        "canonical_multiline_mode": False,
        "provider_implicit_whole_string_match": True,
        **{
            key: value
            for key, value in provider_proof.items()
            if key not in {"proved", "failure_reason", "provider_pattern"}
        },
    }


def strip_redundant_xai_outer_anchors(pattern: str) -> str:
    """Return xAI's implicit-whole-string form, failing closed if unproved."""

    proof = _prove_restricted_xai_outer_anchor_removal(pattern)
    if not proof["proved"]:
        raise PreflightError(
            "unresolved provider pattern incompatibility: "
            f"{proof['failure_reason']}: {pattern!r}"
        )
    return str(proof["provider_pattern"])


def _restricted_full_string_match(pattern: str, instance: str) -> bool:
    """Match one proved provider pattern; this is not a general ECMA interpreter."""

    proof = _prove_restricted_xai_provider_pattern(pattern)
    if not proof["proved"]:
        raise PreflightError(
            f"restricted pattern match requested for unproved syntax: {proof}"
        )
    return re.fullmatch(pattern, instance, flags=re.ASCII) is not None


def audit_reference_graph(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Build an independently checked local $defs reference graph."""

    definitions = schema.get("$defs", {})
    owners: dict[str, Any] = {"#": schema}
    if isinstance(definitions, Mapping):
        owners.update(
            {
                f"#/$defs/{_escape_pointer(str(name))}": definition
                for name, definition in definitions.items()
            }
        )
    graph: dict[str, set[str]] = {owner: set() for owner in owners}
    occurrences: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    def owner_walk(owner: str, value: Any, pointer: str) -> None:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if reference is not None:
                entry = {"pointer": pointer_join(pointer, "$ref"), "reference": reference}
                if not isinstance(reference, str) or not reference.startswith("#"):
                    entry["status"] = "external_or_malformed"
                    unresolved.append(entry)
                else:
                    try:
                        resolve_pointer(schema, reference)
                    except PreflightError:
                        entry["status"] = "unresolved"
                        unresolved.append(entry)
                    else:
                        target_owner = "#"
                        if reference.startswith("#/$defs/"):
                            parts = reference.split("/", 3)
                            target_owner = "/".join(parts[:3])
                        graph.setdefault(owner, set()).add(target_owner)
                        entry.update({"status": "resolved_local", "target_owner": target_owner})
                occurrences.append(entry)
            for key in sorted(value):
                if owner == "#" and pointer == "" and key == "$defs":
                    continue
                owner_walk(owner, value[key], pointer_join(pointer, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                owner_walk(owner, item, pointer_join(pointer, index))

    owner_walk("#", schema, "")
    for owner, definition in sorted(owners.items()):
        if owner != "#":
            owner_walk(owner, definition, owner[1:])

    cycles: set[tuple[str, ...]] = set()

    def visit(node: str, stack: tuple[str, ...]) -> None:
        if node in stack:
            start = stack.index(node)
            cycle = stack[start:] + (node,)
            rotations = [cycle[index:-1] + cycle[:index] for index in range(len(cycle) - 1)]
            canonical = min(rotations)
            cycles.add(canonical + (canonical[0],))
            return
        for target in sorted(graph.get(node, ())):
            visit(target, stack + (node,))

    for vertex in sorted(graph):
        visit(vertex, ())

    longest = 0

    def depth(node: str, active: frozenset[str]) -> int:
        if node in active:
            return 0
        return 1 + max(
            (depth(target, active | {node}) for target in graph.get(node, ())),
            default=0,
        )

    if not cycles:
        longest = max((depth(node, frozenset()) for node in graph), default=0)
    return {
        "reference_count": len(occurrences),
        "unique_reference_target_count": len(
            {entry["reference"] for entry in occurrences if entry["status"] == "resolved_local"}
        ),
        "all_references_local_and_resolved": not unresolved,
        "unresolved_or_external_references": unresolved,
        "occurrences": occurrences,
        "graph_vertex_count": len(graph),
        "graph_edge_count": sum(len(targets) for targets in graph.values()),
        "graph": {key: sorted(value) for key, value in sorted(graph.items())},
        "cycles": [list(cycle) for cycle in sorted(cycles)],
        "circular_reference_count": len(cycles),
        "acyclic": not cycles,
        "longest_owner_chain": longest,
        "longest_owner_chain_vertex_count": longest,
        "longest_owner_chain_edge_count": max(0, longest - 1),
    }


def _resolved_schema(root: Mapping[str, Any], value: Any) -> Any:
    seen: set[str] = set()
    while isinstance(value, Mapping) and isinstance(value.get("$ref"), str):
        reference = value["$ref"]
        if reference in seen:
            raise PreflightError(f"circular reference while resolving {reference}")
        seen.add(reference)
        target = resolve_pointer(root, reference)
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            return {
                "allOf": [copy.deepcopy(target), copy.deepcopy(siblings)]
            }
        value = target
    return value


def _explicit_type_set(schema: Mapping[str, Any]) -> set[str]:
    """Return only type domains that are structurally asserted by a schema."""

    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
        return set(declared)
    if "const" in schema:
        value = schema["const"]
        if value is None:
            return {"null"}
        if isinstance(value, bool):
            return {"boolean"}
        if isinstance(value, (int, float)):
            return {"number"}
        if isinstance(value, str):
            return {"string"}
        if isinstance(value, list):
            return {"array"}
        if isinstance(value, Mapping):
            return {"object"}
    return set()


def _type_domains_disjoint(first: set[str], second: set[str]) -> bool:
    """Check JSON Schema primitive domains, including integer/number overlap."""

    if not first or not second:
        return False
    if not first.isdisjoint(second):
        return False
    numeric = {"integer", "number"}
    return not (first & numeric and second & numeric)


def _const_or_enum_domain(root: Mapping[str, Any], value: Any) -> list[Any] | None:
    value = _resolved_schema(root, value)
    if not isinstance(value, Mapping):
        return None
    if "const" in value:
        return [value["const"]]
    enum = value.get("enum")
    if isinstance(enum, list):
        return list(enum)
    return None


def _domain_disjoint(first: Sequence[Any], second: Sequence[Any]) -> bool:
    def json_equal(left: Any, right: Any) -> bool:
        if (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ):
            return left == right
        return type(left) is type(right) and left == right

    return not any(json_equal(left, right) for left in first for right in second)


def prove_oneof_disjointness(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Prove every oneOf pair via disjoint type or required const/enum domains."""

    proofs: list[dict[str, Any]] = []
    for pointer, node, _ in iter_schema_nodes(schema):
        if not isinstance(node, Mapping) or "oneOf" not in node:
            continue
        alternatives = node["oneOf"]
        occurrence = {
            "pointer": pointer_join(pointer, "oneOf"),
            "alternative_count": len(alternatives) if isinstance(alternatives, list) else 0,
            "pair_proofs": [],
        }
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            occurrence["all_pairs_proved_disjoint"] = False
            occurrence["error"] = "oneOf must contain at least two alternatives"
            proofs.append(occurrence)
            continue
        for first_index in range(len(alternatives)):
            for second_index in range(first_index + 1, len(alternatives)):
                first = _resolved_schema(schema, alternatives[first_index])
                second = _resolved_schema(schema, alternatives[second_index])
                pair: dict[str, Any] = {
                    "alternative_indexes": [first_index, second_index],
                    "proved_disjoint": False,
                }
                if isinstance(first, Mapping) and isinstance(second, Mapping):
                    first_types = _explicit_type_set(first)
                    second_types = _explicit_type_set(second)
                    if _type_domains_disjoint(first_types, second_types):
                        pair.update(
                            {
                                "proved_disjoint": True,
                                "proof_type": "disjoint_json_type_domains",
                                "first_domain": sorted(first_types),
                                "second_domain": sorted(second_types),
                            }
                        )
                    elif first_types == {"object"} and second_types == {"object"}:
                        first_required = set(first.get("required", []))
                        second_required = set(second.get("required", []))
                        first_properties = first.get("properties", {})
                        second_properties = second.get("properties", {})
                        for property_name in sorted(first_required & second_required):
                            if not isinstance(first_properties, Mapping) or not isinstance(
                                second_properties, Mapping
                            ):
                                continue
                            first_domain = _const_or_enum_domain(
                                schema, first_properties.get(property_name)
                            )
                            second_domain = _const_or_enum_domain(
                                schema, second_properties.get(property_name)
                            )
                            if (
                                first_domain is not None
                                and second_domain is not None
                                and _domain_disjoint(first_domain, second_domain)
                            ):
                                pair.update(
                                    {
                                        "proved_disjoint": True,
                                        "proof_type": "required_discriminant_disjoint_const_or_enum",
                                        "discriminant_property": property_name,
                                        "first_domain": first_domain,
                                        "second_domain": second_domain,
                                    }
                                )
                                break
                if not pair["proved_disjoint"]:
                    pair["proof_type"] = "no_structural_disjointness_proof"
                occurrence["pair_proofs"].append(pair)
        occurrence["all_pairs_proved_disjoint"] = all(
            pair["proved_disjoint"] for pair in occurrence["pair_proofs"]
        )
        proofs.append(occurrence)
    return {
        "oneof_occurrence_count": len(proofs),
        "alternative_pair_count": sum(len(item["pair_proofs"]) for item in proofs),
        "proved_pair_count": sum(
            pair["proved_disjoint"]
            for item in proofs
            for pair in item["pair_proofs"]
        ),
        "all_oneof_pairs_structurally_disjoint": all(
            item["all_pairs_proved_disjoint"] for item in proofs
        ),
        "proofs": proofs,
    }


def transform_provider_schema(
    canonical_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the pure, equivalence-proved xAI-facing schema copy."""

    provider = copy.deepcopy(dict(canonical_schema))
    ledger: list[dict[str, Any]] = []
    for pointer, node, _ in iter_schema_nodes(provider):
        if isinstance(node, dict) and is_object_schema(node) and "additionalProperties" not in node:
            node["additionalProperties"] = True
            ledger.append(
                {
                    "canonical_json_pointer": pointer,
                    "transformation_kind": "insert_explicit_additional_properties_true",
                    "canonical_value": {
                        "keyword_present": False,
                        "json_schema_default": True,
                    },
                    "provider_value": True,
                    "reason": "xAI defaults omitted additionalProperties to false",
                    "xai_rule_id": "additional_properties_provider_default_false",
                    "semantic_proof_type": "json_schema_default_expansion",
                    "proof_result": "exactly_equivalent",
                }
            )
        pattern = node.get("pattern") if isinstance(node, dict) else None
        if isinstance(pattern, str):
            proof = _prove_restricted_xai_outer_anchor_removal(pattern)
            if not proof["proved"]:
                raise PreflightError(
                    "unresolved provider pattern incompatibility at "
                    f"{pointer_join(pointer, 'pattern')}: "
                    f"{proof['failure_reason']}"
                )
            provider_pattern = str(proof["provider_pattern"])
            node["pattern"] = provider_pattern
            ledger.append(
                {
                    "canonical_json_pointer": pointer_join(pointer, "pattern"),
                    "canonical_pattern": pattern,
                    "provider_pattern": provider_pattern,
                    "transformation_kind": (
                        "remove_redundant_outer_anchors_for_xai_full_string_pattern"
                    ),
                    "xai_rule_id": "regex_implicit_anchors",
                    "canonical_semantics_source": [
                        "json_schema_draft_2020_12_validation",
                        "ecma_262_text_processing",
                    ],
                    "provider_semantics_source": "xai_structured_outputs",
                    "restricted_subset_proof": proof,
                    "semantic_proof_type": (
                        "outer_anchor_removal_under_provider_implicit_full_string_semantics"
                    ),
                    "proof_result": (
                        "exactly_equivalent_for_proved_restricted_pattern"
                    ),
                }
            )
    ledger.sort(
        key=lambda item: (
            item["canonical_json_pointer"],
            item["transformation_kind"],
        )
    )
    if not ledger:
        ledger.append(
            {
                "canonical_json_pointer": "",
                "transformation_kind": "identity",
                "canonical_value": None,
                "provider_value": None,
                "reason": "no provider-facing change required",
                "xai_rule_id": None,
                "semantic_proof_type": "identity",
                "proof_result": "exactly_equivalent",
            }
        )
    return provider, ledger


def _constraint_guarantee(keyword: str, value: Any) -> str:
    if keyword in {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}:
        return "guaranteed_no_documented_threshold"
    thresholds = {
        "minLength": 2048,
        "maxLength": 2048,
        "minItems": 256,
        "maxItems": 256,
        "minProperties": 64,
        "maxProperties": 64,
    }
    if keyword in thresholds and isinstance(value, (int, float)):
        return (
            "guaranteed_within_documented_threshold"
            if value <= thresholds[keyword]
            else "accepted_best_effort_above_documented_threshold"
        )
    if keyword == "uniqueItems":
        return "provider_guarantee_not_documented_canonical_postvalidation_required"
    return "not_a_thresholded_constraint"


def audit_provider_keywords(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Classify every provider-relevant keyword against the frozen rules."""

    rejected: list[dict[str, Any]] = []
    best_effort: list[dict[str, Any]] = []
    undocumented_guarantees: list[dict[str, Any]] = []
    pattern_audit: list[dict[str, Any]] = []
    regex_semantic_uncertainties: list[dict[str, Any]] = []
    format_audit: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    arrays: list[dict[str, Any]] = []
    prefix_items: list[str] = []
    items_arrays: list[str] = []
    boolean_schemas: list[dict[str, Any]] = []
    best_effort_groups: set[tuple[str, str]] = set()

    for pointer, node, _ in iter_schema_nodes(schema):
        if isinstance(node, bool):
            boolean_schemas.append({"pointer": pointer, "value": node})
            continue
        enum = node.get("enum")
        if isinstance(enum, list) and not enum:
            rejected.append({"pointer": pointer_join(pointer, "enum"), "kind": "empty_enum"})
        alternatives = node.get("anyOf")
        if isinstance(alternatives, list) and not alternatives:
            rejected.append({"pointer": pointer_join(pointer, "anyOf"), "kind": "empty_anyof"})
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            for name, child in sorted(properties.items()):
                if isinstance(child, bool):
                    rejected.append(
                        {
                            "pointer": pointer_join(pointer_join(pointer, "properties"), name),
                            "kind": "boolean_property_schema",
                            "value": child,
                        }
                    )
        for keyword in ("minContains", "maxContains"):
            if keyword in node:
                rejected.append({"pointer": pointer_join(pointer, keyword), "kind": keyword})
        if isinstance(node.get("items"), list):
            items_arrays.append(pointer_join(pointer, "items"))
            rejected.append(
                {"pointer": pointer_join(pointer, "items"), "kind": "items_array_tuple_syntax"}
            )
        if "prefixItems" in node:
            prefix_items.append(pointer_join(pointer, "prefixItems"))
        if is_array_schema(node):
            arrays.append(
                {
                    "pointer": pointer,
                    "items_form": (
                        "array" if isinstance(node.get("items"), list) else
                        "schema" if isinstance(node.get("items"), (Mapping, bool)) else
                        "absent"
                    ),
                    "prefix_items_count": len(node.get("prefixItems", []))
                    if isinstance(node.get("prefixItems"), list)
                    else 0,
                    "bounded": "maxItems" in node,
                }
            )
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            unsupported = _regex_unsupported_features(pattern)
            pattern_pointer = pointer_join(pointer, "pattern")
            explicit_outer_anchors = _has_explicit_outer_anchors(pattern)
            outer_proof = _prove_restricted_xai_outer_anchor_removal(pattern)
            provider_pattern = (
                str(outer_proof["provider_pattern"])
                if outer_proof["proved"]
                else pattern
            )
            provider_pattern_proof = _prove_restricted_xai_provider_pattern(
                provider_pattern
            )
            intended_equivalence = bool(
                outer_proof["proved"] and provider_pattern_proof["proved"]
            )
            pattern_entry = {
                "pointer": pattern_pointer,
                "pattern": pattern,
                "avoids_documented_rejected_features": not unsupported,
                "explicit_outer_anchors": explicit_outer_anchors,
                "canonical_pattern_has_explicit_outer_anchors": (
                    explicit_outer_anchors
                ),
                "provider_outer_anchor_transformation_required": (
                    explicit_outer_anchors
                ),
                "provider_pattern": provider_pattern,
                "provider_pattern_supported_subset": bool(
                    provider_pattern_proof["proved"] and not unsupported
                ),
                "supported_subset": bool(
                    provider_pattern_proof["proved"] and not unsupported
                ),
                "unsupported_features": unsupported,
                "restricted_outer_anchor_proof": outer_proof,
                "python_jsonschema_regex_engine_divergence": bool(
                    outer_proof["proved"]
                ),
                "intended_canonical_xai_semantics_equivalent": (
                    intended_equivalence
                ),
                "exact_provider_semantics_proved": intended_equivalence,
                "provider_anchor_documentation": (
                    "outer_anchors_removed_for_implicit_full_string_match"
                    if explicit_outer_anchors
                    else "not_applicable"
                ),
            }
            pattern_audit.append(pattern_entry)
            if not intended_equivalence:
                regex_semantic_uncertainties.append(
                    {
                        "pointer": pattern_pointer,
                        "kind": "unresolved_provider_pattern_incompatibility",
                        "failure_reason": outer_proof["failure_reason"],
                        "reason": (
                            "the canonical pattern is outside the deliberately narrow "
                            "outer-anchor equivalence proof subset"
                        ),
                    }
                )
            for feature in unsupported:
                rejected.append(
                    {
                        "pointer": pattern_pointer,
                        "kind": f"unsupported_regex_{feature}",
                    }
                )
        value_format = node.get("format")
        if isinstance(value_format, str):
            enforced = value_format in ENFORCED_FORMATS
            format_audit.append(
                {
                    "pointer": pointer_join(pointer, "format"),
                    "format": value_format,
                    "provider_enforced": enforced,
                }
            )
            if not enforced:
                best_effort.append(
                    {
                        "pointer": pointer_join(pointer, "format"),
                        "kind": "unlisted_format",
                    }
                )
                best_effort_groups.add(
                    (pointer_join(pointer, "format"), "unlisted_format")
                )
        for keyword in (*NUMERIC_CONSTRAINTS, *STRING_CONSTRAINTS, *ARRAY_CONSTRAINTS, *PROPERTY_CONSTRAINTS):
            if keyword not in node:
                continue
            guarantee = _constraint_guarantee(keyword, node[keyword])
            entry = {
                "pointer": pointer_join(pointer, keyword),
                "keyword": keyword,
                "value": node[keyword],
                "provider_guarantee": guarantee,
            }
            constraints.append(entry)
            if guarantee == "accepted_best_effort_above_documented_threshold":
                best_effort.append(
                    {"pointer": entry["pointer"], "kind": "over_threshold_constraint"}
                )
                best_effort_groups.add(
                    (entry["pointer"], "over_threshold_constraint")
                )
            elif guarantee.startswith("provider_guarantee_not_documented"):
                undocumented_guarantees.append(
                    {"pointer": entry["pointer"], "kind": keyword}
                )
        for keyword in ("not", "if", "then", "else"):
            if keyword in node:
                best_effort.append(
                    {"pointer": pointer_join(pointer, keyword), "kind": keyword}
                )
                if keyword in {"if", "then", "else"}:
                    best_effort_groups.add((pointer, "if_then_else"))
                else:
                    best_effort_groups.add(
                        (pointer_join(pointer, keyword), keyword)
                    )
        all_of = node.get("allOf")
        if isinstance(all_of, list) and len(all_of) != 1:
            if len(all_of) > 1:
                best_effort.append(
                    {"pointer": pointer_join(pointer, "allOf"), "kind": "multiple_allof"}
                )
                best_effort_groups.add(
                    (pointer_join(pointer, "allOf"), "multiple_allof")
                )
            else:
                rejected.append(
                    {"pointer": pointer_join(pointer, "allOf"), "kind": "empty_allof_unsupported"}
                )

    return {
        "rejected_construct_count": len(rejected),
        "rejected_constructs": rejected,
        "best_effort_occurrence_count": len(best_effort),
        "best_effort_construct_group_count": len(best_effort_groups),
        "best_effort_construct_groups": [
            {"pointer": pointer, "kind": kind}
            for pointer, kind in sorted(best_effort_groups)
        ],
        "best_effort_occurrences": best_effort,
        "undocumented_guarantee_occurrence_count": len(undocumented_guarantees),
        "undocumented_guarantee_occurrences": undocumented_guarantees,
        "boolean_schema_count": len(boolean_schemas),
        "boolean_schemas": boolean_schemas,
        "array_schema_count": len(arrays),
        "array_forms": arrays,
        "prefix_items_occurrence_count": len(prefix_items),
        "prefix_items_pointers": prefix_items,
        "items_array_occurrence_count": len(items_arrays),
        "items_array_pointers": items_arrays,
        "pattern_count": len(pattern_audit),
        "patterns": pattern_audit,
        "all_patterns_in_supported_subset": all(item["supported_subset"] for item in pattern_audit),
        "all_patterns_avoid_documented_rejections": all(
            item["avoids_documented_rejected_features"] for item in pattern_audit
        ),
        "regex_semantic_uncertainty_count": len(regex_semantic_uncertainties),
        "regex_semantic_uncertainties": regex_semantic_uncertainties,
        "regex_exact_semantics_compatible": (
            not regex_semantic_uncertainties
            and all(
                item["intended_canonical_xai_semantics_equivalent"]
                for item in pattern_audit
            )
        ),
        "format_count": len(format_audit),
        "formats": format_audit,
        "constraints": constraints,
    }


def audit_canonical_schema(
    schema: Mapping[str, Any],
    raw: bytes,
    *,
    provider_neutral_inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an independent deterministic inventory of the canonical schema."""

    nodes = list(iter_schema_nodes(schema))
    mappings = [(pointer, node, depth) for pointer, node, depth in nodes if isinstance(node, Mapping)]
    objects = []
    for pointer, node, _ in mappings:
        if is_object_schema(node):
            objects.append(
                {
                    "pointer": pointer,
                    "property_count": len(node.get("properties", {}))
                    if isinstance(node.get("properties"), Mapping)
                    else 0,
                    "additional_properties_explicit": "additionalProperties" in node,
                    "additional_properties_value": node.get("additionalProperties", True),
                    "canonical_default_used": "additionalProperties" not in node,
                }
            )
    typed_object_count = sum(
        isinstance(node.get("type"), str) and node.get("type") == "object"
        for _, node, _ in mappings
    )
    references = [
        {"pointer": pointer_join(pointer, "$ref"), "value": node["$ref"]}
        for pointer, node, _ in mappings
        if "$ref" in node
    ]
    definitions = []
    for pointer, node, _ in mappings:
        defs = node.get("$defs")
        if isinstance(defs, Mapping):
            definitions.extend(
                {
                    "pointer": pointer_join(pointer_join(pointer, "$defs"), name),
                    "name": name,
                }
                for name in sorted(defs)
            )
    maximum_object_depth, maximum_array_nesting = _effective_depth_metrics(schema)
    reference_audit = audit_reference_graph(schema)
    independent = {
        "schema_version": schema.get("properties", {}).get("schema_version", {}).get("const"),
        "schema_sha256": sha256_bytes(raw),
        "schema_size_bytes": len(raw),
        "maximum_object_depth": maximum_object_depth,
        "property_count": sum(
            len(node.get("properties", {}))
            for _, node, _ in mappings
            if isinstance(node.get("properties"), Mapping)
        ),
        "required_property_count": sum(
            len(node.get("required", []))
            for _, node, _ in mappings
            if isinstance(node.get("required"), list)
        ),
        "ref_count": len(references),
        "one_of_count": sum("oneOf" in node for _, node, _ in mappings),
        "any_of_count": sum("anyOf" in node for _, node, _ in mappings),
        "all_of_count": sum("allOf" in node for _, node, _ in mappings),
        "enum_count": sum("enum" in node for _, node, _ in mappings),
        "const_count": sum("const" in node for _, node, _ in mappings),
        "nullable_union_count": sum(_is_nullable(node) for _, node, _ in mappings),
        "additional_properties_false_count": sum(
            node.get("additionalProperties") is False for _, node, _ in mappings
        ),
        "recursive_reference_count": reference_audit["circular_reference_count"],
        "maximum_array_item_nesting": maximum_array_nesting,
        "unbounded_array_count": sum(
            is_array_schema(node) and "maxItems" not in node for _, node, _ in mappings
        ),
    }
    reconciliation = {
        "provider_neutral_inventory_available": provider_neutral_inventory is not None,
        "independent_inventory": independent,
        "provider_neutral_inventory": dict(provider_neutral_inventory or {}),
    }
    reconciliation["exact_match"] = (
        provider_neutral_inventory is not None
        and independent == dict(provider_neutral_inventory)
    )
    combinators = {
        keyword: [
            {
                "pointer": pointer_join(pointer, keyword),
                "subschema_count": len(node[keyword])
                if isinstance(node[keyword], list)
                else None,
            }
            for pointer, node, _ in mappings
            if keyword in node
        ]
        for keyword in ("oneOf", "anyOf", "allOf")
    }
    enums = [
        {"pointer": pointer_join(pointer, "enum"), "values": node["enum"]}
        for pointer, node, _ in mappings
        if "enum" in node
    ]
    consts = [
        {"pointer": pointer_join(pointer, "const"), "value": node["const"]}
        for pointer, node, _ in mappings
        if "const" in node
    ]
    annotations = [
        {"pointer": pointer_join(pointer, key), "keyword": key, "value": node[key]}
        for pointer, node, _ in mappings
        for key in sorted(ANNOTATION_KEYS & set(node))
    ]
    null_admitting = [
        pointer
        for pointer, node, _ in mappings
        if _is_nullable(node)
        or (isinstance(node.get("enum"), list) and None in node["enum"])
    ]
    return {
        "schema_version": independent["schema_version"],
        "source_sha256": independent["schema_sha256"],
        "schema_byte_size": len(raw),
        "schema_node_count": len(nodes),
        "maximum_schema_node_depth": max((depth for _, _, depth in nodes), default=0),
        "maximum_object_depth": maximum_object_depth,
        "maximum_object_property_count": max(
            (entry["property_count"] for entry in objects), default=0
        ),
        "maximum_array_nesting": maximum_array_nesting,
        "unbounded_array_count": independent["unbounded_array_count"],
        "object_schema_count": len(objects),
        "typed_object_schema_count": typed_object_count,
        "implicit_object_applicator_schema_count": len(objects) - typed_object_count,
        "object_schemas": objects,
        "implicit_additional_properties_count": sum(
            entry["canonical_default_used"] for entry in objects
        ),
        "references": references,
        "definitions": definitions,
        "reference_graph_summary": {
            key: reference_audit[key]
            for key in (
                "reference_count",
                "unique_reference_target_count",
                "all_references_local_and_resolved",
                "circular_reference_count",
                "acyclic",
            )
        },
        "combinators": combinators,
        "enums": enums,
        "consts": consts,
        "nullable_union_count": independent["nullable_union_count"],
        "null_admitting_field_schema_count": len(null_admitting),
        "null_admitting_schema_pointers": null_admitting,
        "boolean_schemas": [
            {"pointer": pointer, "value": node}
            for pointer, node, _ in nodes
            if isinstance(node, bool)
        ],
        "annotations": annotations,
        "dialect_and_identifier_metadata": [
            {"pointer": f"/{_escape_pointer(key)}", "keyword": key, "value": schema[key]}
            for key in ("$schema", "$id")
            if key in schema
        ],
        "provider_neutral_reconciliation": reconciliation,
    }


def _validator_class():
    try:
        import jsonschema
    except ImportError as exc:
        raise PreflightError("jsonschema is required for semantic-equivalence validation") from exc
    return getattr(jsonschema, "Draft202012Validator", jsonschema.Draft7Validator)


def validation_errors(schema: Mapping[str, Any], instance: Any) -> tuple[str, ...]:
    """Return ordinary python-jsonschema observations for one instance."""

    validator_class = _validator_class()
    validator = validator_class(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    return tuple(
        f"/{'/'.join(_escape_pointer(str(item)) for item in error.absolute_path)}: {error.message}"
        for error in errors
    )


_RESTRICTED_VALIDATOR_CLASSES: dict[tuple[type[Any], str], type[Any]] = {}


def _restricted_validator_class(pattern_mode: str):
    """Extend jsonschema only for the proved current pattern subset."""

    if pattern_mode not in {"canonical_outer_anchors", "xai_full_string"}:
        raise PreflightError(f"unknown restricted pattern mode: {pattern_mode}")
    base = _validator_class()
    cache_key = (base, pattern_mode)
    if cache_key in _RESTRICTED_VALIDATOR_CLASSES:
        return _RESTRICTED_VALIDATOR_CLASSES[cache_key]
    from jsonschema import validators
    from jsonschema.exceptions import ValidationError

    def validate_restricted_pattern(
        validator: Any,
        pattern: Any,
        instance: Any,
        schema: Any,
    ) -> Iterable[Any]:
        del validator, schema
        if not isinstance(instance, str) or not isinstance(pattern, str):
            return
        if pattern_mode == "canonical_outer_anchors":
            proof = _prove_restricted_xai_outer_anchor_removal(pattern)
            if not proof["proved"]:
                yield ValidationError(
                    "canonical pattern is outside the restricted ECMA proof subset: "
                    f"{proof['failure_reason']}"
                )
                return
            provider_pattern = str(proof["provider_pattern"])
        else:
            proof = _prove_restricted_xai_provider_pattern(pattern)
            if not proof["proved"]:
                yield ValidationError(
                    "provider pattern is outside the restricted xAI proof subset: "
                    f"{proof['failure_reason']}"
                )
                return
            provider_pattern = pattern
        if not _restricted_full_string_match(provider_pattern, instance):
            yield ValidationError(
                f"{instance!r} does not match the proved full-string pattern"
            )

    result = validators.extend(base, {"pattern": validate_restricted_pattern})
    _RESTRICTED_VALIDATOR_CLASSES[cache_key] = result
    return result


def intended_validation_errors(
    schema: Mapping[str, Any],
    instance: Any,
    *,
    pattern_mode: str,
) -> tuple[str, ...]:
    """Validate with the narrow intended canonical or xAI pattern semantics."""

    validator = _restricted_validator_class(pattern_mode)(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    return tuple(
        f"/{'/'.join(_escape_pointer(str(item)) for item in error.absolute_path)}: {error.message}"
        for error in errors
    )


def _subschema_wrapper(root: Mapping[str, Any], subschema: Any) -> dict[str, Any]:
    wrapper: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [subschema],
    }
    if "$defs" in root:
        wrapper["$defs"] = root["$defs"]
    return wrapper


def _pattern_sample(pattern: str, min_length: int) -> str:
    proof = _prove_restricted_xai_outer_anchor_removal(pattern)
    if not proof["proved"]:
        raise PreflightError(
            f"cannot sample an unproved canonical pattern: {proof}"
        )
    provider_pattern = str(proof["provider_pattern"])
    candidates = [
        "a",
        "en",
        "en-US",
        "new-proposition-1",
        "new-proposition-group-1",
        "new-issue-1",
        "new-commitment-1",
        "new-obligation-1",
        "new-relation-1",
        "new-answer-target-1",
        "new-rejected-answer-target-1",
        "new-repair-1",
        "new-warning-1",
        "new-alternative-1",
    ]
    for candidate in candidates:
        if (
            len(candidate) >= min_length
            and _restricted_full_string_match(provider_pattern, candidate)
        ):
            return candidate
    raise PreflightError(f"no deterministic sample for canonical pattern: {pattern}")


def minimal_instance(
    schema: Mapping[str, Any],
    value: Any | None = None,
    *,
    active_refs: frozenset[str] = frozenset(),
) -> Any:
    """Generate a deterministic invented instance for a bounded local schema case."""

    value = schema if value is None else value
    if isinstance(value, bool):
        return None
    if not isinstance(value, Mapping):
        return None
    reference = value.get("$ref")
    if isinstance(reference, str):
        if reference in active_refs:
            raise PreflightError(f"cannot sample circular reference: {reference}")
        return minimal_instance(
            schema,
            resolve_pointer(schema, reference),
            active_refs=active_refs | {reference},
        )
    if "const" in value:
        return copy.deepcopy(value["const"])
    if isinstance(value.get("enum"), list) and value["enum"]:
        return copy.deepcopy(value["enum"][0])
    for keyword in ("oneOf", "anyOf"):
        alternatives = value.get(keyword)
        if isinstance(alternatives, list) and alternatives:
            if is_object_schema(value):
                return _minimal_object(
                    schema,
                    value,
                    active_refs,
                    selected_alternative=alternatives[0],
                )
            chosen = minimal_instance(schema, alternatives[0], active_refs=active_refs)
            return chosen
    types = _type_set(value)
    if "object" in types or is_object_schema(value):
        return _minimal_object(schema, value, active_refs)
    if "array" in types or is_array_schema(value):
        count = int(value.get("minItems", 0))
        item_schema = value.get("items", {})
        return _minimal_array_items(
            schema,
            item_schema,
            count,
            unique=value.get("uniqueItems") is True,
            active_refs=active_refs,
        )
    if "string" in types or not types and ("pattern" in value or "minLength" in value):
        minimum = int(value.get("minLength", 0))
        if isinstance(value.get("pattern"), str):
            return _pattern_sample(value["pattern"], minimum)
        return "x" * max(1, minimum)
    if "integer" in types:
        minimum = value.get("minimum", value.get("exclusiveMinimum", 0))
        number = math.ceil(minimum)
        if "exclusiveMinimum" in value and number <= value["exclusiveMinimum"]:
            number += 1
        return number
    if "number" in types:
        return float(value.get("minimum", 0))
    if "boolean" in types:
        return True
    if "null" in types:
        return None
    return None


def _minimal_object(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    active_refs: frozenset[str],
    *,
    selected_alternative: Any | None = None,
) -> dict[str, Any]:
    properties = schema.get("properties", {})
    result: dict[str, Any] = {}
    if isinstance(properties, Mapping):
        for name in schema.get("required", []):
            if name in properties:
                result[name] = minimal_instance(root, properties[name], active_refs=active_refs)
    alternatives = schema.get("anyOf")
    chosen_raw = selected_alternative
    if chosen_raw is None and isinstance(alternatives, list) and alternatives:
        chosen_raw = alternatives[0]
    chosen = _resolved_schema(root, chosen_raw)
    if isinstance(chosen, Mapping):
        chosen_properties = chosen.get("properties", {})
        if isinstance(chosen_properties, Mapping):
            for name, constraint in sorted(chosen_properties.items()):
                base_schema = properties.get(name, {}) if isinstance(properties, Mapping) else {}
                combined = _merge_schema_constraints(root, base_schema, constraint)
                result[name] = minimal_instance(
                    root, combined, active_refs=active_refs
                )
        for name in chosen.get("required", []):
            if name not in result:
                base_schema = properties.get(name, {}) if isinstance(properties, Mapping) else {}
                chosen_schema = (
                    chosen_properties.get(name, {})
                    if isinstance(chosen_properties, Mapping)
                    else {}
                )
                result[name] = minimal_instance(
                    root,
                    _merge_schema_constraints(root, base_schema, chosen_schema),
                    active_refs=active_refs,
                )
    minimum_properties = int(schema.get("minProperties", 0))
    if isinstance(properties, Mapping) and len(result) < minimum_properties:
        for name in sorted(properties):
            if name not in result:
                result[name] = minimal_instance(
                    root, properties[name], active_refs=active_refs
                )
            if len(result) >= minimum_properties:
                break
    return result


def _merge_schema_constraints(
    root: Mapping[str, Any],
    base: Any,
    overlay: Any,
) -> Any:
    """Merge the bounded object-property constraints used by synthetic cases."""

    base = copy.deepcopy(_resolved_schema(root, base))
    overlay = copy.deepcopy(_resolved_schema(root, overlay))
    if not isinstance(base, Mapping):
        return overlay
    if not isinstance(overlay, Mapping):
        return base
    result = dict(base)
    for key, child in overlay.items():
        if key == "properties" and isinstance(child, Mapping):
            current = result.get("properties", {})
            current = dict(current) if isinstance(current, Mapping) else {}
            for name, nested in child.items():
                current[name] = _merge_schema_constraints(
                    root, current.get(name, {}), nested
                )
            result[key] = current
        else:
            result[key] = child
    return result


def _instance_variants(value: Any) -> Iterable[Any]:
    """Yield deterministic nearby values for unique synthetic array members."""

    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        for increment in range(1, 16):
            yield value + increment
        return
    if isinstance(value, float):
        for increment in range(1, 16):
            yield value + increment
        return
    if isinstance(value, str):
        for suffix in range(1, 16):
            yield f"{value}{suffix}"
        return
    if isinstance(value, list):
        for index, member in enumerate(value):
            for replacement in _instance_variants(member):
                candidate = copy.deepcopy(value)
                candidate[index] = replacement
                yield candidate
        return
    if isinstance(value, Mapping):
        for name in sorted(value):
            for replacement in _instance_variants(value[name]):
                candidate = copy.deepcopy(dict(value))
                candidate[name] = replacement
                yield candidate


def _minimal_array_items(
    root: Mapping[str, Any],
    item_schema: Any,
    count: int,
    *,
    unique: bool,
    active_refs: frozenset[str],
) -> list[Any]:
    base = minimal_instance(root, item_schema, active_refs=active_refs)
    if not unique or count <= 1:
        return [copy.deepcopy(base) for _ in range(count)]
    wrapper = _subschema_wrapper(root, item_schema)
    result = [base]
    while len(result) < count:
        selected = None
        for candidate in _instance_variants(base):
            if candidate in result:
                continue
            if not validation_errors(wrapper, candidate):
                selected = candidate
                break
        if selected is None:
            raise PreflightError("could not construct distinct valid synthetic array items")
        result.append(selected)
        base = selected
    return result


def _root_semantic_delta(*, turn_index: int) -> dict[str, Any]:
    prior: dict[str, Any] | None = None
    if turn_index:
        prior = {"ledger_id": "synthetic-ledger-0", "as_of_turn_index": turn_index - 1}
    return {
        "schema_version": EXPECTED_CANONICAL_VERSION,
        "conversation_key": "synthetic:provider-preflight",
        "target_turn_id": f"synthetic-turn-{turn_index}",
        "as_of_turn_index": turn_index,
        "prior_ledger_reference": prior,
        "new_propositions": [],
        "proposition_updates": [],
        "new_proposition_groups": [],
        "proposition_group_updates": [],
        "new_issue_states": [],
        "issue_state_updates": [],
        "commitment_changes": [],
        "obligation_changes": [],
        "new_relations": [],
        "answer_target_changes": [],
        "rejected_answer_target_changes": [],
        "repair_records": [],
        "resolved_items": [],
        "extraction_status": "complete",
        "abstentions": [],
        "unsupported_inferences_rejected": 0,
        "warnings": [],
    }


def _existing_synthetic_semantic_delta_fixtures() -> list[tuple[str, dict[str, Any]]]:
    """Materialise the repository's existing wholly synthetic delta factories."""

    from tools import proposition_ledger_semantic_delta as semantic_delta

    _, _, behavioral = semantic_delta._synthetic_behavioral_validation_case(
        PROJECT_DIR
    )
    genesis_turn = {"turn_id": "synthetic-existing-genesis", "turn_index": 0}
    later_turn = {"turn_id": "synthetic-existing-later", "turn_index": 1}
    prior = {"ledger_id": "synthetic-existing-ledger", "as_of_turn_index": 0}
    fixtures: list[tuple[str, dict[str, Any]]] = [
        ("semantic_module_behavioral", behavioral),
        (
            "semantic_module_empty_genesis",
            semantic_delta._synthetic_empty_delta(
                "synthetic:existing-module", genesis_turn, None
            ),
        ),
        (
            "semantic_module_empty_later",
            semantic_delta._synthetic_empty_delta(
                "synthetic:existing-module", later_turn, prior
            ),
        ),
    ]

    test_path = PROJECT_DIR / "tests/test_proposition_ledger_semantic_delta.py"
    spec = importlib.util.spec_from_file_location(
        "_phase13_existing_semantic_delta_fixtures", test_path
    )
    if spec is None or spec.loader is None:
        raise PreflightError("could not load existing synthetic delta factories")
    test_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test_module)
    direct_factory = getattr(test_module.direct_answer_case, "__wrapped__", None)
    if direct_factory is None:
        raise PreflightError("existing direct-answer fixture is not callable locally")
    _, _, direct_delta = direct_factory()
    fixtures.extend(
        [
            ("semantic_test_direct_answer", direct_delta),
            (
                "semantic_test_noop_genesis",
                test_module._semantic_noop(
                    "synthetic:existing-test", genesis_turn, None
                ),
            ),
            (
                "semantic_test_noop_later",
                test_module._semantic_noop(
                    "synthetic:existing-test", later_turn, prior
                ),
            ),
        ]
    )
    return fixtures


def _agreement_case(
    case_id: str,
    canonical_schema: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    instance: Any,
    *,
    expected_valid: bool,
) -> dict[str, Any]:
    canonical_errors = intended_validation_errors(
        canonical_schema,
        instance,
        pattern_mode="canonical_outer_anchors",
    )
    provider_errors = intended_validation_errors(
        provider_schema,
        instance,
        pattern_mode="xai_full_string",
    )
    ordinary_canonical_errors = validation_errors(canonical_schema, instance)
    ordinary_provider_errors = validation_errors(provider_schema, instance)
    canonical_valid = not canonical_errors
    provider_valid = not provider_errors
    ordinary_canonical_valid = not ordinary_canonical_errors
    ordinary_provider_valid = not ordinary_provider_errors
    return {
        "case_id": case_id,
        "expected_valid": expected_valid,
        "canonical_valid": canonical_valid,
        "provider_valid": provider_valid,
        "validators_agree": canonical_valid == provider_valid,
        "expectation_met": canonical_valid == expected_valid and provider_valid == expected_valid,
        "canonical_error_count": len(canonical_errors),
        "provider_error_count": len(provider_errors),
        "ordinary_python_jsonschema": {
            "canonical_valid": ordinary_canonical_valid,
            "provider_valid": ordinary_provider_valid,
            "validators_agree": (
                ordinary_canonical_valid == ordinary_provider_valid
            ),
            "canonical_error_count": len(ordinary_canonical_errors),
            "provider_error_count": len(ordinary_provider_errors),
        },
    }


def build_semantic_equivalence_audit(
    canonical_schema: Mapping[str, Any],
    provider_schema: Mapping[str, Any],
    transformation_ledger: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare both schemas over structural witnesses and a bounded corpus."""

    cases: list[dict[str, Any]] = []
    existing_fixture_ids: list[str] = []
    for fixture_id, fixture in _existing_synthetic_semantic_delta_fixtures():
        existing_fixture_ids.append(fixture_id)
        cases.append(
            _agreement_case(
                f"existing_fixture:{fixture_id}",
                canonical_schema,
                provider_schema,
                fixture,
                expected_valid=True,
            )
        )
    genesis = _root_semantic_delta(turn_index=0)
    later = _root_semantic_delta(turn_index=1)
    cases.append(_agreement_case("root_genesis_valid", canonical_schema, provider_schema, genesis, expected_valid=True))
    cases.append(_agreement_case("root_later_valid", canonical_schema, provider_schema, later, expected_valid=True))
    canonical_optional_object = canonical_schema["$defs"]["groupChanges"]
    provider_optional_object = provider_schema["$defs"]["groupChanges"]
    optional_absent = {"structure_type": "independent_set"}
    cases.append(
        _agreement_case(
            "optional_field_absent_valid",
            _subschema_wrapper(canonical_schema, canonical_optional_object),
            _subschema_wrapper(provider_schema, provider_optional_object),
            optional_absent,
            expected_valid=True,
        )
    )
    optional_unexpected = copy.deepcopy(optional_absent)
    optional_unexpected["synthetic_unexpected_property"] = True
    cases.append(
        _agreement_case(
            "optional_object_unexpected_property_invalid",
            _subschema_wrapper(canonical_schema, canonical_optional_object),
            _subschema_wrapper(provider_schema, provider_optional_object),
            optional_unexpected,
            expected_valid=False,
        )
    )
    unbounded = copy.deepcopy(genesis)
    unbounded_item = minimal_instance(
        canonical_schema,
        canonical_schema["properties"]["new_propositions"]["items"],
    )
    unbounded["new_propositions"] = [
        copy.deepcopy(unbounded_item) for _ in range(257)
    ]
    cases.append(
        _agreement_case(
            "root_unbounded_array_257_valid",
            canonical_schema,
            provider_schema,
            unbounded,
            expected_valid=True,
        )
    )
    mutations: list[tuple[str, dict[str, Any]]] = []
    missing = copy.deepcopy(genesis)
    del missing["warnings"]
    mutations.append(("root_missing_required", missing))
    extra = copy.deepcopy(genesis)
    extra["unexpected"] = True
    mutations.append(("root_unexpected_property", extra))
    wrong_const = copy.deepcopy(genesis)
    wrong_const["schema_version"] = "synthetic-wrong-version"
    mutations.append(("root_wrong_const", wrong_const))
    wrong_enum = copy.deepcopy(genesis)
    wrong_enum["extraction_status"] = "synthetic-invalid"
    mutations.append(("root_wrong_enum", wrong_enum))
    wrong_nullable = copy.deepcopy(genesis)
    wrong_nullable["prior_ledger_reference"] = {"ledger_id": "synthetic-ledger-0", "as_of_turn_index": 0}
    mutations.append(("root_genesis_nonnull_prior", wrong_nullable))
    wrong_optional = copy.deepcopy(later)
    wrong_optional["prior_ledger_reference"] = None
    mutations.append(("root_later_null_prior", wrong_optional))
    wrong_array = copy.deepcopy(genesis)
    wrong_array["warnings"] = "not-an-array"
    mutations.append(("root_array_type", wrong_array))
    for case_id, instance in mutations:
        cases.append(_agreement_case(case_id, canonical_schema, provider_schema, instance, expected_valid=False))

    boundary_cases: list[dict[str, Any]] = []
    regex_semantic_evidence: list[dict[str, Any]] = []
    for pointer, node, _ in iter_schema_nodes(canonical_schema):
        if not isinstance(node, Mapping):
            continue
        for keyword in ("oneOf", "anyOf"):
            alternatives = node.get(keyword)
            if not isinstance(alternatives, list):
                continue
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            for index, alternative in enumerate(alternatives):
                if is_object_schema(node):
                    instance = _minimal_object(
                        canonical_schema,
                        node,
                        frozenset(),
                        selected_alternative=alternative,
                    )
                else:
                    instance = minimal_instance(canonical_schema, alternative)
                canonical_wrapper = _subschema_wrapper(canonical_schema, node)
                provider_wrapper = _subschema_wrapper(provider_schema, provider_node)
                boundary_cases.append(
                    _agreement_case(
                        f"{keyword}:{pointer_join(pointer_join(pointer, keyword), index)}",
                        canonical_wrapper,
                        provider_wrapper,
                        instance,
                        expected_valid=True,
                    )
                )
        if "enum" in node and isinstance(node["enum"], list) and node["enum"]:
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            invalid_enum_value: Any = "__synthetic_invalid_enum_value__"
            if invalid_enum_value in node["enum"]:
                invalid_enum_value = {"synthetic": "invalid"}
            for label, instance, expected in (
                ("first_valid", node["enum"][0], True),
                ("last_valid", node["enum"][-1], True),
                ("invalid", invalid_enum_value, False),
            ):
                boundary_cases.append(
                    _agreement_case(
                        f"enum:{label}:{pointer}",
                        _subschema_wrapper(canonical_schema, node),
                        _subschema_wrapper(provider_schema, provider_node),
                        instance,
                        expected_valid=expected,
                    )
                )
        if "const" in node:
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            invalid_const: Any = "__synthetic_invalid_const_value__"
            if invalid_const == node["const"]:
                invalid_const = {"synthetic": "invalid"}
            for label, instance, expected in (
                ("valid", node["const"], True),
                ("invalid", invalid_const, False),
            ):
                boundary_cases.append(
                    _agreement_case(
                        f"const:{label}:{pointer}",
                        _subschema_wrapper(canonical_schema, node),
                        _subschema_wrapper(provider_schema, provider_node),
                        instance,
                        expected_valid=expected,
                    )
                )
        if _is_nullable(node) or (
            isinstance(node.get("enum"), list) and None in node["enum"]
        ):
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            boundary_cases.append(
                _agreement_case(
                    f"nullable:null:{pointer}",
                    _subschema_wrapper(canonical_schema, node),
                    _subschema_wrapper(provider_schema, provider_node),
                    None,
                    expected_valid=True,
                )
            )
        if isinstance(node.get("pattern"), str):
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            valid = _pattern_sample(node["pattern"], int(node.get("minLength", 0)))
            provider_pattern = provider_node.get("pattern")
            if not isinstance(provider_pattern, str):
                raise PreflightError(
                    f"provider pattern missing at {pointer_join(pointer, 'pattern')}"
                )
            canonical_proof = _prove_restricted_xai_outer_anchor_removal(
                node["pattern"]
            )
            provider_proof = _prove_restricted_xai_provider_pattern(
                provider_pattern
            )
            if not canonical_proof["proved"] or not provider_proof["proved"]:
                raise PreflightError(
                    f"unproved current pattern at {pointer_join(pointer, 'pattern')}"
                )
            witness_cases = (
                ("valid", valid, True),
                ("invalid_leading_character", "!" + valid, False),
                ("invalid_trailing_character", valid + "!", False),
                ("terminal_lf", valid + "\n", False),
                ("terminal_cr", valid + "\r", False),
                ("terminal_crlf", valid + "\r\n", False),
                ("terminal_u2028", valid + "\u2028", False),
                ("terminal_u2029", valid + "\u2029", False),
                (
                    "embedded_newline",
                    valid[: max(1, len(valid) // 2)]
                    + "\n"
                    + valid[max(1, len(valid) // 2):],
                    False,
                ),
            )
            observations: list[dict[str, Any]] = []
            for label, instance, expected in witness_cases:
                boundary_cases.append(
                    _agreement_case(
                        f"pattern:{label}:{pointer}",
                        _subschema_wrapper(canonical_schema, node),
                        _subschema_wrapper(provider_schema, provider_node),
                        instance,
                        expected_valid=expected,
                    )
                )
                ordinary_canonical_accepts = not validation_errors(
                    _subschema_wrapper(canonical_schema, node), instance
                )
                ordinary_provider_accepts = not validation_errors(
                    _subschema_wrapper(provider_schema, provider_node), instance
                )
                intended_canonical_accepts = _restricted_full_string_match(
                    str(canonical_proof["provider_pattern"]), instance
                )
                xai_provider_accepts = _restricted_full_string_match(
                    provider_pattern, instance
                )
                observations.append(
                    {
                        "witness_kind": label,
                        "expected_acceptance": expected,
                        "ordinary_python_jsonschema": {
                            "canonical_accepts": ordinary_canonical_accepts,
                            "provider_schema_accepts": ordinary_provider_accepts,
                        },
                        "intended_canonical_restricted_ecma_semantics": {
                            "accepts": intended_canonical_accepts,
                        },
                        "xai_provider_full_string_semantics": {
                            "accepts": xai_provider_accepts,
                        },
                        "intended_semantics_agree": (
                            intended_canonical_accepts == xai_provider_accepts
                        ),
                    }
                )
            regex_semantic_evidence.append(
                {
                    "pointer": pointer_join(pointer, "pattern"),
                    "canonical_pattern": node["pattern"],
                    "provider_pattern": provider_pattern,
                    "restricted_subset_proof": canonical_proof,
                    "ordinary_python_jsonschema_is_contract_oracle": False,
                    "intended_canonical_xai_semantics_equivalent": all(
                        item["intended_semantics_agree"]
                        for item in observations
                    ),
                    "observations": observations,
                }
            )
        if is_array_schema(node) and isinstance(node.get("minItems"), int):
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            minimum = node["minItems"]
            at_minimum = minimal_instance(canonical_schema, node)
            below_minimum = at_minimum[: max(0, minimum - 1)]
            for label, length, expected in (
                ("at_minimum", minimum, True),
                ("below_minimum", max(0, minimum - 1), minimum == 0),
            ):
                instance = at_minimum if label == "at_minimum" else below_minimum
                boundary_cases.append(
                    _agreement_case(
                        f"array:{label}:{pointer}",
                        _subschema_wrapper(canonical_schema, node),
                        _subschema_wrapper(provider_schema, provider_node),
                        instance,
                        expected_valid=expected,
                    )
                )
        if is_array_schema(node) and node.get("uniqueItems") is True:
            provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
            item = minimal_instance(canonical_schema, node.get("items", {}))
            valid_count = max(1, int(node.get("minItems", 0)))
            unique_instance = _minimal_array_items(
                canonical_schema,
                node.get("items", {}),
                valid_count,
                unique=True,
                active_refs=frozenset(),
            )
            duplicate_instance = [
                copy.deepcopy(item)
                for _ in range(max(2, int(node.get("minItems", 0))))
            ]
            for label, instance, expected in (
                ("unique", unique_instance, True),
                ("duplicate", duplicate_instance, False),
            ):
                boundary_cases.append(
                    _agreement_case(
                        f"uniqueItems:{label}:{pointer}",
                        _subschema_wrapper(canonical_schema, node),
                        _subschema_wrapper(provider_schema, provider_node),
                        instance,
                        expected_valid=expected,
                    )
                )

    transformed_location_evidence: list[dict[str, Any]] = []
    for entry in transformation_ledger:
        if entry["transformation_kind"] == "identity":
            continue
        pointer = entry["canonical_json_pointer"]
        canonical_node = resolve_pointer(canonical_schema, f"#{pointer}" if pointer else "#")
        provider_node = resolve_pointer(provider_schema, f"#{pointer}" if pointer else "#")
        if entry["transformation_kind"] == (
            "insert_explicit_additional_properties_true"
        ):
            witness = minimal_instance(canonical_schema, canonical_node)
            if not isinstance(witness, dict):
                witness = {}
            witness["synthetic_unexpected_property"] = True
            counterfactual = copy.deepcopy(dict(provider_node))
            counterfactual["additionalProperties"] = False
            canonical_valid = not validation_errors(
                _subschema_wrapper(canonical_schema, canonical_node), witness
            )
            provider_valid = not validation_errors(
                _subschema_wrapper(provider_schema, provider_node), witness
            )
            counterfactual_valid = not validation_errors(
                _subschema_wrapper(provider_schema, counterfactual), witness
            )
            transformed_location_evidence.append(
                {
                    "pointer": pointer,
                    "transformation_kind": entry["transformation_kind"],
                    "canonical_accepts_open_property": canonical_valid,
                    "provider_accepts_open_property": provider_valid,
                    "counterfactual_provider_default_false_accepts": counterfactual_valid,
                    "transformation_necessary_and_equivalent": (
                        canonical_valid and provider_valid and not counterfactual_valid
                    ),
                }
            )
        elif entry["transformation_kind"] == (
            "remove_redundant_outer_anchors_for_xai_full_string_pattern"
        ):
            canonical_pattern = str(canonical_node)
            provider_pattern = str(provider_node)
            proof = _prove_restricted_xai_outer_anchor_removal(canonical_pattern)
            transformed_location_evidence.append(
                {
                    "pointer": pointer,
                    "transformation_kind": entry["transformation_kind"],
                    "canonical_pattern": canonical_pattern,
                    "provider_pattern": provider_pattern,
                    "restricted_subset_proof": proof,
                    "transformation_necessary_and_equivalent": bool(
                        proof["proved"]
                        and proof["provider_pattern"] == provider_pattern
                        and entry.get("canonical_pattern") == canonical_pattern
                        and entry.get("provider_pattern") == provider_pattern
                        and entry.get("proof_result")
                        == "exactly_equivalent_for_proved_restricted_pattern"
                    ),
                }
            )
        else:
            raise PreflightError(
                f"unknown transformation kind in semantic audit: {entry}"
            )

    all_cases = cases + boundary_cases
    exact_proof_results = {
        "exactly_equivalent",
        "exactly_equivalent_for_proved_restricted_pattern",
    }
    transformations_proved = all(
        entry.get("proof_result") in exact_proof_results
        for entry in transformation_ledger
    )
    unresolved_regex = [
        {
            "pointer": item["pointer"],
            "kind": "intended_canonical_xai_regex_semantic_mismatch",
        }
        for item in regex_semantic_evidence
        if not item["intended_canonical_xai_semantics_equivalent"]
    ]
    implementation_divergences = []
    for item in regex_semantic_evidence:
        terminal_lf = next(
            observation
            for observation in item["observations"]
            if observation["witness_kind"] == "terminal_lf"
        )
        if (
            terminal_lf["ordinary_python_jsonschema"]["canonical_accepts"]
            != terminal_lf["intended_canonical_restricted_ecma_semantics"][
                "accepts"
            ]
        ):
            implementation_divergences.append(
                {
                    "pointer": item["pointer"],
                    "kind": "python_jsonschema_regex_engine_divergence",
                    "witness_kind": "valid_value_plus_terminal_lf",
                    "ordinary_python_jsonschema_accepts": terminal_lf[
                        "ordinary_python_jsonschema"
                    ]["canonical_accepts"],
                    "intended_canonical_restricted_ecma_accepts": terminal_lf[
                        "intended_canonical_restricted_ecma_semantics"
                    ]["accepts"],
                    "provider_incompatibility": False,
                }
            )
    provider_patterns_supported = all(
        _prove_restricted_xai_provider_pattern(item["provider_pattern"])[
            "proved"
        ]
        for item in regex_semantic_evidence
    )
    return {
        "transformation_equivalence_proved": transformations_proved,
        "structurally_proved_transformation_equivalence": transformations_proved,
        "structurally_proved_equivalence": (
            transformations_proved and not unresolved_regex
        ),
        "overall_documented_provider_semantics_equivalent": not unresolved_regex,
        "documented_provider_regex_semantics_compatible": not unresolved_regex,
        "intended_regex_semantics_equivalent": not unresolved_regex,
        "ordinary_python_validator_agreement": all(
            item["ordinary_python_jsonschema"]["validators_agree"]
            for item in all_cases
        ),
        "ordinary_python_validator_is_contract_oracle": False,
        "python_validator_divergence_count": len(implementation_divergences),
        "implementation_divergences": implementation_divergences,
        "provider_schema_supported_subset": provider_patterns_supported,
        "provider_output_guarantee_complete": False,
        "canonical_postvalidation_required": True,
        "regex_semantic_evidence": regex_semantic_evidence,
        "regex_semantic_mismatch_count": len(unresolved_regex),
        "transformation_ledger_proofs_complete": all(
            entry.get("proof_result") in exact_proof_results
            for entry in transformation_ledger
        ),
        "transformed_location_proofs": transformed_location_evidence,
        "all_transformed_locations_proved": all(
            item["transformation_necessary_and_equivalent"]
            for item in transformed_location_evidence
        ),
        "existing_wholly_synthetic_semantic_delta_fixture_count": len(
            existing_fixture_ids
        ),
        "existing_wholly_synthetic_semantic_delta_fixture_ids": (
            existing_fixture_ids
        ),
        "note_on_existing_fixtures": (
            "The tracked Phase 1 files are persisted-ledger fixtures; existing "
            "semantic-delta fixtures are factory-generated in the semantic module and "
            "its tests, and are independently validated here by both schemas."
        ),
        "root_fixture_case_count": len(cases),
        "boundary_case_count": len(boundary_cases),
        "combinator_coverage": {
            "oneOf_occurrences": sum(
                isinstance(node, Mapping) and "oneOf" in node
                for _, node, _ in iter_schema_nodes(canonical_schema)
            ),
            "anyOf_occurrences": sum(
                isinstance(node, Mapping) and "anyOf" in node
                for _, node, _ in iter_schema_nodes(canonical_schema)
            ),
            "allOf_occurrences": sum(
                isinstance(node, Mapping) and "allOf" in node
                for _, node, _ in iter_schema_nodes(canonical_schema)
            ),
            "allOf_exercised_by_root_genesis_and_later_cases": True,
        },
        "actual_format_occurrence_count": sum(
            isinstance(node, Mapping) and "format" in node
            for _, node, _ in iter_schema_nodes(canonical_schema)
        ),
        "bounded_fixture_case_count": len(all_cases),
        "bounded_fixture_agreement": all(item["validators_agree"] for item in all_cases),
        "bounded_fixture_expectations_met": all(item["expectation_met"] for item in all_cases),
        "cases": all_cases,
        "unresolved_semantic_uncertainty": unresolved_regex,
    }


def scrub_provider_environment() -> None:
    """Remove named provider secrets without inspecting or recording their values."""

    for name in PROVIDER_KEY_ENV_NAMES:
        os.environ.pop(name, None)
    os.environ["XAI_SDK_DISABLE_TRACING"] = "1"
    os.environ["XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES"] = "1"


def probe_nonprivileged_network_namespace() -> dict[str, Any]:
    """Test whether a local unprivileged network namespace can be created."""

    executable = shutil.which("unshare")
    if executable is None:
        return {
            "attempted": False,
            "available": False,
            "status": "unshare_executable_unavailable",
        }
    result = subprocess.run(
        [executable, "-n", "true"],
        check=False,
        capture_output=True,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "XAI_SDK_DISABLE_TRACING": "1",
            "XAI_SDK_DISABLE_SENSITIVE_TELEMETRY_ATTRIBUTES": "1",
        },
    )
    return {
        "attempted": True,
        "available": result.returncode == 0,
        "status": (
            "available"
            if result.returncode == 0
            else "unavailable_in_execution_environment"
        ),
        "return_code": result.returncode,
        "subprocess_provider_credentials_present": False,
    }


@dataclass
class NetworkDenialGuard:
    """Fail-closed monkeypatch guard for socket, DNS, HTTP, and gRPC."""

    attempts: list[dict[str, str]] = field(default_factory=list)
    _restores: list[tuple[Any, str, Any]] = field(default_factory=list)

    def _deny(self, category: str, operation: str):
        def denied(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            self.attempts.append({"category": category, "operation": operation})
            raise NetworkDenied(f"network denied: {category}.{operation}")

        return denied

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        if hasattr(owner, name):
            self._restores.append((owner, name, getattr(owner, name)))
            setattr(owner, name, replacement)

    def __enter__(self) -> "NetworkDenialGuard":
        self._patch(socket.socket, "connect", self._deny("socket", "connect"))
        self._patch(socket.socket, "connect_ex", self._deny("socket", "connect_ex"))
        self._patch(socket, "create_connection", self._deny("socket", "create_connection"))
        self._patch(socket, "getaddrinfo", self._deny("dns", "getaddrinfo"))
        self._patch(socket, "gethostbyname", self._deny("dns", "gethostbyname"))
        self._patch(http.client.HTTPConnection, "connect", self._deny("http", "connect"))
        self._patch(http.client.HTTPSConnection, "connect", self._deny("http", "https_connect"))
        return self

    def patch_grpc(self, grpc_module: Any) -> None:
        """Replace synchronous and asynchronous gRPC channel constructors."""

        self._patch(grpc_module, "secure_channel", self._deny("grpc", "secure_channel"))
        self._patch(grpc_module, "insecure_channel", self._deny("grpc", "insecure_channel"))
        aio = getattr(grpc_module, "aio", None)
        if aio is not None:
            self._patch(aio, "secure_channel", self._deny("grpc", "aio_secure_channel"))
            self._patch(aio, "insecure_channel", self._deny("grpc", "aio_insecure_channel"))

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        while self._restores:
            owner, name, original = self._restores.pop()
            setattr(owner, name, original)


class _FailClosedRpc:
    def __init__(self, method: str):
        self.method = method
        self.call_count = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        self.call_count += 1
        raise NetworkDenied(f"RPC transport invocation denied: {self.method}")


class _RegistrationOnlyChannel:
    """Enough of grpc.Channel for generated stubs; never owns a transport."""

    def __init__(self) -> None:
        self.methods: list[_FailClosedRpc] = []

    def _register(self, method: str) -> _FailClosedRpc:
        call = _FailClosedRpc(method)
        self.methods.append(call)
        return call

    def unary_unary(self, method: str, *args: Any, **kwargs: Any) -> _FailClosedRpc:
        del args, kwargs
        return self._register(method)

    def unary_stream(self, method: str, *args: Any, **kwargs: Any) -> _FailClosedRpc:
        del args, kwargs
        return self._register(method)

    @property
    def rpc_invocation_count(self) -> int:
        return sum(method.call_count for method in self.methods)


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def compile_sdk_profiles(
    profiles: Sequence[Mapping[str, Any]],
    provider_schema: Mapping[str, Any],
    *,
    exercise_guard: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Build exact SDK protobufs under denial; never call a transport."""

    scrub_provider_environment()
    namespace_probe = probe_nonprivileged_network_namespace()
    guard = NetworkDenialGuard()
    results: list[dict[str, Any]] = []
    protos: list[Any] = []
    with guard:
        try:
            import grpc
        except ImportError as exc:
            raise PreflightError("grpc unavailable in pinned SDK environment") from exc
        guard.patch_grpc(grpc)
        try:
            from xai_sdk.chat import BaseChat, system, user
            from xai_sdk.proto import chat_pb2
            from xai_sdk.sync.chat import Client as ChatClient
        except ImportError as exc:
            raise PreflightError("xai-sdk unavailable in pinned SDK environment") from exc

        deliberate: list[tuple[str, Any]] = []
        if exercise_guard:
            deliberate = [
                ("socket", lambda: socket.create_connection(("127.0.0.1", 9))),
                ("dns", lambda: socket.getaddrinfo("synthetic.invalid", 443)),
                ("http", lambda: http.client.HTTPSConnection("synthetic.invalid").connect()),
                ("grpc", lambda: grpc.insecure_channel("synthetic.invalid:443")),
            ]
            for name, attempt in deliberate:
                try:
                    attempt()
                except NetworkDenied:
                    pass
                else:
                    raise PreflightError(f"network guard failed to block {name}")

        schema_text = canonical_json_bytes(provider_schema).decode("utf-8")
        for profile in profiles:
            channel = _RegistrationOnlyChannel()
            client = ChatClient(channel)
            response_format = chat_pb2.ResponseFormat(
                format_type=chat_pb2.FORMAT_TYPE_JSON_SCHEMA,
                schema=schema_text,
            )
            try:
                chat = client.create(
                    model=profile["model"],
                    messages=[
                        system("Synthetic proposition-ledger schema preflight only."),
                        user("Return the wholly synthetic structured result."),
                    ],
                    reasoning_effort="low",
                    tools=[],
                    tool_choice="none",
                    parallel_tool_calls=False,
                    response_format=response_format,
                    search_parameters=None,
                    store_messages=False,
                )
                request = BaseChat._make_request(chat, 1)
            except Exception as exc:
                results.append(
                    {
                        "profile_id": profile["profile_id"],
                        "model": profile["model"],
                        "request_construction_status": "rejected_locally",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "provider_calls_made": 0,
                    }
                )
                continue
            if channel.rpc_invocation_count:
                raise PreflightError("SDK local construction invoked an RPC")
            serialized = request.SerializeToString(deterministic=True)
            emitted_schema = json.loads(request.response_format.schema)
            chat_source = Path(sys.modules["xai_sdk.chat"].__file__).resolve()
            result = {
                "profile_id": profile["profile_id"],
                "provider": "xAI",
                "model": request.model,
                "reasoning_effort": chat_pb2.ReasoningEffort.Name(
                    request.reasoning_effort
                ).removeprefix("EFFORT_").lower(),
                "request_construction_status": "succeeded_without_transport",
                "sdk_schema_conversion_status": "raw_schema_preserved",
                "sdk_emitted_schema_sha256": value_sha256(emitted_schema),
                "provider_schema_sha256": value_sha256(provider_schema),
                "sdk_emitted_schema_equal": emitted_schema == provider_schema,
                "serialized_request_sha256": sha256_bytes(serialized),
                "serialized_request_byte_length": len(serialized),
                "tool_count": len(request.tools),
                "tool_choice": chat_pb2.ToolMode.Name(
                    request.tool_choice.mode
                ).removeprefix("TOOL_MODE_").lower(),
                "search_parameters_present": request.HasField("search_parameters"),
                "web_search_enabled": False,
                "x_search_enabled": False,
                "code_execution_enabled": False,
                "streaming": False,
                "message_count": len(request.messages),
                "synthetic_messages_only": True,
                "api_key_in_request": False,
                "rpc_invocation_count": channel.rpc_invocation_count,
                "provider_calls_made": 0,
                "inference_requests_made": 0,
                "model_list_requests_made": 0,
                "local_model_specific_rejection": False,
                "public_sdk_facility": "xai_sdk.sync.chat.Client.create",
                "raw_response_format_type": "xai_sdk.proto.chat_pb2.ResponseFormat",
                "private_helper": "xai_sdk.chat.BaseChat._make_request",
                "private_helper_reason": (
                    "construct the exact unary request copy and set n=1 without sampling"
                ),
                "private_helper_source_file": "xai_sdk/chat.py",
                "private_helper_source_sha256": sha256_bytes(chat_source.read_bytes()),
                "private_helper_fragility": "private SDK helper; revalidate on every SDK pin",
                "sdk_versions": {
                    "xai-sdk": _installed_version("xai-sdk"),
                    "pydantic": _installed_version("pydantic"),
                    "protobuf": _installed_version("protobuf"),
                    "grpcio": _installed_version("grpcio"),
                },
            }
            results.append(result)
            protos.append(request)

    profiles_only_model = False
    if len(protos) == 2:
        first = copy.deepcopy(protos[0])
        second = copy.deepcopy(protos[1])
        first.model = ""
        second.model = ""
        profiles_only_model = (
            first.SerializeToString(deterministic=True)
            == second.SerializeToString(deterministic=True)
        )
    counts = Counter(item["category"] for item in guard.attempts)
    network_audit = {
        "guard_method": "patched_socket_dns_http_grpc_plus_registration_only_channel",
        "network_namespace_probe": namespace_probe,
        "deliberate_connection_attempt_blocked": all(
            counts[name] >= 1 for name in ("socket", "dns", "http", "grpc")
        )
        if exercise_guard
        else True,
        "attempts_observed": dict(sorted(counts.items())),
        "attempt_log": guard.attempts,
        "provider_key_environment_names_removed_before_sdk_import": list(
            PROVIDER_KEY_ENV_NAMES
        ),
        "provider_calls_made": 0,
        "xai_inference_calls": 0,
        "openai_calls": 0,
        "x_calls": 0,
        "model_list_calls": 0,
        "transport_rpc_invocations": 0,
    }
    return results, network_audit, profiles_only_model


def validate_profiles_manifest(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Validate and return the two frozen profiles in model order."""

    if manifest.get("provider_call_authorised") is not False:
        raise PreflightError("profile manifest must forbid provider calls")
    if manifest.get("development_pilot_authorised") is not False:
        raise PreflightError("profile manifest must forbid a development pilot")
    if manifest.get("server_acceptance_status") != "not_tested":
        raise PreflightError("server acceptance must remain untested")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise PreflightError("profile manifest must contain exactly two profiles")
    expected = {
        "xai-grok-4.3-low-ledger-v1": "grok-4.3",
        "xai-grok-4.6-low-ledger-v1": "grok-4.6",
    }
    for profile in profiles:
        if profile.get("profile_id") not in expected:
            raise PreflightError("unexpected profile ID")
        if profile.get("model") != expected[profile["profile_id"]]:
            raise PreflightError("profile model mismatch")
        fixed = {
            "provider": "xAI",
            "reasoning_effort": "low",
            "sdk_package": "xai-sdk",
            "sdk_version": EXPECTED_XAI_SDK_VERSION,
            "tools": [],
            "web_search": False,
            "x_search": False,
            "code_execution": False,
            "streaming": False,
            "response_form": "strict_structured_json",
            "canonical_post_validation": "mandatory",
        }
        for key, value in fixed.items():
            if profile.get(key) != value:
                raise PreflightError(f"profile {profile['profile_id']} has invalid {key}")
    first = dict(profiles[0])
    second = dict(profiles[1])
    first.pop("profile_id")
    second.pop("profile_id")
    first.pop("model")
    second.pop("model")
    if first != second:
        raise PreflightError("profiles differ by more than model ID/profile ID")
    return tuple(sorted(profiles, key=lambda item: item["model"]))


def validate_rules_manifest(rules: Mapping[str, Any]) -> None:
    """Reject any drift or weakening of the frozen provider-rule manifest."""

    if value_sha256(rules) != EXPECTED_RULES_SEMANTIC_SHA256:
        raise PreflightError(
            "provider rule manifest differs from its frozen semantic digest"
        )
    expectations = rules.get("integrity_expectations", {})
    subset = rules.get("json_schema_subset", {})
    counts = {
        "supported_form_rule_count": len(subset.get("supported_forms", [])),
        "enforced_format_count": len(subset.get("enforced_formats", [])),
        "constraint_guarantee_rule_count": len(subset.get("constraint_guarantees", [])),
        "best_effort_rule_count": len(subset.get("best_effort_constructs", [])),
        "rejected_rule_count": len(subset.get("rejected_constructs", [])),
        "regex_supported_rule_count": len(subset.get("regex_subset", {}).get("supported", [])),
        "regex_unsupported_rule_count": len(subset.get("regex_subset", {}).get("unsupported", [])),
        "regex_semantic_difference_rule_count": len(
            subset.get("regex_subset", {}).get("provider_semantic_differences", [])
        ),
    }
    if any(expectations.get(key) != value for key, value in counts.items()):
        raise PreflightError(f"provider rule manifest integrity mismatch: {counts}")
    source_ids = {
        source.get("source_id")
        for source in rules.get("source_capture", {}).get("sources", [])
        if isinstance(source, Mapping)
    }
    required_regex_sources = set(
        expectations.get("required_regex_contract_source_ids", [])
    )
    if not required_regex_sources or not required_regex_sources <= source_ids:
        raise PreflightError(
            "provider rule manifest omits required regex contract provenance"
        )
    local_validator = rules.get("local_validator_implementation", {})
    if (
        local_validator.get("source_sha256")
        != expectations.get("required_local_validator_source_sha256")
        or local_validator.get(
            "implementation_is_normative_canonical_regex_authority"
        )
        is not False
        or local_validator.get("behavior_classification")
        != "python_jsonschema_regex_engine_divergence"
    ):
        raise PreflightError(
            "provider rule manifest local validator observation was weakened"
        )
    regex_contract = subset.get("regex_subset", {}).get(
        "normative_regex_contract", {}
    )
    if (
        regex_contract.get("canonical_dialect")
        != "ECMA-262_as_required_by_JSON_Schema_Draft_2020-12"
        or regex_contract.get("canonical_pattern_implicitly_anchored") is not False
        or regex_contract.get(
            "ecma_non_multiline_dollar_matches_before_final_line_terminator"
        )
        is not False
        or regex_contract.get("provider_pattern_implicitly_whole_string") is not True
    ):
        raise PreflightError(
            "provider rule manifest normative regex contract was weakened"
        )
    resolution = rules.get("documentation_resolution", {})
    expected_resolution = {
        "documentation_status": "resolved_by_more_specific_official_sources",
        "generic_reasoning_page_status": "incomplete_for_grok_4_3",
        "profile_documentation_status": "sufficiently_supported_for_local_preflight",
    }
    for key, value in expected_resolution.items():
        if resolution.get(key) != value:
            raise PreflightError(f"documentation resolution missing {key}={value}")


def _validated_lock_packages(lock: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Authenticate the lock before accepting any lock-controlled filename."""

    if value_sha256(lock) != EXPECTED_LOCK_SEMANTIC_SHA256:
        raise PreflightError(
            "dependency lock differs from its frozen semantic digest"
        )
    reconstruction = lock.get("reconstruction")
    if not isinstance(reconstruction, Mapping) or reconstruction.get(
        "credential_variables_to_unset_before_provider_import"
    ) != list(PROVIDER_KEY_ENV_NAMES):
        raise PreflightError(
            "dependency lock provider credential scrub list differs from code"
        )
    raw_packages = lock.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise PreflightError("dependency lock packages must be a non-empty list")
    packages: list[Mapping[str, Any]] = []
    wheel_filenames: set[str] = set()
    for index, package in enumerate(raw_packages):
        if not isinstance(package, Mapping):
            raise PreflightError(f"dependency lock package {index} is not an object")
        filename = package.get("wheel_filename")
        if (
            not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or Path(filename).is_absolute()
            or Path(filename).name != filename
        ):
            raise PreflightError(
                f"dependency lock package {index} has unsafe wheel_filename"
            )
        if filename in wheel_filenames:
            raise PreflightError(f"duplicate wheel_filename in lock: {filename}")
        wheel_filenames.add(filename)
        packages.append(package)
    return tuple(packages)


def _read_environment_ancillary(
    output_dir: Path,
    filename: str,
) -> bytes | None:
    """Read one fixed-name environment record from the run root or its parent."""

    for base in (output_dir, output_dir.parent):
        candidate = base / filename
        if os.path.lexists(candidate):
            return _read_regular_nonsymlink(
                candidate, f"environment ancillary {filename}"
            )
    return None


def _environment_record(
    lock: Mapping[str, Any], output_dir: Path
) -> dict[str, Any]:
    packages = _validated_lock_packages(lock)

    def normalize(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    installed = {
        normalize(str(distribution.metadata["Name"])): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    locked = {
        normalize(str(package["name"])): str(package["version"])
        for package in packages
    }
    bootstrap_expected = {
        name: str(record["version"])
        for name, record in lock.get("environment_bootstrap", {}).items()
        if name in {"pip", "setuptools"} and isinstance(record, Mapping)
    }
    bootstrap_installed = {
        name: installed.get(name) for name in sorted(bootstrap_expected)
    }
    relevant_installed = {
        name: installed.get(name) for name in sorted(locked)
    }
    unexpected = sorted(set(installed) - set(locked) - set(bootstrap_expected))

    libc_name, libc_version = platform.libc_ver()
    expected_python = dict(lock.get("python", {}))
    actual_python = {
        "abi_flags": getattr(sys, "abiflags", ""),
        "architecture": f"{struct.calcsize('P') * 8}bit",
        "binary_format": "ELF" if sys.platform.startswith("linux") else "unknown",
        "cache_tag": sys.implementation.cache_tag,
        "full_version": sys.version,
        "implementation": platform.python_implementation(),
        "implementation_name": sys.implementation.name,
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "libc_name": libc_name,
        "libc_version": libc_version,
        "machine": platform.machine(),
        "platform": sysconfig.get_platform(),
        "pointer_bits": struct.calcsize("P") * 8,
        "soabi": sysconfig.get_config_var("SOABI"),
        "version": platform.python_version(),
    }

    symlinks = sorted(
        str(path.relative_to(Path(sys.prefix)))
        for path in Path(sys.prefix).rglob("*")
        if path.is_symlink()
    )

    wheelhouse_candidates = (
        output_dir / "wheelhouse",
        output_dir.parent / "wheelhouse",
    )
    wheelhouse = None
    for candidate in wheelhouse_candidates:
        if os.path.lexists(candidate):
            wheelhouse = _require_safe_directory(candidate, "wheelhouse")
            break
    wheel_checks: list[dict[str, Any]] = []
    expected_wheel_names: set[str] = set()
    if wheelhouse is not None:
        for package in packages:
            filename = str(package["wheel_filename"])
            expected_wheel_names.add(filename)
            wheel = wheelhouse / filename
            regular = False
            actual_hash = None
            if os.path.lexists(wheel):
                try:
                    wheel_bytes = _read_regular_nonsymlink(
                        wheel, f"wheelhouse file {filename}"
                    )
                except PreflightError:
                    wheel_bytes = None
                else:
                    regular = True
                    actual_hash = sha256_bytes(wheel_bytes)
            wheel_checks.append(
                {
                    "filename": filename,
                    "regular_non_symlink": regular,
                    "expected_sha256": package["wheel_sha256"],
                    "actual_sha256": actual_hash,
                    "matched": regular and actual_hash == package["wheel_sha256"],
                }
            )
    wheelhouse_entries = list(wheelhouse.iterdir()) if wheelhouse is not None else []
    actual_wheel_names = {path.name for path in wheelhouse_entries}
    wheelhouse_entries_safe = all(
        not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode)
        for path in wheelhouse_entries
    )
    wheelhouse_exact = (
        wheelhouse is not None
        and actual_wheel_names == expected_wheel_names
        and wheelhouse_entries_safe
        and all(item["matched"] for item in wheel_checks)
    )

    requirements_bytes = "".join(
        f"{package['normalized_name']}=={package['version']} "
        f"--hash=sha256:{package['wheel_sha256']}\n"
        for package in packages
    ).encode("utf-8")
    wheel_manifest_bytes = "".join(
        f"{package['wheel_sha256']}  {package['wheel_filename']}\n"
        for package in packages
    ).encode("utf-8")
    source_digests = lock.get("source_digests", {})
    ancillary_specs = (
        ("sdk-dependency-graph.json", "dependency_graph_sha256"),
        ("sdk-installed-inventory.json", "installed_inventory_sha256"),
        ("sdk-requirements.lock", "requirements_lock_sha256"),
        ("sdk-wheel-sha256s.txt", "wheel_hash_manifest_sha256"),
    )
    ancillary_file_checks: list[dict[str, Any]] = []
    ancillary_bytes: dict[str, bytes | None] = {}
    for filename, digest_name in ancillary_specs:
        content = _read_environment_ancillary(output_dir, filename)
        ancillary_bytes[filename] = content
        actual_hash = sha256_bytes(content) if content is not None else None
        expected_hash = source_digests.get(digest_name)
        ancillary_file_checks.append(
            {
                "filename": filename,
                "digest_name": digest_name,
                "present_regular_non_symlink": content is not None,
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "matched": content is not None and actual_hash == expected_hash,
            }
        )
    materialized_lock_checks = {
        "requirements_lock_sha256": sha256_bytes(requirements_bytes),
        "requirements_lock_matched": sha256_bytes(requirements_bytes)
        == source_digests.get("requirements_lock_sha256"),
        "wheel_hash_manifest_sha256": sha256_bytes(wheel_manifest_bytes),
        "wheel_hash_manifest_matched": sha256_bytes(wheel_manifest_bytes)
        == source_digests.get("wheel_hash_manifest_sha256"),
        "requirements_file_matches_materialized_lock": (
            ancillary_bytes["sdk-requirements.lock"] == requirements_bytes
        ),
        "wheel_manifest_file_matches_materialized_manifest": (
            ancillary_bytes["sdk-wheel-sha256s.txt"] == wheel_manifest_bytes
        ),
    }

    xai_distribution = importlib.metadata.distribution("xai-sdk")
    source_paths = {
        "xai_sdk_client_py_sha256": "xai_sdk/client.py",
        "xai_sdk_distribution_metadata_sha256": (
            "xai_sdk-1.19.0.dist-info/METADATA"
        ),
        "xai_sdk_distribution_record_sha256": "xai_sdk-1.19.0.dist-info/RECORD",
        "xai_sdk_distribution_wheel_metadata_sha256": (
            "xai_sdk-1.19.0.dist-info/WHEEL"
        ),
        "xai_sdk_primary_chat_py_sha256": "xai_sdk/chat.py",
        "xai_sdk_proto_init_py_sha256": "xai_sdk/proto/__init__.py",
        "xai_sdk_proto_v6_chat_pb2_grpc_py_sha256": (
            "xai_sdk/proto/v6/chat_pb2_grpc.py"
        ),
        "xai_sdk_proto_v6_chat_pb2_py_sha256": "xai_sdk/proto/v6/chat_pb2.py",
        "xai_sdk_proto_v6_chat_pb2_pyi_sha256": "xai_sdk/proto/v6/chat_pb2.pyi",
        "xai_sdk_sync_chat_py_sha256": "xai_sdk/sync/chat.py",
        "xai_sdk_sync_client_py_sha256": "xai_sdk/sync/client.py",
        "xai_sdk_types_chat_py_sha256": "xai_sdk/types/chat.py",
    }
    installed_source_checks: list[dict[str, Any]] = []
    for digest_name, relative in sorted(source_paths.items()):
        source = Path(xai_distribution.locate_file(relative))
        regular = source.is_file() and not source.is_symlink()
        actual_hash = sha256_bytes(source.read_bytes()) if regular else None
        installed_source_checks.append(
            {
                "digest_name": digest_name,
                "relative_path": relative,
                "regular_non_symlink": regular,
                "expected_sha256": source_digests.get(digest_name),
                "actual_sha256": actual_hash,
                "matched": regular and actual_hash == source_digests.get(digest_name),
            }
        )

    sdk_files = sorted(
        (
            Path(xai_distribution.locate_file(file)),
            Path(str(file)).relative_to("xai_sdk").as_posix(),
        )
        for file in xai_distribution.files or ()
        if str(file).startswith("xai_sdk/")
        and Path(str(file)).suffix in {".py", ".pyi"}
    )
    tree_manifest = "".join(
        f"{sha256_bytes(path.read_bytes())}  {relative}\n"
        for path, relative in sdk_files
        if path.is_file() and not path.is_symlink()
    ).encode("ascii")
    source_tree_sha256 = sha256_bytes(tree_manifest)
    source_tree_matched = (
        len(sdk_files) == 146
        and source_tree_sha256
        == source_digests.get("xai_sdk_installed_source_tree_sha256")
    )

    checks = {
        "lock_semantic_digest_matched": value_sha256(lock)
        == EXPECTED_LOCK_SEMANTIC_SHA256,
        "python_fingerprint_matched": actual_python == expected_python,
        "bootstrap_versions_matched": bootstrap_installed == bootstrap_expected,
        "locked_versions_matched": relevant_installed == locked,
        "no_unexpected_distributions": not unexpected,
        "virtual_environment_symlink_free": not symlinks,
        "wheelhouse_exact_and_hash_verified": wheelhouse_exact,
        "environment_ancillary_hashes_matched": all(
            item["matched"] for item in ancillary_file_checks
        ),
        "materialized_requirements_hash_matched": materialized_lock_checks[
            "requirements_lock_matched"
        ],
        "materialized_wheel_manifest_hash_matched": materialized_lock_checks[
            "wheel_hash_manifest_matched"
        ],
        "materialized_requirements_file_matched": materialized_lock_checks[
            "requirements_file_matches_materialized_lock"
        ],
        "materialized_wheel_manifest_file_matched": materialized_lock_checks[
            "wheel_manifest_file_matches_materialized_manifest"
        ],
        "installed_source_files_matched": all(
            item["matched"] for item in installed_source_checks
        ),
        "installed_source_tree_matched": source_tree_matched,
    }
    return {
        "python": actual_python,
        "expected_python": expected_python,
        "bootstrap_versions": bootstrap_installed,
        "expected_bootstrap_versions": bootstrap_expected,
        "locked_versions": locked,
        "installed_locked_versions": relevant_installed,
        "unexpected_distributions": unexpected,
        "virtual_environment_symlink_count": len(symlinks),
        "virtual_environment_symlinks": symlinks,
        "wheel_checks": wheel_checks,
        "wheelhouse_exact_file_set": (
            actual_wheel_names == expected_wheel_names and wheelhouse_entries_safe
        ),
        "environment_ancillary_file_checks": ancillary_file_checks,
        "materialized_lock_checks": materialized_lock_checks,
        "installed_source_checks": installed_source_checks,
        "installed_source_tree_file_count": len(sdk_files),
        "installed_source_tree_sha256": source_tree_sha256,
        "checks": checks,
        "lock_matches_environment": all(checks.values()),
    }


def build_artifacts(
    *,
    output_dir: Path,
    generated_at_utc: str,
    git_commit: str,
) -> dict[str, Any]:
    """Build every substantive preflight artifact without provider transport."""

    profiles_manifest = load_json(PROFILES_PATH)
    rules = load_json(RULES_PATH)
    lock = load_json(LOCK_PATH)
    profiles = validate_profiles_manifest(profiles_manifest)
    validate_rules_manifest(rules)
    raw = CANONICAL_SCHEMA_PATH.read_bytes()
    canonical = load_json(CANONICAL_SCHEMA_PATH)
    if sha256_bytes(raw) != EXPECTED_CANONICAL_SHA256:
        raise PreflightError("canonical schema SHA-256 mismatch")
    if canonical.get("properties", {}).get("schema_version", {}).get("const") != EXPECTED_CANONICAL_VERSION:
        raise PreflightError("canonical schema version mismatch")
    from tools import proposition_ledger_provider_schema as neutral

    neutral_inventory = neutral.build_schema_feature_inventory(CANONICAL_SCHEMA_PATH)
    audit = audit_canonical_schema(
        canonical,
        raw,
        provider_neutral_inventory=neutral_inventory,
    )
    provider_schema, transformation_ledger = transform_provider_schema(canonical)
    keyword_audit = audit_provider_keywords(canonical)
    references = audit_reference_graph(canonical)
    oneof = prove_oneof_disjointness(canonical)
    semantic = build_semantic_equivalence_audit(
        canonical, provider_schema, transformation_ledger
    )
    environment = _environment_record(lock, output_dir)
    if not environment["lock_matches_environment"]:
        raise PreflightError(STATUS_DEPENDENCY_FAILURE)
    sdk_results, network, profiles_only_model = compile_sdk_profiles(
        profiles, provider_schema
    )

    local_rejections = [
        item for item in sdk_results if item["request_construction_status"] != "succeeded_without_transport"
    ]
    expected_sdk_versions = {
        name: next(
            package["version"]
            for package in lock["packages"]
            if package["name"] == name
        )
        for name in ("xai-sdk", "pydantic", "protobuf", "grpcio")
    }
    sdk_invariant_results: list[dict[str, Any]] = []
    for result in sdk_results:
        if result.get("request_construction_status") != "succeeded_without_transport":
            sdk_invariant_results.append(
                {
                    "model": result.get("model"),
                    "passed": False,
                    "failed_checks": ["request_construction_status"],
                }
            )
            continue
        invariant_checks = {
            "schema_preserved": result.get("sdk_emitted_schema_equal") is True,
            "schema_digest_matched": result.get("sdk_emitted_schema_sha256")
            == value_sha256(provider_schema),
            "reasoning_effort_low": result.get("reasoning_effort") == "low",
            "zero_tools": result.get("tool_count") == 0,
            "tool_choice_none": result.get("tool_choice") == "none",
            "search_absent": result.get("search_parameters_present") is False,
            "web_search_disabled": result.get("web_search_enabled") is False,
            "x_search_disabled": result.get("x_search_enabled") is False,
            "code_execution_disabled": result.get("code_execution_enabled") is False,
            "streaming_disabled": result.get("streaming") is False,
            "rpc_zero": result.get("rpc_invocation_count") == 0,
            "provider_calls_zero": result.get("provider_calls_made") == 0,
            "sdk_versions_exact": result.get("sdk_versions")
            == expected_sdk_versions,
        }
        sdk_invariant_results.append(
            {
                "model": result["model"],
                "passed": all(invariant_checks.values()),
                "checks": invariant_checks,
                "failed_checks": [
                    name for name, passed in invariant_checks.items() if not passed
                ],
            }
        )
    sdk_invariants_passed = (
        {item.get("model") for item in sdk_results} == {"grok-4.3", "grok-4.6"}
        and len(sdk_invariant_results) == 2
        and all(item["passed"] for item in sdk_invariant_results)
        and profiles_only_model
    )
    multiple_allof_unproved = any(
        item["kind"] == "multiple_allof"
        for item in keyword_audit["best_effort_occurrences"]
    )
    if local_rejections:
        status = STATUS_LOCAL_PROFILE_INCOMPATIBLE
    elif not environment["lock_matches_environment"]:
        status = STATUS_DEPENDENCY_FAILURE
    elif (
        not sdk_invariants_passed
        or keyword_audit["rejected_construct_count"]
        or not references["all_references_local_and_resolved"]
        or not references["acyclic"]
        or not oneof["all_oneof_pairs_structurally_disjoint"]
        or multiple_allof_unproved
    ):
        status = STATUS_INCOMPATIBLE
    elif (
        not semantic["bounded_fixture_agreement"]
        or not semantic["bounded_fixture_expectations_met"]
        or not semantic["all_transformed_locations_proved"]
        or not semantic["structurally_proved_transformation_equivalence"]
        or semantic["unresolved_semantic_uncertainty"]
        or not keyword_audit["regex_exact_semantics_compatible"]
    ):
        status = STATUS_INCOMPATIBLE
    elif (
        keyword_audit["best_effort_occurrence_count"]
        or keyword_audit["undocumented_guarantee_occurrence_count"]
    ):
        status = STATUS_POSTVALIDATION
    else:
        status = STATUS_EXACT

    canonical_postvalidation = {
        "strict_json_parse_required": True,
        "unchanged_canonical_schema_validation_required": True,
        "deterministic_semantic_reference_validation_required": True,
        "deterministic_materialiser_validation_required": True,
        "provider_schema_is_persistence_authority": False,
        "silent_field_dropping_allowed": False,
        "coercion_allowed": False,
        "repair_allowed": False,
        "malformed_or_provider_only_valid_result_may_enter_persisted_ledger": False,
        "retry_failure_policy_status": "pending_freeze_before_first_provider_call",
    }
    compatibility = {
        "record_id": "proposition-ledger-phase1.3-xai-regex-correction-v1",
        "status": status,
        "supersedes_compatibility_record_commit": (
            SUPERSEDED_COMPATIBILITY_RECORD_COMMIT
        ),
        "prior_disposition": PRIOR_DISPOSITION,
        "correction_reason": CORRECTION_REASON,
        "corrected_disposition": status,
        "provider_request_compatible": status in {STATUS_EXACT, STATUS_POSTVALIDATION},
        "provider_guarantee_incomplete": bool(
            keyword_audit["best_effort_occurrence_count"]
            or keyword_audit["undocumented_guarantee_occurrence_count"]
        ),
        "provider_output_guarantee_complete": False,
        "provider_schema_locally_serializable": sdk_invariants_passed,
        "overall_documented_provider_semantics_equivalent": semantic[
            "overall_documented_provider_semantics_equivalent"
        ],
        "incompatibility_reasons": semantic["unresolved_semantic_uncertainty"],
        "regex_semantic_mismatch_count": semantic[
            "regex_semantic_mismatch_count"
        ],
        "python_validator_divergence_count": semantic[
            "python_validator_divergence_count"
        ],
        "intended_regex_semantics_equivalent": semantic[
            "intended_regex_semantics_equivalent"
        ],
        "ordinary_python_validator_agreement": semantic[
            "ordinary_python_validator_agreement"
        ],
        "implementation_divergences": semantic[
            "implementation_divergences"
        ],
        "canonical_postvalidation_required": True,
        "server_acceptance_status": "not_tested",
        "provider_call_authorised": False,
        "development_pilot_authorised": False,
        "provider_calls": 0,
        "documentation_resolution": rules["documentation_resolution"],
        "canonical_schema_sha256": EXPECTED_CANONICAL_SHA256,
        "provider_schema_sha256": value_sha256(provider_schema),
        "transformation_count": sum(
            entry["transformation_kind"] != "identity" for entry in transformation_ledger
        ),
        "transformation_count_by_kind": dict(
            sorted(
                Counter(
                    entry["transformation_kind"]
                    for entry in transformation_ledger
                    if entry["transformation_kind"] != "identity"
                ).items()
            )
        ),
        "oneof_pair_proofs_passed": oneof["all_oneof_pairs_structurally_disjoint"],
        "reference_graph_acyclic": references["acyclic"],
        "rejected_construct_count": keyword_audit["rejected_construct_count"],
        "best_effort_occurrences": keyword_audit["best_effort_occurrences"],
        "undocumented_provider_guarantees": keyword_audit[
            "undocumented_guarantee_occurrences"
        ],
        "sdk_profile_construction_succeeded": not local_rejections,
        "local_profile_construction_status": (
            "local_profile_construction_incompatible"
            if local_rejections
            else "succeeded_without_transport"
        ),
        "sdk_invariants_passed": sdk_invariants_passed,
        "sdk_invariant_results": sdk_invariant_results,
        "compiled_profiles_differ_only_by_model_id": profiles_only_model,
        "network_denial_passed": network["deliberate_connection_attempt_blocked"],
        "canonical_postvalidation": canonical_postvalidation,
        "profile_comparison_status": "pending_not_run",
        "live_retry_loop_implemented": False,
    }
    checks = {
        "canonical_schema_hash_matched": sha256_bytes(raw) == EXPECTED_CANONICAL_SHA256,
        "canonical_schema_version_matched": audit["schema_version"] == EXPECTED_CANONICAL_VERSION,
        "provider_neutral_inventory_reconciled": audit["provider_neutral_reconciliation"]["exact_match"],
        "provider_rules_integrity_valid": True,
        "refs_local_resolved_and_acyclic": references[
            "all_references_local_and_resolved"
        ]
        and references["acyclic"],
        "oneof_structural_proofs_complete": oneof["all_oneof_pairs_structurally_disjoint"],
        "transformations_structurally_equivalent": semantic[
            "all_transformed_locations_proved"
        ],
        "bounded_fixture_agreement": semantic["bounded_fixture_agreement"],
        "bounded_fixture_expectations_met": semantic[
            "bounded_fixture_expectations_met"
        ],
        "sdk_result_records_complete": len(sdk_results) == 2
        and {item.get("model") for item in sdk_results}
        == {"grok-4.3", "grok-4.6"},
        "frozen_profile_manifests_validated": True,
        "network_denial_passed": network["deliberate_connection_attempt_blocked"],
        "dependency_lock_matches_environment": environment["lock_matches_environment"],
        "provider_calls_zero": True,
        "no_private_or_production_source_used": True,
    }
    compatibility_checks = {
        "no_rejected_construct": keyword_audit["rejected_construct_count"] == 0,
        "no_unproved_multiple_allof": not multiple_allof_unproved,
        "refs_exact": references["all_references_local_and_resolved"]
        and references["acyclic"],
        "oneof_exact": oneof["all_oneof_pairs_structurally_disjoint"],
        "transformations_exact": semantic[
            "structurally_proved_transformation_equivalence"
        ]
        and semantic["all_transformed_locations_proved"],
        "bounded_fixture_agreement": semantic["bounded_fixture_agreement"]
        and semantic["bounded_fixture_expectations_met"],
        "documented_provider_regex_semantics_exact": semantic[
            "documented_provider_regex_semantics_compatible"
        ]
        and keyword_audit["regex_exact_semantics_compatible"],
        "sdk_exact": sdk_invariants_passed,
    }
    validation = {
        "passed": all(checks.values()),
        "preflight_execution_valid": all(checks.values()),
        "compatibility_passed": status in {STATUS_EXACT, STATUS_POSTVALIDATION},
        "compatibility_checks": compatibility_checks,
        "compatibility_failure_correctly_recorded": (
            status not in {STATUS_EXACT, STATUS_POSTVALIDATION}
            and not all(compatibility_checks.values())
        ),
        "checks": checks,
        "provider_calls": 0,
        "real_conversation_records_read": 0,
        "held_out_records_read": 0,
        "production_writes": 0,
    }
    if not validation["passed"]:
        raise PreflightError(f"preflight validation failed: {checks}; status={status}")

    sdk_by_model = {result["model"]: result for result in sdk_results}
    source_provenance = {
        "source_capture": rules["source_capture"],
        "documentation_resolution": rules["documentation_resolution"],
        "official_source_discrepancies": rules["official_source_discrepancies"],
        "normative_regex_contract": rules["json_schema_subset"]["regex_subset"][
            "normative_regex_contract"
        ],
        "local_validator_implementation": rules[
            "local_validator_implementation"
        ],
        "complete_provider_documentation_snapshots_committed": False,
        "provider_calls": 0,
    }
    run_manifest = {
        "run_id": "proposition-ledger-phase1.3-xai-regex-correction-v1",
        "generated_at_utc": generated_at_utc,
        "private_run_path": str(output_dir.resolve()),
        "python_executable_path": str(Path(sys.executable).resolve()),
        "git_commit": git_commit,
        "supersedes_compatibility_record_commit": (
            SUPERSEDED_COMPATIBILITY_RECORD_COMMIT
        ),
        "prior_disposition": PRIOR_DISPOSITION,
        "correction_reason": CORRECTION_REASON,
        "corrected_disposition": status,
        "canonical_schema_path": str(CANONICAL_SCHEMA_PATH.relative_to(PROJECT_DIR)),
        "synthetic_only": True,
        "provider_calls": 0,
        "xai_inference_calls": 0,
        "openai_calls": 0,
        "x_calls": 0,
        "model_list_calls": 0,
        "real_conversation_records_read": 0,
        "held_out_records_read": 0,
        "production_writes": 0,
        "private_author_key_required": False,
    }
    determinism = {
        "comparison_status": "second_environment_pending",
        "substantive_artifact_names": list(SUBSTANTIVE_ARTIFACT_NAMES),
        "permitted_metadata_differences": [
            "generation timestamp",
            "private run path",
            "virtual environment path",
            "run-manifest checksum entry",
        ],
    }
    artifacts: dict[str, Any] = {
        "run-manifest.json": run_manifest,
        "provider-profile-manifest.json": profiles_manifest,
        "official-source-provenance.json": source_provenance,
        "xai-structured-output-rules.json": rules,
        "sdk-environment-lock.json": lock,
        "canonical-schema-audit.json": audit,
        "provider-schema.json": provider_schema,
        "provider-schema-transformation-ledger.json": {
            "canonical_schema_sha256": EXPECTED_CANONICAL_SHA256,
            "provider_schema_sha256": value_sha256(provider_schema),
            "transformation_count": compatibility["transformation_count"],
            "transformations": transformation_ledger,
        },
        "provider-schema-keyword-audit.json": keyword_audit,
        "oneof-disjointness-proofs.json": oneof,
        "reference-graph-audit.json": references,
        "semantic-equivalence-audit.json": semantic,
        "sdk-compilation-grok-4.3-low.json": sdk_by_model["grok-4.3"],
        "sdk-compilation-grok-4.6-low.json": sdk_by_model["grok-4.6"],
        "network-denial-audit.json": network,
        "compatibility-record.json": compatibility,
        "determinism-audit.json": determinism,
        "validation.json": validation,
    }
    return artifacts


def _write_private_json(path: Path, value: Any) -> None:
    _write_private_bytes(path, pretty_json_bytes(value), f"private artifact {path.name}")


def _write_checksums(output_dir: Path) -> None:
    output_dir = _require_safe_directory(output_dir, "checksum output root")
    lines = []
    for name in PRIVATE_ARTIFACT_NAMES:
        path = output_dir / name
        content = _read_regular_nonsymlink(path, f"checksum source {name}")
        lines.append(f"{sha256_bytes(content)}  {name}\n")
    checksum_path = output_dir / "SHA256SUMS"
    _write_private_bytes(
        checksum_path,
        "".join(lines).encode("utf-8"),
        "SHA256SUMS",
    )


def write_artifacts(output_dir: Path, artifacts: Mapping[str, Any]) -> None:
    """Write the private artifact set with restrictive permissions."""

    output_dir = _prepare_safe_output_directory(output_dir)
    for name in PRIVATE_ARTIFACT_NAMES:
        _write_private_json(output_dir / name, artifacts[name])
    _write_checksums(output_dir)


def compare_determinism(output_dir: Path, comparison_dir: Path) -> dict[str, Any]:
    """Compare substantive artifacts from two independently pinned environments."""

    output_dir = _require_safe_directory(output_dir, "first determinism root")
    comparison_dir = _require_safe_directory(
        comparison_dir, "second determinism root"
    )
    comparisons = []
    for name in SUBSTANTIVE_ARTIFACT_NAMES:
        first = output_dir / name
        second = comparison_dir / name
        first_bytes = _read_regular_nonsymlink(
            first, f"first determinism artifact {name}"
        )
        second_bytes = _read_regular_nonsymlink(
            second, f"second determinism artifact {name}"
        )
        first_hash = sha256_bytes(first_bytes)
        second_hash = sha256_bytes(second_bytes)
        comparisons.append(
            {
                "artifact": name,
                "first_sha256": first_hash,
                "second_sha256": second_hash,
                "first_regular_non_symlink": True,
                "second_regular_non_symlink": True,
                "byte_identical": first_bytes == second_bytes,
            }
        )
    first_manifest_path = output_dir / "run-manifest.json"
    second_manifest_path = comparison_dir / "run-manifest.json"
    first_manifest = _load_regular_json(
        first_manifest_path, "first determinism run manifest"
    )
    second_manifest = _load_regular_json(
        second_manifest_path, "second determinism run manifest"
    )
    if not isinstance(first_manifest, Mapping) or not isinstance(
        second_manifest, Mapping
    ):
        raise PreflightError("determinism run manifests must be JSON objects")
    if set(first_manifest) != set(second_manifest):
        raise PreflightError("determinism run manifest key sets differ")
    ignored_manifest_field_types = {
        "generated_at_utc": str,
        "private_run_path": str,
        "python_executable_path": str,
    }
    for field_name, required_type in ignored_manifest_field_types.items():
        if field_name not in first_manifest:
            raise PreflightError(
                f"determinism run manifests omit required metadata: {field_name}"
            )
        if type(first_manifest[field_name]) is not required_type or type(
            second_manifest[field_name]
        ) is not required_type:
            raise PreflightError(
                f"determinism metadata has invalid type: {field_name}"
            )
    ignored_manifest_fields = set(ignored_manifest_field_types)
    first_normalized_manifest = {
        key: value
        for key, value in first_manifest.items()
        if key not in ignored_manifest_fields
    }
    second_normalized_manifest = {
        key: value
        for key, value in second_manifest.items()
        if key not in ignored_manifest_fields
    }
    normalized_manifest_equal = first_normalized_manifest == second_normalized_manifest
    all_equal = (
        all(item["byte_identical"] for item in comparisons)
        and normalized_manifest_equal
    )
    return {
        "comparison_status": "byte_identical" if all_equal else "mismatch",
        "substantive_artifact_count": len(comparisons),
        "normalized_run_manifest_equal": normalized_manifest_equal,
        "run_manifests_regular_non_symlink": True,
        "run_manifest_key_sets_equal": True,
        "required_metadata_fields_present_and_typed": True,
        "normalized_run_manifest_first_sha256": value_sha256(
            first_normalized_manifest
        ),
        "normalized_run_manifest_second_sha256": value_sha256(
            second_normalized_manifest
        ),
        "normalized_run_manifest_ignored_fields": sorted(
            ignored_manifest_fields
        ),
        "all_substantive_outputs_byte_identical": all(
            item["byte_identical"] for item in comparisons
        )
        and normalized_manifest_equal,
        "comparisons": comparisons,
        "permitted_metadata_differences": [
            "generation timestamp",
            "private run path",
            "virtual environment path",
            "run-manifest checksum entry",
        ],
    }


def verify_stored_artifacts(
    output_dir: Path,
    rebuilt: Mapping[str, Any],
) -> tuple[str, ...]:
    """Reconcile rebuilt substantive artifacts and all recorded checksums."""

    errors: list[str] = []
    try:
        output_dir = _require_safe_directory(output_dir, "verify-only run root")
    except PreflightError as exc:
        return (str(exc),)
    expected_names = set(PRIVATE_ARTIFACT_NAMES)
    for name in PRIVATE_ARTIFACT_NAMES:
        stored = output_dir / name
        if not stored.is_file() or stored.is_symlink():
            errors.append(f"missing/non-regular artifact: {name}")
    for name in SUBSTANTIVE_ARTIFACT_NAMES:
        stored = output_dir / name
        expected = pretty_json_bytes(rebuilt[name])
        if stored.is_file() and not stored.is_symlink():
            try:
                actual = _read_regular_nonsymlink(
                    stored, f"stored substantive artifact {name}"
                )
            except PreflightError as exc:
                errors.append(str(exc))
            else:
                if actual != expected:
                    errors.append(f"substantive artifact differs: {name}")
    manifest_path = output_dir / "run-manifest.json"
    if manifest_path.is_file() and not manifest_path.is_symlink():
        try:
            manifest_bytes = _read_regular_nonsymlink(
                manifest_path, "stored run manifest"
            )
        except PreflightError as exc:
            errors.append(str(exc))
        else:
            if manifest_bytes != pretty_json_bytes(rebuilt["run-manifest.json"]):
                errors.append(
                    "run manifest differs beyond verify-only permitted metadata"
                )
    checksum_path = output_dir / "SHA256SUMS"
    checksum_entries: dict[str, str] = {}
    if not checksum_path.is_file() or checksum_path.is_symlink():
        errors.append("SHA256SUMS is missing, non-regular, or a symlink")
    else:
        try:
            checksum_source = _read_regular_nonsymlink(
                checksum_path, "stored SHA256SUMS"
            ).decode("utf-8")
        except (PreflightError, UnicodeDecodeError) as exc:
            errors.append(f"SHA256SUMS cannot be read safely: {exc}")
            checksum_source = ""
        for line_number, line in enumerate(checksum_source.splitlines(), start=1):
            if "  " not in line:
                errors.append(f"malformed checksum line: {line_number}")
                continue
            digest, name = line.split("  ", 1)
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not name
                or Path(name).name != name
            ):
                errors.append(f"unsafe/malformed checksum line: {line_number}")
                continue
            if name in checksum_entries:
                errors.append(f"duplicate checksum entry: {name}")
                continue
            checksum_entries[name] = digest
        if set(checksum_entries) != expected_names:
            errors.append(
                "checksum filename set mismatch: "
                f"missing={sorted(expected_names - set(checksum_entries))};"
                f"extra={sorted(set(checksum_entries) - expected_names)}"
            )
        for name, digest in sorted(checksum_entries.items()):
            path = output_dir / name
            if not path.is_file() or path.is_symlink():
                errors.append(f"checksum target missing/non-regular: {name}")
            else:
                try:
                    content = _read_regular_nonsymlink(
                        path, f"checksum target {name}"
                    )
                except PreflightError as exc:
                    errors.append(str(exc))
                else:
                    if sha256_bytes(content) != digest:
                        errors.append(f"checksum mismatch: {name}")
    symlinks = sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
        if path.is_symlink()
    )
    if symlinks:
        errors.append(f"private run contains symlinks: {symlinks}")
    validation_path = output_dir / "validation.json"
    if validation_path.is_file() and not validation_path.is_symlink():
        validation = _load_regular_json(validation_path, "stored validation")
        if validation.get("provider_calls") != 0:
            errors.append("stored validation does not record zero provider calls")
        if validation.get("real_conversation_records_read") != 0:
            errors.append("stored validation claims real conversation input")
        if validation.get("held_out_records_read") != 0:
            errors.append("stored validation claims held-out input")
    return tuple(errors)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git_head() -> str:
    git = shutil.which("git")
    if git is None:
        raise PreflightError("git executable unavailable")
    result = subprocess.run(
        [git, "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    lines = result.stdout.splitlines()
    if len(lines) != 1 or re.fullmatch(r"[0-9A-Fa-f]{40}", lines[0]) is None:
        raise PreflightError("git HEAD did not resolve to exactly one full commit ID")
    return lines[0].lower()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--record-determinism", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a build, verification, or second-environment comparison."""

    scrub_provider_environment()
    args = _parse_args(argv)
    output_dir = _lexical_absolute(args.output_dir)
    if args.record_determinism:
        comparison_dir = _lexical_absolute(args.record_determinism)
        audit = compare_determinism(output_dir, comparison_dir)
        if not audit["all_substantive_outputs_byte_identical"]:
            raise PreflightError("second-environment substantive output mismatch")
        _write_private_json(output_dir / "determinism-audit.json", audit)
        _write_checksums(output_dir)
        print(json.dumps({"status": audit["comparison_status"]}, sort_keys=True))
        return 0

    generated_at = _utc_now()
    stored_manifest_path = output_dir / "run-manifest.json"
    if args.verify_only:
        _require_safe_directory(output_dir, "verify-only run root")
        stored_manifest = _load_regular_json(
            stored_manifest_path, "verify-only run manifest"
        )
        if not isinstance(stored_manifest, Mapping):
            raise PreflightError("verify-only run manifest must be a JSON object")
        generated_at = stored_manifest.get("generated_at_utc")
        if not isinstance(generated_at, str):
            raise PreflightError(
                "verify-only run manifest has invalid generated_at_utc"
            )
    artifacts = build_artifacts(
        output_dir=output_dir,
        generated_at_utc=generated_at,
        git_commit=_git_head(),
    )
    if args.verify_only:
        errors = verify_stored_artifacts(output_dir, artifacts)
        if errors:
            raise PreflightError("verify-only failed: " + "; ".join(errors))
        print(json.dumps({"status": "verified", "provider_calls": 0}, sort_keys=True))
        return 0
    write_artifacts(output_dir, artifacts)
    print(
        json.dumps(
            {
                "status": artifacts["compatibility-record.json"]["status"],
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
