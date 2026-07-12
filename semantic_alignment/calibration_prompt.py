from __future__ import annotations

import json
from typing import Any

from .calibration import RELATIONSHIPS, SCORES

CALIBRATION_CRITIC_PROMPT_VERSION = "picture-editor-critic-v3-calibration-1"


def calibration_critic_prompt(quote: dict[str, Any], image: dict[str, Any],
                              quote_decomposition: dict[str, Any],
                              image_decomposition: dict[str, Any]) -> str:
    payload = {
        "quote_fingerprint": quote,
        "image_fingerprint": image,
        "quote_local_decomposition": quote_decomposition,
        "image_local_decomposition": image_decomposition,
    }
    return f"""Prompt version: {CALIBRATION_CRITIC_PROMPT_VERSION}
Act as an experienced newspaper picture editor. A picture need not depict the causal
mechanism named by a quotation to be relevant: it may directly illustrate a claimed
consequence or a broader principle. Distinguish what the quotation argues from what
the image illustrates. Score mechanism, consequence, and principle matches separately.

Choose a primary relationship and, when useful, a different secondary relationship
from: {', '.join(RELATIONSHIPS)}. Do not call an image unrelated merely because it
shows a claimed outcome rather than the mechanism. Penalise ideological substitution
when a broad political argument displaces a specific policy claim, but preserve any
genuine consequence or principle match as a secondary relationship.

Return independent 0-100 scores for: {', '.join(SCORES)}. Overall suitability is an
editorial judgement, not an average. Identify extraneous arguments and missing primary
claims. Return only the strict structured JSON requested.

INPUT_FINGERPRINTS_AND_LOCAL_DERIVATIONS:
{json.dumps(payload, sort_keys=True)}"""
