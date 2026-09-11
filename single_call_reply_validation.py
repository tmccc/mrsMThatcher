"""Shared rule vocabulary for reply validation diagnostics, without runtime I/O.

These names describe existing schema and mechanical checks. They neither run
validation nor change failure routing. Only allow-listed codes cross the log
boundary; exception prose, reply text and model reasoning are never projected.
"""

from __future__ import annotations


SCHEMA_VALIDATION_ERROR_CODES = frozenset({
    "output_not_text",
    "output_too_large",
    "strict_json",
    "output_not_object",
    "response_fields_mismatch",
    "invalid_decision",
    "invalid_reply_kind",
    "reply_not_string",
    "invalid_used_fact_ids",
    "invalid_reason_code",
})
MECHANICAL_VALIDATION_ERROR_CODES = frozenset({
    "duplicate_used_fact_ids",
    "unknown_fact_id",
    "invalid_reply_length_or_whitespace",
    "reply_sentence_limit_exceeded",
    "reply_kind_inconsistent",
    "reply_reason_inconsistent",
    "no_reply_text_not_empty",
    "no_reply_kind_inconsistent",
    "no_reply_used_facts_not_empty",
    "no_reply_reason_inconsistent",
    "direct_factual_missing_fact_id",
    "malformed_unicode",
    "control_or_format_character",
    "reply_contains_line_break",
    "reply_contains_link_or_address",
    "reply_contains_mention",
    "reply_contains_hashtag",
    "reply_contains_emoji",
    "exact_duplicate_reply",
    "near_duplicate_reply",
})
VALIDATION_ERROR_CODES = (
    SCHEMA_VALIDATION_ERROR_CODES | MECHANICAL_VALIDATION_ERROR_CODES
)
MAX_VALIDATION_ERROR_CODES = 32


def normalise_validation_error_codes(value: object) -> tuple[tuple[str, ...], int]:
    """Return distinct known codes and the count of redacted or unscanned values.

    Tuples support immutable pipeline results; JSON events carry lists. Repeated
    known codes describe one failed rule and do not count as lost diagnostics.
    """
    if not isinstance(value, (list, tuple)):
        return (), 1
    omitted = max(0, len(value) - MAX_VALIDATION_ERROR_CODES)
    codes: set[str] = set()
    for item in value[:MAX_VALIDATION_ERROR_CODES]:
        if type(item) is str and item in VALIDATION_ERROR_CODES:
            codes.add(item)
        else:
            omitted += 1
    return tuple(sorted(codes)), omitted
