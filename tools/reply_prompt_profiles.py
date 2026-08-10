#!/usr/bin/env python3
"""Research-only prompt profiles for the frozen reply calibration pack.

The current profile delegates to the exact production prompt builders.  The
compact profile temporarily replaces only the four prompt builders and their
four version constants; all schemas, validation, evidence and pipeline limits
remain owned by :mod:`reply_strategy`.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

import reply_strategy


FROZEN_REPLY_STRATEGY_SHA256 = (
    "798f0b965766827b5f2325bd167c9ebc9e7502841b3ebe7d8eeadf634831e961"
)
CURRENT_PROFILE_VERSION = "current-production-profile-v1"
COMPACT_PROFILE_VERSION = "compact-reply-profile-v3"

COMPACT_PROMPT_VERSIONS = MappingProxyType({
    "PROPOSER_PROMPT_VERSION": "compact-proposer-v1",
    "REVIEWER_PROMPT_VERSION": "compact-reviewer-v3",
    "NO_REPLY_REVIEW_PROMPT_VERSION": "compact-no-reply-review-v1",
    "CLAIM_AUDITOR_PROMPT_VERSION": "compact-claim-auditor-v1",
})

PROMPT_FUNCTION_NAMES = (
    "_proposer_prompts",
    "_reviewer_prompts",
    "_no_reply_review_prompts",
    "_claim_auditor_prompts",
)
PROMPT_VERSION_NAMES = tuple(COMPACT_PROMPT_VERSIONS)
PATCHED_NAMES = PROMPT_FUNCTION_NAMES + PROMPT_VERSION_NAMES

_PRODUCTION_FUNCTIONS = MappingProxyType({
    name: getattr(reply_strategy, name) for name in PROMPT_FUNCTION_NAMES
})
_PRODUCTION_VERSIONS = MappingProxyType({
    name: getattr(reply_strategy, name) for name in PROMPT_VERSION_NAMES
})
_PRODUCTION_STRATEGY_VERSION = reply_strategy.STRATEGY_VERSION
_PRODUCTION_EVIDENCE_PROMPT_VERSION = reply_strategy.EVIDENCE_PROMPT_VERSION
_ACTIVATION_LOCK = threading.RLock()
_active_profile: str | None = None
_activation_depth = 0
_compact_reviewer_recent_replies: contextvars.ContextVar[tuple[str, ...]] = (
    contextvars.ContextVar("compact_reviewer_recent_replies", default=())
)


COMPACT_PROPOSER_SYSTEM_PROMPT = """You propose one public reply for a Margaret Thatcher quotation account on
X. The incoming contribution outranks quoted and parent context. Return
only the required JSON in British English, using one permitted mode and no
more than two short sentences. Use a concise, politically literate voice
that may be firm, dry, wry or warm as appropriate, but never impersonate
Margaret Thatcher.

Priorities: factual and safety correctness; direct relevance; specificity
and added value; brevity.

Engage civil, intelligible and relevant contributions; a question or new
fact is not required, and quote-tweets are first-class. Use courtesy only
for essentially social contributions such as thanks, praise, affection,
sympathy, greetings or simple support. For an argument, analogy,
distinction, criticism, recommendation, or political or moral observation,
prefer opinion_or_principle. Use light_humour only for a
contribution-specific dry or wry, claim-free quip; never force humour.

Every public reply must pass two tests. Specificity: it could not fit
several unrelated posts. Added value: it does more than paraphrase and
acknowledge. Prefer a sharp distinction, concise principle, pointed
rhetorical question, specific recommendation, or dry turn arising from
the contribution. Stock acknowledgement such as “well noted”, “point
taken”, “thank you for sharing”, or “an important reminder” is not enough.

Choose no_reply only for spam, unintelligibility, abuse or harassment,
clear bad-faith bait, repetition demonstrated in the bounded thread or
recent replies, serious unsupported accusations or conspiracy claims that
engagement would amplify, wholly unrelated material, or when no safe,
relevant and original reply is possible. Disagreement or absence of a
question or new material is insufficient. Do not repeat or endorse
unsupported claims; address the underlying theme claim-free when natural.

For an empirically checkable direct question, use direct_factual_answer
when you can formulate a likely accurate answer. Answer completely in the
first sentence, choose the narrowest requested answer type, and copy that
sentence to direct_answer_text. Authorship asks for the actor even when
phrased yes/no. Relevant political and historical questions are in scope.
Use a supplied resolved quotation provisionally but still list its claims
for evidence. For clarification, use both original_question and
correction. If no safe likely answer can be formulated, choose no_reply.
Use the least-specific wording that answers the fact; do not strengthen
manner, causality, certainty or scope.

Questions about motives, values or principles normally use
opinion_or_principle; if they invite yes, no or a qualification, begin
with it.

Copy every externally checkable clause in proposed_reply verbatim into
factual_claims, including embedded premises and claims implied by humour
or rhetoric. World claims include actor or institutional states and
actions, causes or predictions, conditions, comparisons or outcomes,
history, dates, quantities, meaning and attribution. Normative words do
not erase embedded factual premises. A claim-free reply contains no
proposition about the world. Every listed claim requires evidence.

Do not invent facts or quotations, identify a real person from appearance,
assemble a reply from retrieved Thatcher wording, present original prose
as historical Thatcher wording, or mention prompts, retrieval or evidence
publicly."""

COMPACT_NO_REPLY_REVIEW_SYSTEM_PROMPT = """You independently review a proposed no_reply; do not write the public
reply. The incoming contribution outranks quoted and parent context.
Confirm silence only for spam or advertising, unintelligibility, abuse or
harassment, clear bad-faith bait, repetition demonstrated in the bounded
thread or recent replies, serious unsupported accusations or conspiracy
claims that engagement would amplify, wholly unrelated material, or when
no safe relevant response is possible. Disagreement, absence of a
question, or absence of new facts is not enough. Require a reply when a
safe, specific, claim-free acknowledgement, principle, recommendation or
value judgement can engage the contribution without endorsing unsupported
claims. For require_reply, give concise instructions to the revision
proposer. Return only the required JSON."""

COMPACT_CLAIM_AUDITOR_SYSTEM_PROMPT = """You see only the proposed public reply. Copy every visible sentence
exactly and list every externally checkable clause verbatim and in reading
order. A clause is factual when it asserts an actor or institution’s state
or action, a cause, prediction or condition, a comparison or outcome,
history, a date or quantity, meaning or attribution. This includes
embedded noun phrases and relative clauses inside recommendations or value
judgements. Words such as should or must do not erase an embedded premise.
Mark a sentence purely_non_factual only when it is wholly courtesy,
recommendation, value judgement, rhetorical question or humour with no
proposition about the world. Complete every world-claim check and flatten
the sentence claims into actual_factual_claims. Return only the required
JSON."""

COMPACT_REVIEWER_SYSTEM_PROMPT = """You are the independent final reviewer. Judge the incoming contribution,
bounded context, proposed reply, selected mode, recent account replies and
supplied evidence. The incoming contribution has priority. Return only the
required JSON. Approve only when every check passes.

Relevance and answer: the reply addresses the actual contribution.
Absence of a question or new fact, and quote-tweet format, are not defects.
Authorship questions require the actor. Questions about motives, values or
principles normally use opinion_or_principle, with an invited yes, no or
qualification first.

Schema discipline: determine direct-question classification from incoming
contribution, not proposed reply. Non-direct: use
direct_factual_question_present=false, requested_answer_type="none",
direct_answer_complete=false and direct_answer_text="". Direct: use the
narrowest permitted answer type and copy the complete first reply sentence
exactly into direct_answer_text. Assess each reply sentence once, verbatim and in order. List every
checkable clause, supported or not. Empty factual_claims means all five
specific world-claim flags false and purely_non_factual=true; otherwise
at least one specific flag true and purely_non_factual=false.
actual_factual_claims exactly concatenates sentence lists.

Factuality: world claims include actor or institutional states and actions,
causes or predictions, conditions, comparisons or outcomes, history, dates,
quantities, meaning and attribution, including embedded premises inside
normative wording. Each factual clause needs claim-specific evidence matching
actor, action or relationship, direction or polarity, date or period, and
quantity; thematic overlap is insufficient.

Integrity and safety: reject or revise fabricated or misattributed
quotations, original prose presented as Thatcher’s words, unsupported
allegations, identification from appearance, or material reversals of
actor, relationship, direction, date or quantity. Preserve safeguards for
spam, abuse, bad-faith bait, dangerous amplification, irrelevance and
demonstrated repetition.

Mode and quality: courtesy is suitable only when the contribution is
mainly social. A substantive argument, analogy, distinction, criticism or
political or moral observation normally deserves a specific
opinion_or_principle reply, or natural dry or wry light_humour when safe.
Harmlessness and topicality are insufficient. The reply must not fit
several unrelated posts and must do more than paraphrase and acknowledge.
Treat stock acknowledgements such as “well noted”, recycled sentence
shapes, or wording prominent in recent replies as correctable defects. Do
not force wit, especially for grief, distress or serious allegations.

Decision: use revise when one safe revision can fix the draft; identify the
particular idea to engage and require non-template wording. If removing an
unsupported claim leaves a safe specific response, revise rather than
reject. Reject only when no safe relevant reply should be written or one
revision cannot fix it. Approval requires British English and empty
revision_instructions."""

COMPACT_PROMPTS = MappingProxyType({
    "proposer": COMPACT_PROPOSER_SYSTEM_PROMPT,
    "reviewer": COMPACT_REVIEWER_SYSTEM_PROMPT,
    "no_reply_review": COMPACT_NO_REPLY_REVIEW_SYSTEM_PROMPT,
    "claim_auditor": COMPACT_CLAIM_AUDITOR_SYSTEM_PROMPT,
})
COMPACT_WORD_CAPS = MappingProxyType({
    "proposer": 500,
    "reviewer": 430,
    "no_reply_review": 150,
    "claim_auditor": 160,
})
POLICY_PRIORITY_ORDER = (
    "factual_and_safety_correctness",
    "direct_relevance",
    "specificity_and_added_value",
    "brevity",
)


def sha256_text(value: str) -> str:
    """Return the SHA-256 of exact UTF-8 prompt text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_word_count(value: str) -> int:
    """Count human-readable words consistently across prompt manifests."""
    return len(re.findall(r"[^\W_]+(?:[’'-][^\W_]+)*", value, flags=re.UNICODE))


def reply_strategy_sha256() -> str:
    """Hash the production module loaded by this process."""
    return hashlib.sha256(Path(reply_strategy.__file__).read_bytes()).hexdigest()


def production_prompt_functions() -> MappingProxyType[str, Any]:
    """Expose the captured production function objects read-only for auditing."""
    return _PRODUCTION_FUNCTIONS


def production_prompt_versions() -> MappingProxyType[str, Any]:
    """Expose the captured production prompt versions read-only for auditing."""
    return _PRODUCTION_VERSIONS


def verify_current_production_objects() -> None:
    """Reject any current-profile run after production prompt mutation."""
    if reply_strategy_sha256() != FROZEN_REPLY_STRATEGY_SHA256:
        raise RuntimeError("reply_strategy.py no longer matches the frozen production hash")
    for name, function in _PRODUCTION_FUNCTIONS.items():
        if getattr(reply_strategy, name) is not function:
            raise RuntimeError(f"production prompt function changed: {name}")
    for name, version in _PRODUCTION_VERSIONS.items():
        if getattr(reply_strategy, name) != version:
            raise RuntimeError(f"production prompt version changed: {name}")
    if reply_strategy.STRATEGY_VERSION != _PRODUCTION_STRATEGY_VERSION:
        raise RuntimeError("production strategy version changed")
    if reply_strategy.EVIDENCE_PROMPT_VERSION != _PRODUCTION_EVIDENCE_PROMPT_VERSION:
        raise RuntimeError("production evidence prompt version changed")


def _proposer_payload(
    context: dict[str, Any],
    recent_replies: list[str],
    resolved_quotation: dict[str, Any] | None,
    revision: dict[str, Any] | None,
) -> str:
    payload: dict[str, Any] = {
        "context_sections": {
            "incoming_contribution_to_answer": context["incoming_contribution"],
            "quoted_post_context_only": context["quoted_post"],
            "bounded_parent_thread_context_only": context["parent_thread"],
            "clarification_request_if_any": context["clarification_request"],
        },
        "target": {
            "target_id": context["target_id"],
            "thread_id": context["thread_id"],
            "lane": context["lane"],
        },
        "current_date": context["current_date"],
        "resolved_quotation_for_factual_use": resolved_quotation,
        "recent_account_replies_to_avoid_repeating": recent_replies[:20],
    }
    if revision is not None:
        payload["single_allowed_revision"] = revision
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _compact_proposer_prompts(
    context: dict[str, Any],
    recent_replies: list[str],
    *,
    resolved_quotation: dict[str, Any] | None,
    revision: dict[str, Any] | None,
) -> tuple[str, str]:
    return (
        COMPACT_PROPOSER_SYSTEM_PROMPT,
        _proposer_payload(context, recent_replies, resolved_quotation, revision),
    )


def _context_sections(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "incoming_contribution_to_answer": context["incoming_contribution"],
        "quoted_post_context_only": context["quoted_post"],
        "bounded_parent_thread_context_only": context["parent_thread"],
        "clarification_request_if_any": context["clarification_request"],
    }


def _compact_no_reply_review_prompts(
    context: dict[str, Any], proposer: dict[str, Any]
) -> tuple[str, str]:
    payload = {
        "context_sections": _context_sections(context),
        "proposer_interpretation_untrusted": proposer["interpretation"],
        "proposer_no_reply_reason_untrusted": proposer["no_reply_reason"],
        "recent_account_replies_for_repetition_check": list(
            _compact_reviewer_recent_replies.get()
        ),
    }
    return (
        COMPACT_NO_REPLY_REVIEW_SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _compact_claim_auditor_prompts(proposed_reply: str) -> tuple[str, str]:
    return (
        COMPACT_CLAIM_AUDITOR_SYSTEM_PROMPT,
        json.dumps(
            {"proposed_reply_to_audit": proposed_reply},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _compact_reviewer_prompts(
    context: dict[str, Any],
    proposer: dict[str, Any],
    evidence: list[dict[str, Any]],
    resolved_quotation: dict[str, Any] | None,
) -> tuple[str, str]:
    payload = {
        "context_sections": _context_sections(context),
        "mode": proposer["mode"],
        "tone": proposer["tone"],
        "proposed_reply": proposer["proposed_reply"],
        "proposer_direct_factual_question_present": proposer[
            "direct_factual_question_present"
        ],
        "proposer_requested_answer_type": proposer["requested_answer_type"],
        "proposer_direct_answer_text": proposer["direct_answer_text"],
        "proposer_listed_factual_claims_untrusted": proposer["factual_claims"],
        "exact_thatcher_wording_used": proposer["exact_thatcher_wording_used"],
        "exact_thatcher_wording": proposer["exact_thatcher_wording"],
        "evidence_package": evidence,
        "resolved_quotation_for_independent_check": resolved_quotation,
        "recent_account_replies_for_style_check": list(
            _compact_reviewer_recent_replies.get()
        ),
    }
    return (
        COMPACT_REVIEWER_SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


_COMPACT_FUNCTIONS = MappingProxyType({
    "_proposer_prompts": _compact_proposer_prompts,
    "_reviewer_prompts": _compact_reviewer_prompts,
    "_no_reply_review_prompts": _compact_no_reply_review_prompts,
    "_claim_auditor_prompts": _compact_claim_auditor_prompts,
})


@contextmanager
def activate_profile(
    profile_name: str,
    *,
    recent_account_replies: list[str] | tuple[str, ...] | None = None,
) -> Iterator[None]:
    """Activate one prompt profile and restore production state on every exit."""
    global _active_profile, _activation_depth
    if profile_name not in {"current", "compact"}:
        raise ValueError(f"unknown reply prompt profile: {profile_name}")
    recent = tuple(str(value) for value in (recent_account_replies or ()))
    recent = recent[-5:]
    with _ACTIVATION_LOCK:
        if _active_profile is not None and _active_profile != profile_name:
            raise RuntimeError(
                f"cannot activate {profile_name} inside active {_active_profile} profile"
            )
        outermost = _active_profile is None
        if outermost:
            verify_current_production_objects()
            _active_profile = profile_name
            if profile_name == "compact":
                for name, function in _COMPACT_FUNCTIONS.items():
                    setattr(reply_strategy, name, function)
                for name, version in COMPACT_PROMPT_VERSIONS.items():
                    setattr(reply_strategy, name, version)
        _activation_depth += 1
        token = _compact_reviewer_recent_replies.set(
            recent if profile_name == "compact" else ()
        )
        try:
            yield
        finally:
            _compact_reviewer_recent_replies.reset(token)
            _activation_depth -= 1
            if outermost:
                try:
                    if profile_name == "compact":
                        for name, function in _PRODUCTION_FUNCTIONS.items():
                            setattr(reply_strategy, name, function)
                        for name, version in _PRODUCTION_VERSIONS.items():
                            setattr(reply_strategy, name, version)
                finally:
                    _active_profile = None


def _current_system_prompts() -> dict[str, str]:
    context = {
        "target_id": "manifest-target",
        "thread_id": "manifest-thread",
        "lane": "mention",
        "incoming_contribution": "Manifest placeholder.",
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "current_date": "2026-08-10",
    }
    proposer = {
        "interpretation": "Manifest placeholder.",
        "no_reply_reason": "",
        "mode": "courtesy",
        "tone": "neutral",
        "proposed_reply": "Manifest placeholder.",
        "direct_factual_question_present": False,
        "requested_answer_type": "none",
        "direct_answer_text": "",
        "factual_claims": [],
        "exact_thatcher_wording_used": False,
        "exact_thatcher_wording": "",
    }
    proposer_system, _ = _PRODUCTION_FUNCTIONS["_proposer_prompts"](
        context, [], resolved_quotation=None, revision=None
    )
    reviewer_system, _ = _PRODUCTION_FUNCTIONS["_reviewer_prompts"](
        context, proposer, [], None
    )
    no_reply_system, _ = _PRODUCTION_FUNCTIONS["_no_reply_review_prompts"](
        context, proposer
    )
    auditor_system, _ = _PRODUCTION_FUNCTIONS["_claim_auditor_prompts"](
        "Manifest placeholder."
    )
    return {
        "proposer": proposer_system,
        "reviewer": reviewer_system,
        "no_reply_review": no_reply_system,
        "claim_auditor": auditor_system,
    }


def profile_manifest(profile_name: str) -> dict[str, Any]:
    """Build and self-hash one deterministic prompt contract manifest."""
    if profile_name not in {"current", "compact"}:
        raise ValueError(f"unknown reply prompt profile: {profile_name}")
    verify_current_production_objects()
    prompts = _current_system_prompts() if profile_name == "current" else dict(COMPACT_PROMPTS)
    if profile_name == "compact":
        exceeded = {
            name: (prompt_word_count(text), COMPACT_WORD_CAPS[name])
            for name, text in prompts.items()
            if prompt_word_count(text) > COMPACT_WORD_CAPS[name]
        }
        if exceeded:
            raise ValueError(f"compact prompt word cap exceeded: {exceeded}")
    versions = (
        dict(_PRODUCTION_VERSIONS)
        if profile_name == "current"
        else dict(COMPACT_PROMPT_VERSIONS)
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "profile_name": profile_name,
        "profile_version": (
            CURRENT_PROFILE_VERSION if profile_name == "current" else COMPACT_PROFILE_VERSION
        ),
        "prompts": {
            name: {
                "system_prompt": text,
                "word_count": prompt_word_count(text),
                "maximum_word_count": (
                    COMPACT_WORD_CAPS[name] if profile_name == "compact" else None
                ),
                "sha256": sha256_text(text),
            }
            for name, text in sorted(prompts.items())
        },
        "prompt_version_constants": versions,
        "evidence_prompt_version": _PRODUCTION_EVIDENCE_PROMPT_VERSION,
        "strategy_version": _PRODUCTION_STRATEGY_VERSION,
        "production_reply_strategy_sha256": reply_strategy_sha256(),
        "policy_priority_order": list(POLICY_PRIORITY_ORDER),
        "uses_exact_production_prompt_functions": profile_name == "current",
    }
    manifest["manifest_sha256"] = sha256_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return manifest


def profile_manifests() -> dict[str, dict[str, Any]]:
    """Return both prompt contracts in stable profile-name order."""
    return {name: profile_manifest(name) for name in ("current", "compact")}
