"""Deterministic checks on the one-call model's declared factual inventory.

The model identifies factual assertions and assesses meaning. Local checks bind
declared spans to exact supplied passages; they neither classify the remaining
prose nor prove inventory completeness or arbitrary semantic entailment. Natural
non-factual conversation has no vocabulary restriction. No second model is called.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone

MAX_FACTUAL_CLAIMS = 32

_CONTEXT_DEPENDENT_FACT = re.compile(
    r"\b(?:i|we|you|he|she|it|they|me|us|him|her|them|my|our|your|his|its|their|"
    r"mine|ours|yours|hers|theirs|this|that|these|those|here|there|now|today|"
    r"yesterday|tomorrow|tonight|recently|ago|later|earlier|then|last|next)\b",
    re.IGNORECASE,
)


def grounding_errors(reply: str, claims: object, used_ids: list[str], facts: object) -> list[str]:
    """Check declared spans and exact evidence, not undeclared prose or meaning."""
    if not isinstance(claims, list) or len(claims) > MAX_FACTUAL_CLAIMS:
        return ['invalid_factual_claims']
    facts_by_id = {
        row.get('id'): row.get('passage') for row in facts
        if isinstance(row, Mapping)
    } if isinstance(facts, list) else {}
    cursor = 0
    bound_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'text', 'fact_ids'}:
            return ['invalid_factual_claims']
        text = claim.get('text')
        ids = claim.get('fact_ids')
        if (not isinstance(text, str) or not text or not isinstance(ids, list)
                or not ids or len(ids) > 32
                or any(not isinstance(item, str) for item in ids)
                or len(ids) != len(set(ids))):
            return ['invalid_factual_claims']
        position = reply.find(text, cursor)
        if (position < 0
                or (position and (not reply[position - 1].isspace()
                                  or not reply[:position].rstrip().endswith(('.', '!', '?'))))
                or (position + len(text) < len(reply)
                    and (not reply[position + len(text)].isspace()
                         or not text.endswith(('.', '!', '?'))))):
            return ['factual_claim_inventory_mismatch']
        cursor = position + len(text)
        bound_ids.update(ids)
        # A conservative equality check, not a semantic entailment claim. The
        # model must still assess relevance, meaning and inventory completeness.
        if (_CONTEXT_DEPENDENT_FACT.search(text)
                or any(facts_by_id.get(fact_id) != text for fact_id in ids)):
            return ['unsupported_factual_claim']
    errors = []
    if bound_ids != set(used_ids):
        errors.append('factual_claim_fact_ids_mismatch')
    return errors


def canonical_time_context(context: Mapping[str, object]) -> dict[str, str | None]:
    """Validate a UTC calendar date and optional canonical UTC source timestamp.

    Missing source timestamps remain explicitly unknown. Invalid supplied times
    fail closed. UTC Z and +00:00 are accepted and normalised to Z.
    """
    current_date = context.get('current_date')
    if (not isinstance(current_date, str)
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}', current_date) is None):
        raise ValueError('current_date must be a canonical UTC calendar date')
    date.fromisoformat(current_date)
    target = context.get('target_created_at')
    if target in (None, ''):
        return {'current_date': current_date, 'target_created_at': None}
    if (not isinstance(target, str)
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', target) is None):
        raise ValueError('target_created_at must be an explicit UTC timestamp')
    parsed = datetime.fromisoformat(target.replace('Z', '+00:00'))
    return {'current_date': current_date, 'target_created_at': parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}
