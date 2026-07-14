from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from reply_strategy import (
    DEFAULT_REPLY_STRATEGY,
    RetrievedEvidence,
    ReplyDecision,
    audit_digest,
    build_strategy_prompt_context,
    parse_decision_json,
    reply_is_repetitive,
    strategy_mode_guidance,
    retrieve_research_packets,
    validate_reply_decision,
    validate_reply_strategy_config,
)

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")


def decision(**overrides):
    value = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "A tidy theory. Reality may request amendments.",
        "no_reply_reason": "",
    }
    value.update(overrides)
    return value


def test_retrieval_uses_only_completed_packets_and_is_deterministic():
    first = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    second = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    assert first and [item.quote_id for item in first] == [item.quote_id for item in second]
    assert len(first) <= 5 and all(len(item.quote_id) == 64 for item in first)


def test_prompt_context_does_not_expose_internal_prose_or_unresolved_records():
    evidence = retrieve_research_packets("free enterprise and government", RESEARCH, maximum=3)
    payload = json.loads(build_strategy_prompt_context(evidence))
    assert len(payload) <= 3
    assert all(set(item) == {"quote_id", "verification_status", "research_confidence", "source_event", "date", "immediate_subject", "intended_argument", "broader_principle", "mechanism", "claimed_consequence", "verified_text"} for item in payload)


def test_historical_correction_precedes_humour_and_requires_high_confidence():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=2)
    qid = evidence[0].quote_id
    valid = validate_reply_decision(decision(
        mode="historical_correction", humour_tone="dry", evidence_confidence="high",
        retrieved_quote_ids=[qid], evidence_summary="Government sets conditions rather than creating wealth.",
        factual_claim_made=True, grounded=True,
        reply_text="Government may set the conditions for prosperity; it does not manufacture prosperity itself.",
    ), evidence, allowed_quote_ids={item.quote_id for item in evidence})
    assert valid["mode"] == "historical_correction"
    with pytest.raises(ValueError, match="requires high confidence"):
        validate_reply_decision({**valid, "evidence_confidence": "medium"}, evidence, allowed_quote_ids={item.quote_id for item in evidence})


def test_historical_correction_rejects_low_confidence_packet():
    low = RetrievedEvidence(
        "b" * 64, 1.0, "exact", "Low-confidence evidence.",
        {"verified_text": "Exact words.", "research_confidence": "low"},
    )
    with pytest.raises(ValueError, match="packet confidence"):
        validate_reply_decision(decision(
            mode="historical_correction", humour_tone="dry", evidence_confidence="high",
            retrieved_quote_ids=[low.quote_id], evidence_summary="Low-confidence evidence.",
            factual_claim_made=True, grounded=True,
            reply_text="That historical claim is not established by the available record.",
        ), [low], allowed_quote_ids={low.quote_id})


def test_humour_remains_available_without_forced_history():
    result = validate_reply_decision(decision(), [], allowed_quote_ids=set())
    assert result["mode"] == "wry_reply" and not result["grounded"]


def test_weak_factual_evidence_requires_no_reply():
    result = validate_reply_decision(decision(
        mode="no_reply", humour_tone="none", evidence_confidence="low",
        reply_text="", no_reply_reason="The historical claim cannot be grounded confidently.",
    ), [], allowed_quote_ids=set())
    assert result["mode"] == "no_reply"
    with pytest.raises(ValueError, match="factual claims require grounding"):
        validate_reply_decision(decision(factual_claim_made=True), [], allowed_quote_ids=set())


def test_paraphrase_cannot_be_presented_as_exact_quotation():
    evidence = retrieve_research_packets("women's liberation", RESEARCH, maximum=5)
    paraphrase = next(item for item in evidence if item.verification_status == "paraphrase")
    with pytest.raises(ValueError, match="quotation marks require"):
        validate_reply_decision(decision(
            mode="researched_principle", evidence_confidence="medium", grounded=True,
            retrieved_quote_ids=[paraphrase.quote_id], evidence_summary="A paraphrased principle.",
            factual_claim_made=True,
            reply_text='Thatcher said “Women succeed without collective action.”',
        ), evidence, allowed_quote_ids={item.quote_id for item in evidence})


def test_normalised_wording_cannot_be_presented_as_exact_quotation():
    normalised = RetrievedEvidence(
        "a" * 64, 1.0, "normalised", "Normalised wording.",
        {"verified_text": "A normalised sentence.", "research_confidence": "high"},
    )
    evidence = [normalised]
    with pytest.raises(ValueError, match="quotation marks require"):
        validate_reply_decision(decision(
            mode="researched_principle", evidence_confidence="medium", grounded=True,
            retrieved_quote_ids=[normalised.quote_id], evidence_summary="Normalised wording.",
            factual_claim_made=True,
            reply_text='Thatcher said “A normalised sentence.”',
        ), evidence, allowed_quote_ids={item.quote_id for item in evidence})


def test_no_hashtags_two_sentence_limit_and_repetition_controls():
    with pytest.raises(ValueError, match="hashtags"):
        validate_reply_decision(decision(reply_text="A point. #history"), [], allowed_quote_ids=set())
    with pytest.raises(ValueError, match="one or two sentences"):
        validate_reply_decision(decision(reply_text="One. Two. Three."), [], allowed_quote_ids=set())
    assert reply_is_repetitive("History has a habit of answering that.", [])
    with pytest.raises(ValueError, match="repeats"):
        validate_reply_decision(decision(), [], allowed_quote_ids=set(), recent_replies=[decision()["reply_text"]])


def test_reply_decision_is_string_compatible_and_carries_private_metadata():
    value = ReplyDecision("Dry, but accurate.", {"mode": "deadpan_reply"})
    assert value == "Dry, but accurate." and value.strategy_metadata["mode"] == "deadpan_reply"


def test_json_parser_accepts_fenced_object():
    assert parse_decision_json('```json\n{"mode":"no_reply"}\n```')["mode"] == "no_reply"


def test_digest_audit_reports_no_rows_for_supplied_digest():
    result = audit_digest(Path("/home/tonym/Dropbox/digest014.md"))
    assert result["auditable_reply_count"] == 0
    assert result["finding"] == "no auditable replies in digest"
    assert result["network_calls"] == 0


def test_enabled_strategy_requires_accuracy_and_completed_packets_only():
    config = dict(DEFAULT_REPLY_STRATEGY)
    config.update(enabled=True, accuracy_first=False, completed_packets_only=False, no_hashtags=False)
    errors = validate_reply_strategy_config(config)
    assert "reply_strategy.accuracy_first must remain true when enabled" in errors
    assert "reply_strategy.completed_packets_only must remain true when enabled" in errors
    assert "reply_strategy.no_hashtags must remain true when enabled" in errors


def test_disabled_modes_and_configured_confidence_are_enforced():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=2)
    qid = evidence[0].quote_id
    historical = decision(
        mode="historical_context", humour_tone="dry", evidence_confidence="medium",
        retrieved_quote_ids=[qid], evidence_summary="Context.", factual_claim_made=True,
        grounded=True, reply_text="Government can set conditions for prosperity without manufacturing it.",
    )
    with pytest.raises(ValueError, match="disabled by configuration"):
        validate_reply_decision(
            historical, evidence, allowed_quote_ids={qid},
            allowed_modes={"wry_reply", "no_reply"},
        )
    with pytest.raises(ValueError, match="configured grounded confidence"):
        validate_reply_decision(
            historical, evidence, allowed_quote_ids={qid}, minimum_grounded_confidence="high",
        )


def test_grounded_claim_requires_selected_evidence():
    with pytest.raises(ValueError, match="grounded replies require selected evidence"):
        validate_reply_decision(
            decision(factual_claim_made=True, grounded=True, evidence_confidence="medium"),
            [], allowed_quote_ids=set(),
        )


def test_factual_humour_obeys_configured_grounded_confidence():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=1)
    qid = evidence[0].quote_id
    with pytest.raises(ValueError, match="configured grounded confidence"):
        validate_reply_decision(
            decision(
                evidence_confidence="low", retrieved_quote_ids=[qid],
                evidence_summary="A factual historical point.", factual_claim_made=True,
                grounded=True,
            ),
            evidence, allowed_quote_ids={qid}, minimum_grounded_confidence="medium",
        )


def test_historical_modes_require_factual_claim_and_evidence_summary():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=1)
    qid = evidence[0].quote_id
    base = decision(
        mode="historical_context", humour_tone="dry", evidence_confidence="medium",
        retrieved_quote_ids=[qid], grounded=True,
        reply_text="Government can set conditions for prosperity without manufacturing it.",
    )
    with pytest.raises(ValueError, match="historical modes must identify a factual claim"):
        validate_reply_decision(base, evidence, allowed_quote_ids={qid})
    with pytest.raises(ValueError, match="grounded replies require an evidence summary"):
        validate_reply_decision(
            {**base, "factual_claim_made": True}, evidence, allowed_quote_ids={qid},
        )


def test_no_reply_metadata_is_internally_consistent():
    with pytest.raises(ValueError, match="no_reply metadata must be empty"):
        validate_reply_decision(
            decision(
                mode="no_reply", humour_tone="dry", evidence_confidence="medium",
                grounded=True, retrieved_quote_ids=[], reply_text="", no_reply_reason="Weak evidence.",
            ),
            [], allowed_quote_ids=set(),
        )


def test_configured_humour_tones_and_emoji_are_enforced():
    with pytest.raises(ValueError, match="humour tone is disabled"):
        validate_reply_decision(
            decision(humour_tone="playful"), [], allowed_quote_ids=set(),
            allowed_humour_tones={"dry", "wry"},
        )


def test_digest_table_parser_preserves_escaped_pipes(tmp_path: Path):
    digest = tmp_path / "digest.md"
    digest.write_text(
        "## Mention replies\n"
        "| time | mention_id | incoming_text | reply |\n"
        "| --- | --- | --- | --- |\n"
        "| now | 1 | A \\| B | A reply. |\n",
        encoding="utf-8",
    )
    result = audit_digest(digest)
    assert result["auditable_reply_count"] == 1
    assert result["items"][0]["cells"] == ["now", "1", "A | B", "A reply."]


def test_mode_guidance_defines_accuracy_first_classification_hierarchy():
    guidance = strategy_mode_guidance()
    assert guidance.index("materially false") < guidance.index("historical_context")
    assert guidance.index("historical_context") < guidance.index("researched_principle")
    assert guidance.index("researched_principle") < guidance.index("humour mode")
    assert "Do not force history" in guidance
    assert "Do not force humour" in guidance


def test_audit_json_stdout_is_machine_readable():
    result = subprocess.run(
        [
            sys.executable, "mrsMThatcher2.py", "audit-replies",
            "--digest", "/home/tonym/Dropbox/digest014.md",
            "--research-run", str(RESEARCH), "--json",
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["auditable_reply_count"] == 0
    assert payload["network_calls"] == 0
    with pytest.raises(ValueError, match="emoji"):
        validate_reply_decision(
            decision(reply_text="A tidy theory. Reality may disagree. 🙂"),
            [], allowed_quote_ids=set(),
        )
