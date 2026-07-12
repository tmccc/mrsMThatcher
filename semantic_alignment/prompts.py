from __future__ import annotations

import json
from typing import Any

from . import CRITIC_PROMPT_VERSION, IMAGE_PROMPT_VERSION, QUOTE_PROMPT_VERSION


def quote_prompt(quote_text: str) -> str:
    return f"""Prompt version: {QUOTE_PROMPT_VERSION}
Analyse this quotation independently. Do not discuss or imagine a particular image.
Identify its single dominant message, primary issue, primary and secondary themes,
specific concepts, explicit contrasts, and useful not_about boundaries. Extract a
core claim and concise declarative claims which preserve causation, contrast, and
value judgements actually expressed by the quotation. Mark claims primary or
secondary; do not invent implications. Suggest balanced visual evidence: concrete
human scenes, physical places, objects, institutions, commerce or industry, and
symbolic editorial concepts. Recommend charts only when the quotation genuinely
calls for quantitative evidence. Return only the requested structured JSON.

QUOTATION:\n{quote_text}"""


def image_prompt() -> str:
    return f"""Prompt version: {IMAGE_PROMPT_VERSION}
Treat the supplied image as an editorial image with no caption. Do not infer or ask
for a quotation, origin quotation, generation prompt, filename meaning, or existing
labels. State the single message an average viewer would infer, secondary messages,
primary issue, concepts, emotional framing, ambiguity, and concrete visual evidence.
Extract a core implied claim and concise declarative implied claims. Every claim must
cite concrete visible support. Mark it dominant, secondary, or speculative. Avoid
over-interpreting portraits, generic symbols, or visual ambiguity. Infer only from
pixels, not likely intent.
Return only the requested structured JSON."""


CRITIC_ALLOWED_QUOTE = {
    "schema_version", "analysis_kind", "quote_hash", "quote_text", "dominant_message",
    "core_claim", "claims",
    "primary_issue", "primary_themes", "secondary_themes", "specific_concepts",
    "desired_visual_evidence", "explicit_contrasts", "not_about", "confidence",
}
CRITIC_ALLOWED_IMAGE = {
    "schema_version", "analysis_kind", "image_basename", "sha256", "dominant_message",
    "core_implied_claim", "implied_claims",
    "primary_issue", "primary_themes", "secondary_messages", "specific_concepts",
    "visual_evidence", "emotional_tone", "ambiguity", "confidence",
}


def critic_prompt(quote: dict[str, Any], image: dict[str, Any]) -> str:
    clean_quote = {key: quote[key] for key in sorted(CRITIC_ALLOWED_QUOTE) if key in quote}
    clean_image = {key: image[key] for key in sorted(CRITIC_ALLOWED_IMAGE) if key in image}
    return f"""Prompt version: {CRITIC_PROMPT_VERSION}
Act as an editorial critic. Compare only the two independent fingerprints below.
Do not infer generation history, production winner, branch, engagement, or prior score.
Score semantic alignment separately from directness, editorial power, and visual
specificity. Compare proposition-level claims before themes. Identify matched claims,
primary quote claims left unillustrated, and strong image claims the quote does not
require. Penalise broad ideology substituted for a specific policy, a secondary idea
dominating the image, and arguments introduced by the image but absent from the quote.
Classify exactly one relationship: direct_equivalence, strong_support, partial_support,
related_but_not_equivalent, generic_ideological_substitution, secondary_theme_only,
contradiction, unrelated, or ambiguous. Set mismatch_type to that same relationship.
90-100 exceptional direct illustration; 75-89 strong/specific; 60-74 broadly relevant
but indirect; 40-59 weak/secondary; 0-39 misleading, unrelated, or contradictory.
Return only structured JSON.

QUOTE_FINGERPRINT:\n{json.dumps(clean_quote, sort_keys=True)}
IMAGE_FINGERPRINT:\n{json.dumps(clean_image, sort_keys=True)}"""
