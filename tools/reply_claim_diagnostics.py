#!/usr/bin/env python3
"""Offline claim-risk diagnostics retained for reply-trial reporting."""

from __future__ import annotations

import re
from typing import Any


_YEAR = re.compile(r"(?<!\w)(?:1[0-9]{3}|20[0-9]{2}|2100)(?!\w)", re.IGNORECASE)
_PERCENT = re.compile(
    r"(?<!\w)(?:\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s+per\s+cent)(?!\w)",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"(?<!\w)(?:[£$€]\s*\d+(?:[.,]\d+)*(?:\s*(?:million|billion|trillion))?|"
    r"\d+(?:[.,]\d+)*\s*(?:pounds?|dollars?|euros?))(?!\w)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)*(?![\w-])")
_WRITTEN_QUANTITY = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:people|persons?|voters?|votes?|seats?|constituencies|countries|nations?|"
    r"years?|months?|weeks?|days?|hours?|minutes?|seconds?|points?|cases?|instances?|"
    r"examples?|items?|pages?|words?|speeches|interviews?|elections?|terms?|pounds?|"
    r"dollars?|euros?|miles?|yards?|feet|inches|kilometres?|meters?|metres?|acres?|"
    r"hectares?|tonnes?|kilograms?|kilos?|degrees?)\b",
    re.IGNORECASE,
)
_MAGNITUDE_QUANTITY = re.compile(
    r"\b(?:hundreds?|thousands?|millions?|billions?|trillions?)\b", re.IGNORECASE
)
_MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)"
)
_DATE = re.compile(
    rf"\b(?:{_MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|\d{{4}})|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?)\b"
)
_QUOTATION_SOURCE = (
    re.compile(
        r"\b(?:exact wording|word[- ]for[- ]word|quotation authenticity|"
        r"authentic quotation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:said|wrote|authored|coined|delivered|published|attributed to|"
        r"spoken by|written by)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:interview|transcript|source|authorship|attribution|publication|"
        r"speech record|archive card)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the quote|the quotation|the line)\s+"
        r"(?:is|was|comes|came|appears|matches)\b",
        re.IGNORECASE,
    ),
)
_TRANSLATION = (
    re.compile(
        r"\b(?:means|meant|translates? (?:as|to)|literally means|"
        r"is (?:an? )?(?:exact |literal )?translation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:linguistic nuance|etymolog(?:y|ical)|loanword|cognate|"
        r"grammatical status)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the (?:French|German|Italian|Spanish|Latin|Greek|Japanese|"
        r"Chinese|Russian|Arabic|foreign)\b[^.!?]{0,80}\b"
        r"(?:means|implies|carries|connotes))\b",
        re.IGNORECASE,
    ),
)
_PRIVATE_MOTIVE = (
    re.compile(
        r"\b(?:privately|secretly|in (?:his|her|their) heart|real motive|"
        r"true motive|hidden reason)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:he|she|they|[A-Z][a-z]+)\s+(?:really\s+)?"
        r"(?:hoped|feared|wanted|knew|intended|believed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:did|acted|said|wrote|insisted|refused)\s+(?:it|that|so)\s+"
        r"because\s+(?:he|she|they)\b",
        re.IGNORECASE,
    ),
)
_PREVALENCE = re.compile(
    r"\b(?:so many|many people|missed by many|admired by many|widely believed|"
    r"widely remembered|commonly regarded|most people|millions|countless|"
    r"universally|everyone knows)\b",
    re.IGNORECASE,
)
_HISTORICAL = (
    re.compile(
        r"\b(?:first\s+)?(?:became|served|won|lost|introduced|abolished|"
        r"privatised|nationalised|signed|founded|appointed|resigned|elected|"
        r"invaded|enacted|repealed|launched|established)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:during|before|after|under)\s+(?:the\s+)?"
        r"(?:war|election|government|administration|conference|summit|campaign)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:at the|in the)\s+[A-Z][A-Za-z'-]+"
        r"(?:\s+[A-Z][A-Za-z'-]+){0,3}\s+"
        r"(?:Conference|Summit|Election|Campaign|Address|Lecture)\b"
    ),
    re.compile(
        r"\b(?:was|were)\s+(?:not\s+)?(?:restricted|banned|required|prohibited|"
        r"permitted|replaced|created|closed|opened|charged|acquitted|convicted)\b",
        re.IGNORECASE,
    ),
)
_QUESTION_OPENING = re.compile(
    r"^\s*(?:what|which|who|whose|where|when|why|how|does|do|did|is|are|was|"
    r"were|can|could|would|will|should|may|might)\b",
    re.IGNORECASE,
)


def detect_claim_risk(reply: str, reply_requirement: str | None) -> dict[str, Any]:
    """Return diagnostic cues for the completed offline provider trial."""
    segments = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", str(reply or "").strip())
        if part.strip()
    ]
    assertions = " ".join(
        part
        for part in segments
        if not (part.endswith("?") or _QUESTION_OPENING.match(part))
    )
    categories: list[str] = []
    matched: list[str] = []

    def add(category: str, value: str) -> None:
        if category not in categories:
            categories.append(category)
            matched.append(value)

    if reply_requirement == "supported_factual":
        add("supported_factual_route", "reply_requirement=supported_factual")
    if assertions:
        number_hits = [
            match
            for pattern in (
                _PERCENT,
                _MONEY,
                _YEAR,
                _DATE,
                _NUMBER,
                _WRITTEN_QUANTITY,
                _MAGNITUDE_QUANTITY,
            )
            if (match := pattern.search(assertions))
        ]
        if number_hits:
            add(
                "numeric_or_date",
                min(number_hits, key=lambda item: item.start()).group(0),
            )
        for category, patterns in (
            ("quotation_or_source", _QUOTATION_SOURCE),
            ("translation_or_linguistic", _TRANSLATION),
            ("private_motive", _PRIVATE_MOTIVE),
            ("precise_historical_claim", _HISTORICAL),
        ):
            hits = [
                match for pattern in patterns if (match := pattern.search(assertions))
            ]
            if hits:
                add(category, min(hits, key=lambda item: item.start()).group(0))
        if match := _PREVALENCE.search(assertions):
            add("prevalence_or_popularity", match.group(0))
    return {
        "risky": bool(categories),
        "categories": categories,
        "matched_text": matched,
    }
