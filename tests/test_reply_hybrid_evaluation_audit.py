"""Synthetic tests for the no-cost fresh reply evaluation audit."""

from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import inspect
import io
import json
import sys
from pathlib import Path

import pytest

from historical_context_source_roles import public_sources as audited_public_sources


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "build_reply_hybrid_evaluation_audit.py"
SPEC = importlib.util.spec_from_file_location("reply_hybrid_evaluation_audit", TOOL_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _sha256_text(text: str) -> str:
    """Return the SHA-256 digest of synthetic UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reason_codes(value: object) -> set[str]:
    """Flatten a returned audit result into stable reason-code text."""
    if isinstance(value, dict):
        parts = [str(key) for key in value]
        parts.extend(str(item) for item in value.values())
        return set(" ".join(parts).replace("-", "_").split())
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.extend(str(key) for key in item)
                parts.extend(str(member) for member in item.values())
            else:
                parts.append(str(item))
        return set(" ".join(parts).replace("-", "_").split())
    return set(str(value).replace("-", "_").split())


def _synthetic_candidate(**changes: object) -> dict[str, object]:
    """Build one invented candidate without production contribution text."""
    candidate: dict[str, object] = {
        "candidate_id": "candidate-synthetic-fresh",
        "target_id": "target-synthetic-fresh",
        "thread_id": "thread-synthetic-fresh",
        "lane": "mention",
        "candidate_timestamp": "2031-02-03T04:05:06Z",
        "incoming_contribution": "Would the fictional archive rule apply here?",
        "normalised_incoming_fingerprint": _sha256_text(
            audit.normalise_incoming("Would the fictional archive rule apply here?")
        ),
        "full_context_fingerprint": "f" * 64,
    }
    candidate.update(changes)
    return candidate


def _synthetic_profile() -> tuple[str, dict[str, object]]:
    """Build executable-looking but never executed synthetic profile source."""
    prompts = {
        "proposer": "Synthetic proposer contract.",
        "reviewer": "Synthetic reviewer contract.",
        "evidence": "Synthetic evidence contract.",
        "no_reply_reviewer": "Synthetic no-reply contract.",
        "claim_auditor": "Synthetic claim-auditor contract.",
    }
    constants = {
        "STRATEGY_VERSION": "synthetic-strategy-v1",
        "PROPOSER_PROMPT_VERSION": "synthetic-proposer-v1",
        "REVIEWER_PROMPT_VERSION": "synthetic-reviewer-v1",
        "EVIDENCE_PROMPT_VERSION": "synthetic-evidence-v1",
        "NO_REPLY_REVIEW_PROMPT_VERSION": "synthetic-no-reply-v1",
        "CLAIM_AUDITOR_PROMPT_VERSION": "synthetic-claim-auditor-v1",
        "VALIDATION_RETRY_PROTOCOL_VERSION": "synthetic-retry-v1",
    }
    function_names = {
        "proposer": "_proposer_prompts",
        "reviewer": "_reviewer_prompts",
        "evidence": "_evidence_prompts",
        "no_reply_reviewer": "_no_reply_review_prompts",
        "claim_auditor": "_claim_auditor_prompts",
    }
    source_lines = [f"{name} = {value!r}" for name, value in constants.items()]
    for role, function_name in function_names.items():
        source_lines.extend(
            [
                "",
                f"def {function_name}(context=None):",
                f"    system = {prompts[role]!r}",
                "    return system, ''",
            ]
        )
    expected: dict[str, object] = {
        "commit": "0" * 40,
        "constants": constants,
        "prompt_sha256": {
            role: _sha256_text(prompt) for role, prompt in prompts.items()
        },
    }
    return "\n".join(source_lines) + "\n", expected


def _synthetic_review_row(**changes: object) -> dict[str, object]:
    """Build one invented, context-only row for blinded manual review."""
    row: dict[str, object] = {
        "candidate_id": "candidate-synthetic-review",
        "proposed_primary_stratum": "factual_or_historical_question",
        "context_dependency_flags": ["demonstrative_reference"],
        "context_audit_record_sha256": "d" * 64,
        "replay_context": {
            "lane": "mention",
            "incoming_contribution": "Would the fictional archive rule apply here?",
            "quoted_post": {
                "post_id": "quoted-synthetic",
                "author_role": "user",
                "text": "Synthetic quoted material.",
            },
            "parent_thread": [
                {
                    "post_id": "parent-synthetic",
                    "author_role": "user",
                    "text": "Synthetic parent material.",
                }
            ],
            "clarification_request": None,
        },
    }
    row.update(changes)
    return row


def test_verify_profile_source_accepts_exact_identity_and_hashes() -> None:
    """Exact synthetic versions and prompt hashes must verify."""
    source, expected = _synthetic_profile()

    result = audit.verify_profile_source(source, expected)

    assert result["verification"] == "pass"
    assert result["constants"] == expected["constants"]
    assert result["prompt_sha256"] == expected["prompt_sha256"]


def test_verify_pinned_profile_identities_from_exact_git_objects() -> None:
    """Pinned production profile identities must verify from their exact commits."""
    expected_profiles = {
        "hardened_current": {
            "commit": "f07957e8b2388d8258821cb21b25f2a43681cbbd",
            "constants": {
                "STRATEGY_VERSION": "ai-first-reply-v3",
                "PROPOSER_PROMPT_VERSION": "ai-first-proposer-v15",
                "REVIEWER_PROMPT_VERSION": "independent-reply-reviewer-v13",
                "VALIDATION_RETRY_PROTOCOL_VERSION": "validator-guided-retry-v1",
                "EVIDENCE_PROMPT_VERSION": "claim-evidence-entailment-v6",
                "NO_REPLY_REVIEW_PROMPT_VERSION": "independent-no-reply-review-v1",
                "CLAIM_AUDITOR_PROMPT_VERSION": "claim-inventory-auditor-v5",
            },
            "prompt_sha256": {
                "proposer": (
                    "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72"
                ),
                "reviewer": (
                    "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e"
                ),
                "evidence": (
                    "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b"
                ),
                "no_reply_reviewer": (
                    "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d"
                ),
                "claim_auditor": (
                    "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03"
                ),
            },
        },
        "conservative_hybrid": {
            "commit": "0b7cd1da11d7c9ff5b3051c3d6fc4a70ae45176d",
            "constants": {
                "STRATEGY_VERSION": "ai-first-reply-v3",
                "PROPOSER_PROMPT_VERSION": "ai-first-proposer-v16",
                "REVIEWER_PROMPT_VERSION": "independent-reply-reviewer-v14",
                "VALIDATION_RETRY_PROTOCOL_VERSION": "validator-guided-retry-v1",
                "EVIDENCE_PROMPT_VERSION": "claim-evidence-entailment-v6",
                "NO_REPLY_REVIEW_PROMPT_VERSION": "independent-no-reply-review-v1",
                "CLAIM_AUDITOR_PROMPT_VERSION": "claim-inventory-auditor-v5",
            },
            "prompt_sha256": {
                "proposer": (
                    "7f69a8bb30296247a049a34a27625885cd5f30813f0eda92f99beb85fbf9cb10"
                ),
                "reviewer": (
                    "b8e632c41bfba03f792a6a4b93bd16c2fd38a2410429a80268cbe0c93c43127d"
                ),
                "evidence": (
                    "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b"
                ),
                "no_reply_reviewer": (
                    "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d"
                ),
                "claim_auditor": (
                    "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03"
                ),
            },
        },
    }

    assert audit.PROFILE_EXPECTATIONS == expected_profiles
    result = audit.verify_profile_identities(ROOT)

    assert result["shared_frozen_prompt_hashes_identical"] is True
    assert result["profile_code_imported"] is False
    assert result["profile_code_executed"] is False
    for profile_name, expected in expected_profiles.items():
        observed = result["profiles"][profile_name]
        assert observed["commit"] == expected["commit"]
        assert observed["constants"] == expected["constants"]
        assert observed["prompt_sha256"] == expected["prompt_sha256"]
        assert observed["verification"] == "pass"
    for role, digest in {
        "evidence": "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b",
        "no_reply_reviewer": (
            "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d"
        ),
        "claim_auditor": (
            "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03"
        ),
    }.items():
        assert result["profiles"]["hardened_current"]["prompt_sha256"][role] == digest
        assert result["profiles"]["conservative_hybrid"]["prompt_sha256"][role] == digest


def test_verify_pinned_historical_tool_identities_from_git_objects() -> None:
    """Every historical read-only tool must match its pinned source hash."""
    expected = {
        "reply_history_reconstruction": {
            "commit": "bdb6a5b18468bbda02f2c908f8a7699601ee5d18",
            "source_sha256": (
                "2417500c01df5d1338a1280a7f168eb3df690995c6eca3cbfd653e28cb6197ce"
            ),
        },
        "evaluation_pool_tooling": {
            "commit": "6931ecd294f8427291d8a31ca2cb5d0a8ad3ca7c",
            "source_sha256": (
                "12d60d1f3d5f2108510eb87c8f560d4a2f5ebe9832a546971da01bf68d3b61b0"
            ),
        },
        "frozen_replay_pack_tooling": {
            "commit": "df7f53bfcb9ccd6b38e8b8631bd1d4a1eca1c5e6",
            "source_sha256": (
                "843982170e729e006ba0c10325885bf12e57d6c9f8f73c573b60837e706e3c4a"
            ),
        },
        "completed_calibration_holdout_runner": {
            "commit": "cc404c3bd45b97d7664ca4fec02cf1f4154a6a1a",
            "source_sha256": (
                "479bc170c5cbb4db2ea6efbcc4fb21bb90688bce8bb514b51f2c02153f9c4581"
            ),
        },
    }

    result = audit.verify_historical_tool_identities(ROOT)

    assert set(result) == set(expected)
    for source_name, identity in expected.items():
        assert result[source_name]["commit"] == identity["commit"]
        assert result[source_name]["source_sha256"] == identity["source_sha256"]
        assert result[source_name]["verification"] == "pass"


@pytest.mark.parametrize(
    "changed_field,wrong_value",
    [
        ("constant", "synthetic-proposer-wrong"),
        ("prompt_hash", "0" * 64),
    ],
)
def test_verify_profile_source_refuses_wrong_identity_or_hash(
    changed_field: str,
    wrong_value: str,
) -> None:
    """Any profile version or prompt-hash mismatch must fail closed."""
    source, expected = _synthetic_profile()
    if changed_field == "constant":
        expected["constants"]["PROPOSER_PROMPT_VERSION"] = wrong_value
    else:
        expected["prompt_sha256"]["reviewer"] = wrong_value

    with pytest.raises(Exception):
        audit.verify_profile_source(source, expected)


def test_verify_profile_identities_refuses_wrong_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Git object that does not resolve to the pinned commit must be refused."""

    def wrong_commit(
        repository: Path, *arguments: str, text: bool = True
    ) -> str | bytes:
        """Return one invented incorrect commit without reading a repository."""
        del repository, text
        assert arguments[0] == "rev-parse"
        return "1" * 40 + "\n"

    monkeypatch.setattr(audit, "_git", wrong_commit)

    with pytest.raises(Exception, match="does not resolve exactly"):
        audit.verify_profile_identities(Path("/synthetic/repository"))


@pytest.mark.parametrize("failure_mode", ["ambiguous_commit", "source_hash_mismatch"])
def test_verify_historical_tool_identities_refuses_ambiguous_or_wrong_identity(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    """Historical sources must resolve uniquely and match their pinned byte hashes."""

    def invented_git(
        repository: Path, *arguments: str, text: bool = True
    ) -> str | bytes:
        """Return only invented Git values for one fail-closed identity check."""
        del repository
        if arguments[0] == "rev-parse" and arguments[1].endswith("^{commit}"):
            commit = arguments[1].removesuffix("^{commit}")
            if failure_mode == "ambiguous_commit":
                return f"{commit}\n{'f' * 40}\n"
            return commit + "\n"
        if arguments[0] == "show":
            assert text is False
            return b"invented historical source with the wrong digest\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(audit, "_git", invented_git)

    message = (
        "does not resolve exactly"
        if failure_mode == "ambiguous_commit"
        else "historical source hash mismatch"
    )
    with pytest.raises(Exception, match=message):
        audit.verify_historical_tool_identities(Path("/synthetic/repository"))


def test_normalisation_similarity_and_rendering_are_deterministic() -> None:
    """Repeated synthetic inputs must produce byte-identical outputs."""
    text = "  Synthetic_CASE—Alpha!  "
    row = _synthetic_review_row()

    first = (
        audit.normalise_incoming(text),
        audit.near_duplicate_ratio(text, "synthetic case alpha"),
        audit.render_manual_context_review([row]),
        audit.render_manual_clearance_csv([row], "a" * 64),
    )
    second = (
        audit.normalise_incoming(text),
        audit.near_duplicate_ratio(text, "synthetic case alpha"),
        audit.render_manual_context_review([row]),
        audit.render_manual_clearance_csv([row], "a" * 64),
    )

    assert first == second
    assert first[0] == "synthetic case alpha"
    assert first[1] == 1.0


def test_freshness_boundary_is_strict() -> None:
    """Only timestamps strictly later than the cutoff are fresh."""
    cutoff = "2030-01-02T03:04:05Z"

    assert not audit.strictly_after("2030-01-02T03:04:04Z", cutoff)
    assert not audit.strictly_after(cutoff, cutoff)
    assert audit.strictly_after("2030-01-02T03:04:06Z", cutoff)


def test_candidate_contamination_excludes_development_id_and_thread() -> None:
    """Development targets and materially dependent threads must be excluded."""
    development = [
        {
            "candidate_id": "candidate-development",
            "target_id": "target-development",
            "thread_id": "thread-development",
            "normalised_incoming_fingerprint": "1" * 64,
            "normalised_incoming": "an unrelated synthetic contribution",
            "full_context_fingerprint": "2" * 64,
        }
    ]
    candidate = _synthetic_candidate(
        target_id="target-development",
        thread_id="thread-development",
        context={
            "lane": "mention",
            "incoming_contribution": "A separate invented contribution.",
            "parent_thread": [
                {"author_role": "user", "text": "Invented prior context."}
            ],
        },
    )

    reasons = _reason_codes(
        audit.candidate_contamination(
            candidate,
            development,
            {"thread-development"},
            [],
        )
    )

    assert "development_target_id" in reasons
    assert "development_thread_context_leakage" in reasons


def test_candidate_contamination_excludes_exact_development_candidate_id() -> None:
    """Candidate identity alone must exclude despite wholly distinct other fields."""
    development = [
        {
            "candidate_id": "candidate-shared-synthetic-identity",
            "target_id": "target-old-synthetic",
            "thread_id": "thread-old-synthetic",
            "incoming_contribution": "An invented historical contribution about amber.",
        }
    ]
    candidate = _synthetic_candidate(
        candidate_id="candidate-shared-synthetic-identity",
        target_id="target-new-synthetic",
        thread_id="thread-new-synthetic",
        incoming_contribution="A distinct fresh contribution about violet geometry.",
    )

    reasons = audit.candidate_contamination(candidate, development, set(), [])

    assert [reason for reason in reasons if reason["reason"] == "development_candidate_id"] == [
        {
            "reason": "development_candidate_id",
            "development_candidate_id": "candidate-shared-synthetic-identity",
        }
    ]
    assert "development_target_id" not in _reason_codes(reasons)
    assert "development_thread_context_leakage" not in _reason_codes(reasons)


def test_candidate_contamination_excludes_exact_and_near_duplicates() -> None:
    """Exact and threshold-reaching lexical duplicates must identify development cases."""
    incoming = "A wholly synthetic principle applies to this example."
    development = [
        {
            "candidate_id": "candidate-development-exact",
            "target_id": "target-development-exact",
            "thread_id": None,
            "incoming_contribution": incoming,
            "normalised_incoming": audit.normalise_incoming(incoming),
            "normalised_incoming_fingerprint": _sha256_text(
                audit.normalise_incoming(incoming)
            ),
            "full_context_fingerprint": "3" * 64,
        },
        {
            "candidate_id": "candidate-development-near",
            "target_id": "target-development-near",
            "thread_id": None,
            "incoming_contribution": (
                "A wholly synthetic principle applies to this example today."
            ),
            "normalised_incoming": audit.normalise_incoming(
                "A wholly synthetic principle applies to this example today."
            ),
            "normalised_incoming_fingerprint": "4" * 64,
            "full_context_fingerprint": "5" * 64,
        },
    ]

    exact = _reason_codes(
        audit.candidate_contamination(
            _synthetic_candidate(incoming_contribution=incoming),
            development,
            set(),
            set(),
        )
    )
    near = _reason_codes(
        audit.candidate_contamination(
            _synthetic_candidate(
                incoming_contribution=(
                    "A wholly synthetic principle applies to this example this day."
                )
            ),
            development,
            set(),
            set(),
        )
    )

    assert "exact_normalised_incoming_duplicate" in exact
    assert "near_duplicate_incoming" in near
    assert audit.near_duplicate_ratio(
        "A wholly synthetic principle applies to this example this day.",
        "A wholly synthetic principle applies to this example today.",
    ) >= 0.92


def test_candidate_contamination_excludes_exact_context_payload() -> None:
    """A development context fingerprint must contaminate a fresh target."""
    context = {
        "lane": "mention",
        "incoming_contribution": "Can this invented point be explained?",
        "quoted_post": None,
        "parent_thread": [
            {"author_role": "user", "text": "Invented context payload."}
        ],
        "clarification_request": None,
    }
    candidate = _synthetic_candidate(context=context)

    reasons = _reason_codes(
        audit.candidate_contamination(
            candidate,
            [],
            set(),
            [{"candidate_id": "candidate-development", "context": context}],
        )
    )

    assert "exact_context_payload_duplicate" in reasons


def test_near_context_duplicate_reports_candidate_dependent_evidence() -> None:
    """Near-context exclusions must identify the old case and similarity evidence."""
    fresh_context = {
        "lane": "mention",
        "incoming_contribution": (
            "Could the invented archive principle apply to this elaborate example today?"
        ),
        "quoted_post": None,
        "parent_thread": [
            {
                "author_role": "user",
                "text": (
                    "The synthetic ledger describes seven fictional boxes in careful detail."
                ),
            }
        ],
        "clarification_request": None,
    }
    development_context = {
        **fresh_context,
        "incoming_contribution": (
            "Could the invented archive principle apply to this elaborate example now?"
        ),
    }

    reasons = audit.candidate_contamination(
        _synthetic_candidate(context=fresh_context),
        [],
        set(),
        [
            {
                "candidate_id": "candidate-development-context-near",
                "context": development_context,
            }
        ],
    )
    matches = [
        reason for reason in reasons if reason["reason"] == "near_duplicate_context_payload"
    ]

    assert len(matches) == 1
    assert matches[0]["development_candidate_id"] == "candidate-development-context-near"
    assert matches[0]["similarity"] >= audit.NEAR_DUPLICATE_THRESHOLD
    assert matches[0]["threshold"] == audit.NEAR_DUPLICATE_THRESHOLD
    assert matches[0]["algorithm"] == audit.NEAR_DUPLICATE_VERSION
    assert matches[0]["candidate_dependent_context_sha256"] != ""
    assert matches[0]["development_candidate_dependent_context_sha256"] != ""
    assert "shared recent-reply history cannot independently trigger exclusion" in matches[0][
        "near_similarity_scope"
    ]


def test_recent_reply_legacy_text_normalises_but_cannot_trigger_near_exclusion() -> None:
    """Legacy reply_text equals fresh text yet shared recent replies are not leakage."""
    shared_reply = "  Synthetic&nbsp;account reply https://invalid.example/item  "
    fresh_context = {
        "lane": "mention",
        "incoming_contribution": "A fresh invented question about indigo triangles?",
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "recent_account_replies": [{"text": shared_reply}],
    }
    development_context = {
        "lane": "mention",
        "incoming_contribution": "An old unrelated statement about copper circles.",
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "recent_account_replies": [{"reply_text": shared_reply}],
    }

    fresh_payload = audit.context_leakage_payload(fresh_context)
    development_payload = audit.context_leakage_payload(development_context)
    reasons = audit.candidate_contamination(
        _synthetic_candidate(context=fresh_context),
        [],
        set(),
        [{"candidate_id": "candidate-development-shared-recent", "context": development_context}],
    )

    assert fresh_payload["recent_account_replies"] == ["Synthetic account reply"]
    assert (
        fresh_payload["recent_account_replies"]
        == development_payload["recent_account_replies"]
    )
    assert "near_duplicate_context_payload" not in _reason_codes(reasons)
    assert "exact_context_payload_duplicate" not in _reason_codes(reasons)


def test_conflicting_source_records_fail_closed() -> None:
    """Conflicting records at one immutable source position must not be discarded."""
    records = [
        {
            "target_id": "target-conflict",
            "stream_sequence": 7,
            "source_snapshot": "snapshot-synthetic",
            "incoming_contribution": "Synthetic version alpha.",
            "source_record_fingerprint": "a" * 64,
        },
        {
            "target_id": "target-conflict",
            "stream_sequence": 7,
            "source_snapshot": "snapshot-synthetic",
            "incoming_contribution": "Synthetic version beta.",
            "source_record_fingerprint": "b" * 64,
        },
    ]

    with pytest.raises(Exception):
        audit.resolve_cache_records(records)


def test_context_temporal_audit_rejects_future_parent_and_recent_reply() -> None:
    """Parent and recent-account context must be strictly prior to the candidate."""
    records = [
        {
            "kind": "parent_thread",
            "post_id": "parent-before",
            "created_at": "2030-04-05T06:07:07Z",
        },
        {
            "kind": "parent_thread",
            "post_id": "parent-future",
            "created_at": "2030-04-05T06:07:09Z",
        },
        {
            "kind": "recent_account_reply",
            "post_id": "recent-before",
            "created_at": "2030-04-05T06:07:06Z",
        },
        {
            "kind": "recent_account_reply",
            "post_id": "recent-future",
            "created_at": "2030-04-05T06:07:10Z",
        },
    ]

    reasons = _reason_codes(
        audit.context_temporal_violations(records, "2030-04-05T06:07:08Z")
    )

    assert "context_postdates_candidate" in reasons
    assert "recent_reply_not_strictly_prior" in reasons


def test_context_dependency_flags_cover_missing_quote_and_elliptical_references() -> None:
    """Review flags must cover the preregistered context-dependent patterns."""
    context = {
        "lane": "quote_tweet",
        "incoming_contribution": "What did he mean by this and the quote; what is the source?",
        "quoted_post": {"id": "quoted-synthetic", "text": ""},
        "parent_thread": [],
    }

    flags = set(audit.context_dependency_flags(context))

    assert "missing_quoted_post_text" in flags
    assert "missing_or_empty_bounded_parent_context" in flags
    assert "unresolved_third_person_pronoun" in flags
    assert "demonstrative_reference" in flags
    assert "what_did_mean_question" in flags
    assert "source_or_attribution_question" in flags
    assert "quote_or_above_reference" in flags


@pytest.mark.parametrize(
    "incoming",
    [
        "@synthetic_account",
        "@synthetic_one @synthetic_two !!!",
        "@synthetic_account #?! £%^&*",
        "... !!! ???",
    ],
)
def test_context_dependency_flags_cover_mention_or_symbol_only_text(
    incoming: str,
) -> None:
    """Handle-only and symbol-only contributions must require context review."""
    context = {
        "lane": "mention",
        "incoming_contribution": incoming,
        "quoted_post": None,
        "parent_thread": [],
    }

    flags = audit.context_dependency_flags(context)

    assert "mention_or_symbol_only_contribution" in flags


def test_context_dependency_flags_do_not_hide_substantive_mention_text() -> None:
    """A handle plus invented lexical content is not mention-only."""
    context = {
        "lane": "mention",
        "incoming_contribution": "@synthetic_account Azure lanterns remain visible.",
        "quoted_post": None,
        "parent_thread": [],
    }

    assert (
        "mention_or_symbol_only_contribution"
        not in audit.context_dependency_flags(context)
    )


def test_manual_review_hides_historical_and_profile_information() -> None:
    """The manual context pack must expose context but hide outcome and profile data."""
    row = _synthetic_review_row()

    rendered = audit.render_manual_context_review([row])

    assert "Would the fictional archive rule apply here?" in rendered
    assert "Synthetic quoted material." in rendered
    assert "Synthetic parent material." in rendered
    for forbidden in (
        "A hidden historical answer.",
        "A hidden current answer.",
        "A hidden hybrid answer.",
        "historical_outcome",
        "locked_old_score",
        "cost_usd",
    ):
        assert forbidden not in rendered

    contaminated = dict(row)
    contaminated["historical_outcome"] = "synthetic-posted"
    with pytest.raises(Exception, match="forbidden field"):
        audit.render_manual_context_review([contaminated])


def test_manual_review_includes_context_metadata_and_rejects_blinded_fields() -> None:
    """Manual review exposes required context while refusing profile/output history."""
    row = _synthetic_review_row(
        recent_account_replies=[
            {
                "post_id": "recent-synthetic",
                "created_at": "2031-02-03T04:05:00Z",
                "text": "Synthetic prior account reply.",
            }
        ],
        resolved_quotation={
            "source_kind": "synthetic_archive",
            "quotation_sha256": "e" * 64,
        },
    )

    rendered = audit.render_manual_context_review([row])

    assert "Recent account replies (production newest-first bound):" in rendered
    assert "Synthetic prior account reply." in rendered
    assert "Resolved quotation metadata:" in rendered
    assert '"source_kind": "synthetic_archive"' in rendered
    assert '"quotation_sha256": "' + "e" * 64 + '"' in rendered
    for forbidden_key, sentinel in (
        ("historical_public_reply", "SENTINEL-HISTORICAL-ANSWER"),
        ("profile", "SENTINEL-PROFILE-LABEL"),
        ("response", "SENTINEL-GENERATED-OUTPUT"),
    ):
        contaminated = dict(row)
        contaminated[forbidden_key] = sentinel
        with pytest.raises(Exception, match="forbidden field"):
            audit.render_manual_context_review([contaminated])
        assert sentinel not in rendered


def test_manual_clearance_decisions_are_blank() -> None:
    """Generated manual-clearance rows must never manufacture approval."""
    rows = [
        _synthetic_review_row(
            proposed_manual_decision="ready",
            manual_decision="approved",
        )
    ]

    rendered = audit.render_manual_clearance_csv(rows, "c" * 64)
    parsed = list(csv.DictReader(io.StringIO(rendered)))

    assert len(parsed) == 1
    assert parsed[0]["manual_decision"] == ""
    assert parsed[0]["reviewer_note"] == ""
    assert parsed[0]["context_audit_sha256"] == "c" * 64


def test_read_jsonl_strict_rejects_malformed_input(tmp_path: Path) -> None:
    """Malformed JSONL must fail closed with its source unavailable for use."""
    source = tmp_path / "malformed.jsonl"
    source.write_text('{"synthetic": true}\nnot-json\n', encoding="utf-8")

    with pytest.raises(Exception):
        audit.read_jsonl_strict(source)


@pytest.mark.parametrize(
    "record",
    [
        {"referenced_tweets": "not-a-list"},
        {"referenced_tweets": ["not-an-object"]},
        {"referenced_tweets": [{"id": "123"}]},
        {"referenced_tweets": [{"type": "quoted", "id": "not-numeric"}]},
    ],
)
def test_malformed_cached_references_fail_closed(record: dict[str, object]) -> None:
    """Malformed cached reference shapes must never enter reconstructed context."""
    with pytest.raises(Exception, match="cached reference|referenced_tweets"):
        audit._references(record, "quoted")


@pytest.mark.parametrize(
    "value",
    ["", "not-a-source-timestamp", "2031-02-30T04:05:06Z"],
)
def test_malformed_retained_source_timestamps_fail_closed(value: str) -> None:
    """Malformed retained timestamps must be excluded rather than guessed."""
    with pytest.raises(Exception, match="invalid retained source timestamp"):
        audit._parse_source_timestamp(value)


def test_safe_source_paths_refuse_escape_symlink_and_mutable_live_input(
    tmp_path: Path,
) -> None:
    """Only immutable snapshot sources contained by the allowed root are safe."""
    allowed = tmp_path / "source-root"
    snapshot = allowed / ".zfs" / "snapshot" / "snapshot-synthetic" / "data"
    snapshot.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed / ".zfs" / "snapshot" / "snapshot-synthetic" / "escape"
    link.symlink_to(outside, target_is_directory=True)

    assert audit.ensure_safe_source_path(snapshot, allowed) == snapshot.resolve()
    for unsafe in (outside, link, allowed / ".." / "outside"):
        with pytest.raises(Exception):
            audit.ensure_safe_source_path(unsafe, allowed)
    with pytest.raises(Exception, match="mutable live production"):
        audit.ensure_safe_source_path(
            Path("/disks/disk1/etc/mrsMThatcher"), Path("/disks/disk1")
        )


def test_prepare_output_directory_refuses_nonempty_directory(tmp_path: Path) -> None:
    """Existing experiment contents must never be overwritten."""
    allowed = tmp_path / "outputs"
    allowed.mkdir()
    output = allowed / "new-run"

    prepared = audit.prepare_output_directory(output, allowed)
    assert prepared == output.resolve()
    (output / "existing.txt").write_text("synthetic", encoding="utf-8")

    with pytest.raises(Exception):
        audit.prepare_output_directory(output, allowed)


def test_prepare_output_directory_refuses_existing_empty_path_outside_root(
    tmp_path: Path,
) -> None:
    """An existing empty directory outside the output root remains forbidden."""
    allowed = tmp_path / "allowed-outputs"
    outside = tmp_path / "outside-empty"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(Exception, match="output must be below"):
        audit.prepare_output_directory(outside, allowed)


def test_provider_credential_exported_empty_is_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exported empty credential is present and must fail the absence proof."""
    for name in audit.PROVIDER_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert audit.provider_credentials_are_absent()

    monkeypatch.setenv("XAI_API_KEY", "")

    assert not audit.provider_credentials_are_absent()


def test_read_only_command_refuses_non_allow_listed_executable() -> None:
    """The command wrapper must reject a network executable before invocation."""
    with pytest.raises(Exception, match="not read-only allow-listed"):
        audit._run_read_only_command("curl", ["https://invalid.example/"])


def test_subprocess_calls_are_centralised_behind_exact_read_only_allowlist() -> None:
    """Every subprocess call must stay inside the exact git/ZFS metadata wrapper."""
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    owners: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "run"
        ):
            continue
        owner = parents.get(node)
        while owner is not None and not isinstance(
            owner, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            owner = parents.get(owner)
        assert isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        owners.append(owner.name)

    allowlist_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "READ_ONLY_SUBPROCESS_COMMANDS"
            for target in node.targets
        )
    )
    assert isinstance(allowlist_assignment.value, ast.Call)
    assert isinstance(allowlist_assignment.value.func, ast.Name)
    assert allowlist_assignment.value.func.id == "frozenset"
    assert len(allowlist_assignment.value.args) == 1
    literal_allowlist = ast.literal_eval(allowlist_assignment.value.args[0])

    assert owners == ["_run_read_only_command"]
    assert literal_allowlist == {"git", "zfs", "zpool"}
    assert audit.READ_ONLY_SUBPROCESS_COMMANDS == frozenset({"git", "zfs", "zpool"})


def test_checksum_generation_and_independent_verification(tmp_path: Path) -> None:
    """Checksums must be stable and detect subsequent file mutation."""
    output = tmp_path / "audit"
    output.mkdir()
    (output / "alpha.json").write_text('{"alpha":1}\n', encoding="utf-8")
    (output / "beta.jsonl").write_text('{"beta":2}\n', encoding="utf-8")

    first = audit.write_sha256sums(output)
    first_bytes = (output / "SHA256SUMS").read_bytes()
    assert audit.verify_sha256sums(output) == first
    with pytest.raises(Exception, match="overwrite"):
        audit.write_sha256sums(output)
    assert (output / "SHA256SUMS").read_bytes() == first_bytes

    (output / "alpha.json").write_text('{"alpha":3}\n', encoding="utf-8")
    with pytest.raises(Exception, match="checksum mismatch"):
        audit.verify_sha256sums(output)


def test_output_writers_apply_private_file_permissions(tmp_path: Path) -> None:
    """Every audit output writer, including checksums, must create mode 0600 files."""
    output = tmp_path / "audit"
    output.mkdir(mode=0o700)
    audit._write_json(output / "synthetic.json", {"synthetic": True})
    audit._write_jsonl(output / "synthetic.jsonl", [{"synthetic": True}])
    audit._write_text(output / "synthetic.md", "Synthetic audit text.\n")

    audit.write_sha256sums(output)

    assert {
        path.name: path.stat().st_mode & 0o777
        for path in output.iterdir()
        if path.is_file()
    } == {
        "SHA256SUMS": 0o600,
        "synthetic.json": 0o600,
        "synthetic.jsonl": 0o600,
        "synthetic.md": 0o600,
    }


def test_semantic_inventory_uses_explicit_other_safe_fallback() -> None:
    """An unclassified safe contribution must retain the explicit fallback tag."""
    result = audit.semantic_inventory(
        "Azure lanterns remain beside quiet fictional windows.",
        {"lane": "mention"},
    )

    assert result["semantic_tags"] == ["other_safe_conversational_contribution"]
    assert result["proposed_primary_stratum"] == "other_safe_conversational_contribution"
    assert result["historical_outcome_used"] is False
    assert result["profile_outputs_used"] is False


def test_fresh_exact_normalised_dedup_is_time_first_and_deterministic() -> None:
    """Fresh exact duplicates keep one earliest time/target representative with proof."""
    candidates = [
        _synthetic_candidate(
            candidate_id="candidate-synthetic-later",
            target_id="target-synthetic-zeta",
            candidate_timestamp="2031-02-03T04:05:09Z",
            incoming_contribution="SYNTHETIC_CASE—ALPHA!",
            lane="mention",
            historical_outcome="editorial_no_reply",
            source_record_fingerprint="1" * 64,
            context_audit_record_sha256="2" * 64,
        ),
        _synthetic_candidate(
            candidate_id="candidate-synthetic-tie-zeta",
            target_id="target-synthetic-zeta",
            candidate_timestamp="2031-02-03T04:05:07Z",
            incoming_contribution=" synthetic case alpha ",
            lane="quote_tweet",
            historical_outcome="posted",
            source_record_fingerprint="3" * 64,
            context_audit_record_sha256="4" * 64,
        ),
        _synthetic_candidate(
            candidate_id="candidate-synthetic-representative",
            target_id="target-synthetic-alpha",
            candidate_timestamp="2031-02-03T04:05:07Z",
            incoming_contribution="Synthetic_case, alpha",
            lane="mention",
            historical_outcome="local_rejection",
            source_record_fingerprint="5" * 64,
            context_audit_record_sha256="6" * 64,
        ),
        _synthetic_candidate(
            candidate_id="candidate-synthetic-unique",
            target_id="target-synthetic-unique",
            candidate_timestamp="2031-02-03T04:05:08Z",
            incoming_contribution="A wholly distinct violet geometry statement.",
            lane="mention",
        ),
    ]

    first = audit.deduplicate_fresh_candidates(candidates)
    second = audit.deduplicate_fresh_candidates(list(reversed(candidates)))

    assert first == second
    kept, excluded, receipt = first
    assert [row["candidate_id"] for row in kept] == [
        "candidate-synthetic-representative",
        "candidate-synthetic-unique",
    ]
    assert [row["candidate_id"] for row in excluded] == [
        "candidate-synthetic-tie-zeta",
        "candidate-synthetic-later",
    ]
    assert receipt["algorithm"] == "fresh-exact-normalised-dedup-v1"
    assert receipt["representative_rule"] == "earliest_candidate_timestamp_then_target_id"
    assert receipt["duplicate_group_count"] == 1
    assert receipt["excluded_candidate_count"] == 2
    assert receipt["pairwise_exact_match_count"] == 3
    assert receipt["groups"] == [
        {
            "normalised_incoming_sha256": _sha256_text("synthetic case alpha"),
            "representative_candidate_id": "candidate-synthetic-representative",
            "representative_target_id": "target-synthetic-alpha",
            "member_candidate_ids": [
                "candidate-synthetic-representative",
                "candidate-synthetic-tie-zeta",
                "candidate-synthetic-later",
            ],
            "member_count": 3,
            "pairwise_exact_match_count": 3,
            "selection_rule": "earliest_candidate_timestamp_then_target_id",
        }
    ]
    for row in excluded:
        assert row["exclusion_reasons"] == [
            {
                "reason": "fresh_exact_normalised_incoming_duplicate",
                "matched_fresh_candidate_id": "candidate-synthetic-representative",
                "matched_fresh_target_id": "target-synthetic-alpha",
                "normalised_incoming_sha256": _sha256_text("synthetic case alpha"),
                "similarity": 1.0,
                "selection_rule": "earliest_candidate_timestamp_then_target_id",
            }
        ]


def test_quote_resolver_uses_exact_audited_public_source_selection() -> None:
    """Offline primary-source choice must project the audited public source exactly."""
    packet = {
        "date": "2031-02-03",
        "source_event": "Synthetic archive exercise",
        "_source_role_audit": {
            "renderable_sources": [
                {
                    "public_title": "Synthetic context volume",
                    "public_url": "https://context.example.invalid/volume",
                    "source_title": "Synthetic context volume",
                    "source_url": "https://context.example.invalid/volume",
                    "source_quality_class": "secondary_source",
                    "assigned_roles": ["historical_context_support"],
                    "claims_supported": ["historical_context"],
                },
                {
                    "public_title": "Synthetic verbatim record",
                    "public_url": "https://archive.example.invalid/transcript",
                    "source_title": "Synthetic verbatim record",
                    "source_url": "https://archive.example.invalid/transcript",
                    "source_quality_class": "primary_transcript",
                    "assigned_roles": [
                        "wording_verification",
                        "attribution_support",
                    ],
                    "claims_supported": ["verified_text", "speaker"],
                },
            ]
        },
    }

    public = audited_public_sources(packet)
    selected = audit._QuoteResolver._select_primary_source(packet)

    assert public[0]["roles"] == ["attribution_support", "wording_verification"]
    assert selected == {
        key: public[0][key] for key in ("title", "url", "source_type")
    }
    assert selected == {
        "title": "Synthetic verbatim record",
        "url": "https://archive.example.invalid/transcript",
        "source_type": "primary_transcript",
    }


def test_manifest_control_fields_are_zero_and_no_cost() -> None:
    """The immutable control fields must prove no model, provider, or posting work."""
    controls = audit.audit_manifest_control_fields()

    assert controls == {
        "model_calls": 0,
        "provider_http_requests": 0,
        "posting_actions": 0,
        "production_state_changes": 0,
        "live_project_included": False,
    }


def test_tool_has_no_paid_provider_posting_or_execute_path() -> None:
    """AST inspection must find no paid mode, provider transport, or posting call."""
    source = TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_import_roots = {
        "aiohttp",
        "anthropic",
        "httpx",
        "openai",
        "requests",
        "socket",
        "tweepy",
    }
    imported_roots: set[str] = set()
    flags: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                flags.add(node.args[0].value)

    assert not imported_roots.intersection(forbidden_import_roots)
    assert not flags.intersection({"--execute", "--paid", "--post", "--provider"})
    assert not called_names.intersection(
        {"create_tweet", "post_tweet", "urlopen", "request", "responses_create"}
    )
    assert "--execute" not in source
    assert "--paid" not in source


def test_added_public_callables_have_docstrings() -> None:
    """Maintained public audit callables must document their contracts."""
    expected = {
        "verify_profile_source",
        "strictly_after",
        "normalise_incoming",
        "near_duplicate_ratio",
        "candidate_contamination",
        "context_dependency_flags",
        "context_temporal_violations",
        "render_manual_context_review",
        "render_manual_clearance_csv",
        "ensure_safe_source_path",
        "prepare_output_directory",
        "write_sha256sums",
        "verify_sha256sums",
        "audit_manifest_control_fields",
        "resolve_cache_records",
        "read_jsonl_strict",
    }

    for name in expected:
        callable_object = getattr(audit, name)
        assert inspect.getdoc(callable_object), name
