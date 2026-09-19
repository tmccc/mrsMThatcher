"""Conservative, local factual authority for the one-call reply contract.

No lexical-overlap or model label is treated as entailment. Every complete
sentence must either belong to a small premise-neutral conversational grammar
or reproduce a complete supplied fact passage. Whole-passage matching avoids
turning a denied, quoted or conditional fragment into an asserted fact. This
intentionally rejects unsupported paraphrases; a second model is never called.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone

# Closed phrases: no arbitrary captured prose that can conceal an assertion.
_SOCIAL = frozenset({
    'thank you', 'thanks', 'thank you for your kind words', 'you are welcome',
    "you're welcome", 'my condolences', 'i am sorry for your loss',
    "i'm sorry for your loss", 'wishing you well', 'take care', 'well said',
    'fair enough', 'i disagree', 'i agree', 'i see your point', 'a fair question',
    'could you clarify your question', 'what do you mean',
    'which principle do you have in mind', 'what would you prefer',
    'i cannot verify that claim from the supplied evidence',
    'i would want evidence before accepting that claim',
    'a little less rhetoric, please', 'let us disagree civilly',
    'let us keep the discussion civil', 'thank you for saying so',
    'thank you for the observation', 'thank you for sharing', 'thanks for sharing',
    'thanks for your kind words', 'my sympathies', 'i hope things get easier for you',
    'i hope tomorrow is kinder', 'wishing you strength', 'you have my sympathy',
    'i appreciate your point', 'i appreciate the distinction', 'i respect your view',
    'please take care', 'that sounds difficult',
})
_VALUES = (
    'responsibility|rhetoric|freedom|liberty|accountability|fairness|kindness|'
    'honesty|evidence|civil disagreement|individual choice|personal responsibility|'
    'sound judgement|principle|principles|respect|dignity|compassion|prudence|'
    'conviction|debate|clarity|restraint|patience|courtesy|tolerance|judgement|judgment|appearances'
)
_VALUE_OPINION = re.compile(
    rf'(?:(?:i value|i favour|i prefer|we should value|we should respect) (?:{_VALUES})'
    rf'|(?:{_VALUES}) (?:matters|matter)(?: more than (?:{_VALUES}))?'
    rf'|(?:{_VALUES}) (?:should|must) (?:matter|come first))', re.IGNORECASE,
)


def reply_sentences(text: str) -> list[str]:
    """Partition all prose without dropping unclassified punctuation or clauses."""
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]


def is_premise_neutral(sentence: str) -> bool:
    """Accept only closed conversational phrases or abstract value judgements."""
    prose = sentence.strip().rstrip('.!?').casefold()
    return prose in _SOCIAL or _VALUE_OPINION.fullmatch(prose) is not None


_CONTEXT_DEPENDENT_FACT = re.compile(
    r"\b(?:i|we|you|he|she|it|they|me|us|him|her|them|my|our|your|his|its|their|"
    r"mine|ours|yours|hers|theirs|this|that|these|those|here|there|now|today|"
    r"yesterday|tomorrow|tonight|recently|ago|later|earlier|then|last|next)\b",
    re.IGNORECASE,
)


def grounding_errors(reply: str, claims: object, used_ids: list[str], facts: object) -> list[str]:
    """Require complete inventory and whole-passage evidence for every assertion."""
    if not isinstance(claims, list) or len(claims) > 2:
        return ['invalid_factual_claims']
    facts_by_id = {
        row.get('id'): row.get('passage') for row in facts
        if isinstance(row, Mapping)
    } if isinstance(facts, list) else {}
    uncovered: list[str] = []
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
        uncovered.append(reply[cursor:position])
        cursor = position + len(text)
        bound_ids.update(ids)
        # Exact complete passage equality is deliberately stricter than substring
        # or token overlap. It preserves polarity, attribution and qualifications.
        if (_CONTEXT_DEPENDENT_FACT.search(text)
                or any(facts_by_id.get(fact_id) != text for fact_id in ids)):
            return ['unsupported_factual_claim']
    errors = []
    uncovered.append(reply[cursor:])
    if any(not is_premise_neutral(sentence)
           for gap in uncovered for sentence in reply_sentences(gap)):
        errors.append('factual_claim_inventory_mismatch')
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
