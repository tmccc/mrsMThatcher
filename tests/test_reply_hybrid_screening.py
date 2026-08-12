"""Focused contracts for the bounded reply-hybrid screening runner."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import pilot_ai_first_reply_strategy as pilot
from tools import run_reply_hybrid_screening as screening


REAL_INPUT = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-hybrid-evaluation-audit-data-20260812T144330Z"
)


def synthetic_cases(count: int = 36) -> list[screening.ScreeningCase]:
    cases: list[screening.ScreeningCase] = []
    identifiers = [f"candidate-{index:064x}" for index in range(count)]
    if count == 36:
        identifiers[-2:] = list(screening.SOCIAL_CANDIDATE_IDS)
    for index, candidate_id in enumerate(sorted(identifiers)):
        context = {
            "target_id": f"target-{index}",
            "thread_id": f"thread-{index}",
            "lane": "mention",
            "incoming_contribution": f"Synthetic contribution {index}",
            "quoted_post": None,
            "parent_thread": [],
            "clarification_request": None,
            "current_date": "2026-08-12",
        }
        recent = [f"Recent account reply {index}-{reply}" for reply in range(2)]
        cases.append(screening.ScreeningCase(
            candidate_id=candidate_id,
            incoming_contribution=context["incoming_contribution"],
            lane="mention",
            replay_context=context,
            recent_replies=recent,
            resolved_quotation=None,
            context_hash=screening.value_sha256(context),
            recent_replies_hash=screening.value_sha256(recent),
            resolved_quotation_hash=screening.value_sha256(None),
            context_audit_record_hash=hashlib.sha256(candidate_id.encode()).hexdigest(),
        ))
    return sorted(cases, key=lambda case: case.candidate_id)


def completed_record(
    case: screening.ScreeningCase,
    profile: str,
    status: str,
    *,
    text: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": case.candidate_id,
        "profile": profile,
        "profile_commit": "private-profile-commit",
        "status": status,
        "terminal_reason": "synthetic",
        "public_reply": text,
        "model_call_count": 1,
        "revision_count": 0,
        "pipeline_audit": [],
        "cost_usd": 0.0123,
        "retry_count": 7,
        "historical_public_reply": "HISTORICAL_REPLY_MUST_NOT_LEAK",
    }


class FakeResponse:
    def __init__(
        self,
        document: dict[str, Any],
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.document = document
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self.document

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeProvider:
    def __init__(self) -> None:
        self.get_count = 0
        self.post_count = 0

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.get_count += 1
        return FakeResponse({"data": [{
            "id": screening.DEFAULT_MODEL,
            "prompt_text_token_price": 1,
            "cached_prompt_text_token_price": 1,
            "completion_text_token_price": 1,
        }]})

    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.post_count += 1
        return FakeResponse({
            "id": f"fake-request-{self.post_count}",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
                "cost_in_usd_ticks": 1000,
            },
        })


class ScriptedProfile:
    def __init__(
        self,
        profile: str,
        calls: list[dict[str, Any]],
        *,
        synthetic_failure: bool = True,
    ) -> None:
        self.profile = profile
        self.calls = calls
        self.synthetic_failure = synthetic_failure

    def run_reply_pipeline(self, **kwargs: Any) -> SimpleNamespace:
        context = json.loads(json.dumps(kwargs["context"]))
        self.calls.append({
            "profile": self.profile,
            "candidate_id": context["target_id"],
            "context": context,
            "config": json.loads(json.dumps(kwargs["config"])),
            "recent_replies": list(kwargs["recent_replies"]),
            "creation_time": kwargs["creation_time"],
            "media_context": kwargs["media_context"],
        })
        kwargs["transport"](
            stage="proposer",
            model=kwargs["config"]["proposer_model"],
            system_prompt=f"system {self.profile}",
            user_prompt=f"user {context['target_id']}",
            response_schema={"type": "object", "properties": {}, "additionalProperties": False},
            timeout_seconds=60,
            max_output_tokens=900,
            media_context=None,
        )
        index = int(context["target_id"].split("-")[-1])
        if self.synthetic_failure and self.profile == "conservative_hybrid" and index == 5:
            return SimpleNamespace(
                reply=None,
                status="operational_failure",
                reason="synthetic_invalid",
                model_call_count=1,
                revision_count=0,
                audit=({"stage": "proposer", "status": "invalid"},),
            )
        if self.profile == "conservative_hybrid":
            return SimpleNamespace(
                reply=None,
                status="no_reply",
                reason="independent_no_reply_confirmed",
                model_call_count=1,
                revision_count=0,
                audit=({"stage": "proposer", "status": "completed"},),
            )
        return SimpleNamespace(
            reply=f"Approved synthetic reply {index}",
            status="approved",
            reason="reviewer_approved",
            model_call_count=1,
            revision_count=0,
            audit=({"stage": "proposer", "status": "completed"},),
        )


@pytest.fixture(scope="module")
def verified_real_input() -> dict[str, Any]:
    return screening.verify_input_bundle(REAL_INPUT)


def test_complete_corrected_input_and_membership(verified_real_input: dict[str, Any]) -> None:
    verification = verified_real_input["verification"]
    assert verification["sha256sums_sha256"] == screening.EXPECTED_SHA256SUMS_SHA256
    assert verification["candidate_count"] == 36
    assert verification["context_join_count"] == 36
    assert verification["candidate_ids_sha256"] == screening.EXPECTED_CANDIDATE_IDS_SHA256
    assert verification["quotation_resolution_count"] == 31
    assert all(case.replay_context["current_date"] == "2026-08-12" for case in verified_real_input["cases"])


def test_input_tamper_and_incomplete_membership_fail_closed(tmp_path: Path) -> None:
    copied = tmp_path / "input"
    shutil.copytree(REAL_INPUT, copied)
    target = copied / "context_audit_summary.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(screening.ScreeningError, match="checksum mismatch"):
        screening.verify_input_bundle(copied)
    shutil.copy2(REAL_INPUT / "context_audit_summary.json", target)
    (copied / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(screening.ScreeningError, match="membership differs"):
        screening.verify_input_bundle(copied)


def test_exact_profiles_transport_and_isolated_modules(tmp_path: Path) -> None:
    plan = screening.load_plan()
    manifests, sources, _ = screening.verify_and_stage_profiles(plan)
    assert manifests["hardened_current"]["source_sha256"] == (
        "1d14501136b7317f7587b1b1763514d0ada399faf5c86db32742c7bf5faa1e4b"
    )
    assert manifests["conservative_hybrid"]["source_sha256"] == (
        "b529777eddbdd93b63b719cf52d546a1d153bb8c812aa3c0dd8d10283c58cfbf"
    )
    assert manifests["hardened_current"]["versions"]["DRAFT_SCHEMA_VERSION"] == 9
    assert manifests["conservative_hybrid"]["versions"]["PROPOSER_PROMPT_VERSION"] == "ai-first-proposer-v16"
    assert screening._operational_ast(sources["hardened_current"]) == screening._operational_ast(
        sources["conservative_hybrid"]
    )
    paths: dict[str, Path] = {}
    for profile, source in sources.items():
        path = tmp_path / f"{profile}.py"
        path.write_bytes(source)
        paths[profile] = path
    modules = {profile: screening.load_isolated_profile(profile, path) for profile, path in paths.items()}
    assert modules["hardened_current"] is not modules["conservative_hybrid"]
    assert modules["hardened_current"].PROPOSER_PROMPT_VERSION == "ai-first-proposer-v15"
    assert modules["conservative_hybrid"].PROPOSER_PROMPT_VERSION == "ai-first-proposer-v16"
    assert modules["hardened_current"].__name__ in sys.modules
    assert modules["conservative_hybrid"].__name__ in sys.modules


def test_rejects_non_prompt_operational_source_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = screening.load_plan()
    original_git = screening._git

    def changed_git(*arguments: str, text: bool = True) -> str | bytes:
        value = original_git(*arguments, text=text)
        if (
            arguments == ("show", f"{plan['profiles']['conservative_hybrid']['commit']}:reply_strategy.py")
            and isinstance(value, bytes)
        ):
            old = b'if call_count >= config["maximum_model_calls"]:'
            new = b'if call_count > config["maximum_model_calls"]:'
            assert old in value
            return value.replace(old, new, 1)
        return value

    monkeypatch.setattr(screening, "_git", changed_git)
    with pytest.raises(screening.ScreeningError, match="non-prompt operational difference"):
        screening.verify_and_stage_profiles(plan)


def test_hmac_pair_plan_and_labels_are_independent() -> None:
    cases = synthetic_cases()
    execution_seed = bytes(range(32))
    other_execution_seed = bytes(reversed(range(32)))
    response_seed = bytes([91]) * 32
    creation = "2026-08-12T18:00:00Z"
    plan = screening.build_execution_plan(cases, execution_seed, run_id="run", creation_time=creation)
    repeated = screening.build_execution_plan(cases, execution_seed, run_id="run", creation_time=creation)
    changed = screening.build_execution_plan(cases, other_execution_seed, run_id="run", creation_time=creation)
    assert plan == repeated
    assert plan != changed
    assert plan["candidate_count"] == 36
    assert plan["planned_pipeline_executions"] == 72
    pairs = defaultdict(list)
    for execution in plan["executions"]:
        pairs[execution["candidate_id"]].append(execution)
    assert all(len(rows) == 2 for rows in pairs.values())
    assert all({row["profile"] for row in rows} == set(screening.PROFILE_NAMES) for rows in pairs.values())
    assert all(rows[0]["execution_index"] + 1 == rows[1]["execution_index"] for rows in pairs.values())
    labels = screening.build_blind_assignments(cases, response_seed)
    labels_after_execution_change = screening.build_blind_assignments(cases, response_seed)
    assert labels == labels_after_execution_change
    assert set(labels) == {case.candidate_id for case in cases}
    assert all(set(mapping) == {"A", "B"} and set(mapping.values()) == set(screening.PROFILE_NAMES) for mapping in labels.values())


def test_execution_identity_binds_candidate_profile_and_shared_inputs(verified_real_input: dict[str, Any]) -> None:
    plan = screening.load_plan()
    manifests, _, _ = screening.verify_and_stage_profiles(plan)
    case = verified_real_input["cases"][0]
    kwargs = {
        "run_id": "r" * 64,
        "case": case,
        "evidence_identity": verified_real_input["evidence_identity"],
        "model": screening.DEFAULT_MODEL,
        "config_hash": "c" * 64,
    }
    identities = {
        profile: screening.pipeline_execution_identity(
            profile=profile, profile_manifest=manifests[profile], **kwargs
        )
        for profile in screening.PROFILE_NAMES
    }
    differing = {key for key in identities[screening.PROFILE_NAMES[0]] if identities[screening.PROFILE_NAMES[0]][key] != identities[screening.PROFILE_NAMES[1]][key]}
    assert differing == {"profile", "profile_commit"}
    bindings = {profile: screening.execution_binding(identity) for profile, identity in identities.items()}
    assert bindings[screening.PROFILE_NAMES[0]]["case_identity"] != bindings[screening.PROFILE_NAMES[1]]["case_identity"]
    assert case.candidate_id in bindings[screening.PROFILE_NAMES[0]]["case_identity"]


def test_result_accounting_and_no_reply_rendering() -> None:
    approved = SimpleNamespace(
        reply="Approved", status="approved", reason="reviewer_approved",
        model_call_count=2, revision_count=0, audit=(),
    )
    silence = SimpleNamespace(
        reply=None, status="no_reply", reason="independent_no_reply_confirmed",
        model_call_count=2, revision_count=0, audit=(),
    )
    disabled = SimpleNamespace(
        reply=None, status="disabled", reason="strategy_disabled",
        model_call_count=0, revision_count=0, audit=(),
    )
    assert screening.classify_result(approved)["status"] == "approved"
    no_reply = screening.classify_result(silence)
    assert no_reply["status"] == "no_reply"
    assert screening.render_completed_outcome(no_reply) == "[NO_REPLY]"
    failure = screening.classify_result(disabled)
    assert failure["status"] == "operational_failure"
    with pytest.raises(screening.ScreeningError, match="cannot be rendered"):
        screening.render_completed_outcome(failure)


def test_hard_cost_ceiling_stops_before_provider_send(tmp_path: Path) -> None:
    ledger = pilot.PilotLedger(
        tmp_path / "ledger.json", model=screening.DEFAULT_MODEL,
        hard_limit_usd=screening.HARD_LIMIT_USD, run_version="synthetic-ceiling",
    )
    calls = 0

    def forbidden_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("provider send must not occur")

    transport = pilot.PilotTransport(
        api_key="fake-key",
        base_url=screening.DEFAULT_XAI_BASE,
        model_metadata={
            "model": screening.DEFAULT_MODEL,
            "prompt_text_token_price": 1_000_000_000,
            "cached_prompt_text_token_price": 1_000_000_000,
            "completion_text_token_price": 1_000_000_000,
        },
        ledger=ledger,
        response_dir=tmp_path / "responses",
        post=forbidden_post,
    )
    transport.set_case("candidate:profile:execution")
    with pytest.raises(pilot.CostLimitReached):
        transport(
            stage="proposer", model=screening.DEFAULT_MODEL,
            system_prompt="system", user_prompt="user",
            response_schema={"type": "object"}, timeout_seconds=60,
            max_output_tokens=900, media_context=None,
        )
    assert calls == 0
    assert ledger.data["known_cost_usd"] == 0
    assert ledger.data["ambiguous_exposure_usd"] == 0


def test_blinded_allowlist_and_unscorable_failure_handling() -> None:
    cases = synthetic_cases(3)
    assignments = screening.build_blind_assignments(cases, bytes([7]) * 32)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for index, case in enumerate(cases):
        records[(case.candidate_id, "hardened_current")] = completed_record(
            case, "hardened_current", "approved", text=f"Reply {index}"
        )
        records[(case.candidate_id, "conservative_hybrid")] = completed_record(
            case,
            "conservative_hybrid",
            "operational_failure" if index == 1 else "no_reply",
        )
    files, unscorable = screening.build_review_pack(cases, records, assignments)
    combined = b"\n".join(files.values())
    assert unscorable == [cases[1].candidate_id]
    assert cases[1].candidate_id in json.loads(files["blind_unscorable_cases.json"])["candidate_ids"]
    assert b"HISTORICAL_REPLY_MUST_NOT_LEAK" not in combined
    assert b"hardened_current" not in combined
    assert b"conservative_hybrid" not in combined
    assert b"private-profile-commit" not in combined
    assert b"cost_usd" not in combined
    assert b"retry_count" not in combined
    assert b"[NO_REPLY]" in combined
    csv_rows = list(csv.DictReader(io.StringIO(files["blind_quality_review.csv"].decode())))
    assert len(csv_rows) == 2
    assert all(all(row[field] == "" for field in screening.REVIEWER_FIELDS) for row in csv_rows)


def test_validate_only_has_no_network_or_posting_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("validate-only attempted network")

    monkeypatch.setattr(pilot.requests, "get", forbidden)
    monkeypatch.setattr(pilot.requests, "post", forbidden)
    args = screening.build_parser().parse_args(["--input", str(REAL_INPUT)])
    result = screening.run(args, {})
    assert result["mode"] == "validate-only"
    assert result["network_requests"] == 0
    source = Path(screening.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "import tweepy" not in source
    assert "--post" not in source
    assert "--deploy" not in source
    assert "--restart" not in source


@pytest.fixture
def research_scratch() -> Any:
    root = Path(tempfile.mkdtemp(prefix="reply-hybrid-screening-test-", dir="/disks/disk1/research"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _synthetic_verified(cases: list[screening.ScreeningCase], corpus: Path) -> dict[str, Any]:
    identity = {
        "corpus_path": str(corpus),
        "source_file_sha256": {"synthetic": "e" * 64},
        "factual_evidence_sha256": "f" * 64,
        "reply_evidence_sha256": screening.EVIDENCE_SHA256,
    }
    identity["identity_sha256"] = screening.value_sha256(identity)
    return {
        "cases": cases,
        "repository": object(),
        "corpus_path": corpus,
        "evidence_identity": identity,
        "verification": {
            "input_directory": "synthetic",
            "sha256sums_sha256": screening.EXPECTED_SHA256SUMS_SHA256,
            "member_sha256": {},
            "candidate_count": 36,
            "candidate_ids_sha256": screening.EXPECTED_CANDIDATE_IDS_SHA256,
            "context_join_count": 36,
            "quotation_resolution_count": 0,
            "development_cutoff": "2026-08-10T05:16:15Z",
        },
    }


def _execute_args(output: Path, *, resume: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        output=output,
        resume=resume,
        model=screening.DEFAULT_MODEL,
        xai_base=screening.DEFAULT_XAI_BASE,
        maximum_rate_limit_retries=1,
        maximum_server_error_retries=1,
        api_key="synthetic-xai-key",
    )


def _scripted_execution_material(
    monkeypatch: pytest.MonkeyPatch,
    cases: list[screening.ScreeningCase],
    calls: list[dict[str, Any]],
    *,
    synthetic_failure: bool = False,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, bytes], dict[str, Any], dict[str, Any]
]:
    modules = {
        profile: ScriptedProfile(
            profile, calls, synthetic_failure=synthetic_failure
        )
        for profile in screening.PROFILE_NAMES
    }
    monkeypatch.setattr(
        screening,
        "load_isolated_profile",
        lambda profile, _path: modules[profile],
    )
    seeds = iter((bytes(range(32)), bytes(reversed(range(32)))))
    monkeypatch.setattr(screening.secrets, "token_bytes", lambda _size: next(seeds))
    plan_spec = screening.load_plan()
    manifests, real_sources, _ = screening.verify_and_stage_profiles(plan_spec)
    verified = _synthetic_verified(
        cases,
        screening.PROJECT_ROOT
        / "semantic_alignment_research/quote_research_full_001",
    )
    provenance = {
        "runner_commit": "a" * 40,
        "expected_runner_commit": "a" * 40,
        "branch": "research/synthetic",
        "worktree_clean": True,
        "source_sha256": {"runner": "b" * 64},
    }
    return plan_spec, manifests, real_sources, verified, provenance


def test_fake_provider_execution_resume_and_archive_separation(
    monkeypatch: pytest.MonkeyPatch,
    research_scratch: Path,
) -> None:
    cases = synthetic_cases()
    output_name = "mrsMThatcher-reply-hybrid-screening-20260812T190000Z"
    output = research_scratch / output_name
    provider = FakeProvider()
    calls: list[dict[str, Any]] = []
    modules = {
        profile: ScriptedProfile(profile, calls) for profile in screening.PROFILE_NAMES
    }
    monkeypatch.setattr(screening, "load_isolated_profile", lambda profile, _path: modules[profile])
    seeds = iter((bytes(range(32)), bytes(reversed(range(32)))))
    monkeypatch.setattr(screening.secrets, "token_bytes", lambda _size: next(seeds))
    plan_spec = screening.load_plan()
    manifests, real_sources, _ = screening.verify_and_stage_profiles(plan_spec)
    verified = _synthetic_verified(cases, screening.PROJECT_ROOT / "semantic_alignment_research/quote_research_full_001")
    provenance = {
        "runner_commit": "a" * 40,
        "expected_runner_commit": "a" * 40,
        "branch": "research/synthetic",
        "worktree_clean": True,
        "source_sha256": {"runner": "b" * 64},
    }

    original_append = screening.append_jsonl
    execution_appends = 0
    interrupted = False

    def interrupt_once(path: Path, row: dict[str, Any]) -> None:
        nonlocal execution_appends, interrupted
        if path.name == "execution_records.jsonl":
            execution_appends += 1
            if execution_appends == 5 and not interrupted:
                interrupted = True
                raise RuntimeError("synthetic interruption after completed provider call")
        original_append(path, row)

    monkeypatch.setattr(screening, "append_jsonl", interrupt_once)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        screening.execute_screening(
            _execute_args(output), verified, plan_spec, provenance, manifests, real_sources,
            get=provider.get, post=provider.post,
        )
    assert provider.get_count == 1
    assert provider.post_count == 5
    monkeypatch.setattr(screening, "append_jsonl", original_append)

    cache_path = next((output / "private_audit/provider_response_cache").iterdir())
    original_cache = cache_path.read_bytes()
    corrupted_cache = json.loads(original_cache)
    corrupted_cache["raw"]["choices"][0]["message"]["content"] = "tampered"
    screening.write_json(cache_path, corrupted_cache)
    with pytest.raises(screening.ScreeningError, match="cache accounting differs"):
        screening.execute_screening(
            _execute_args(output, resume=True), verified, plan_spec, provenance, manifests,
            real_sources, get=provider.get, post=provider.post,
        )
    assert provider.get_count == 1
    assert provider.post_count == 5
    screening.atomic_bytes(cache_path, original_cache)

    result = screening.execute_screening(
        _execute_args(output, resume=True), verified, plan_spec, provenance, manifests,
        real_sources, get=provider.get, post=provider.post,
    )
    assert provider.get_count == 2
    assert provider.post_count == 72
    assert result["planned_execution_count"] == 72
    assert result["completed_execution_count"] == 72
    assert result["operational_failure_execution_count"] == 1
    assert result["unscorable_candidate_count"] == 1
    assert result["scorable_paired_case_count"] == 35
    assert result["known_cost_usd"] == pytest.approx(72_000 / pilot.USD_TICKS_PER_DOLLAR)
    assert result["ambiguous_exposure_usd"] == 0
    assert result["no_case_replacement"] is True

    by_target = defaultdict(list)
    for call in calls:
        by_target[call["candidate_id"]].append(call)
    assert set(by_target) == {f"target-{index}" for index in range(36)}
    assert all({call["profile"] for call in pair} == set(screening.PROFILE_NAMES) for pair in by_target.values())
    for pair in by_target.values():
        # One interrupted execution is reconstructed from cache, so it appears twice.
        unique = {call["profile"]: call for call in pair}
        assert len(unique) == 2
        first, second = unique.values()
        assert first["context"] == second["context"]
        assert first["config"] == second["config"]
        assert first["recent_replies"] == second["recent_replies"]
        assert first["creation_time"] == second["creation_time"]
        assert first["media_context"] is second["media_context"] is None

    records = screening._index_jsonl(
        output / "private_audit/execution_records.jsonl", ("candidate_id", "profile")
    )
    assert len(records) == 72
    assert all(len(row["logical_call_ids"]) == 1 for row in records.values())
    for candidate_id in {key[0] for key in records}:
        logical = [records[(candidate_id, profile)]["logical_call_ids"][0] for profile in screening.PROFILE_NAMES]
        assert logical[0] != logical[1]
        assert all(candidate_id in value for value in logical)

    review_archive = Path(result["review_archive"])
    audit_archive = Path(result["audit_archive"])
    assert review_archive.is_file() and audit_archive.is_file()
    duplicate_review_archive = research_scratch / "determinism-check.tgz"
    assert screening.publish_deterministic_archive(
        output / "review_pack", duplicate_review_archive
    ) == result["review_archive_sha256"]
    assert duplicate_review_archive.read_bytes() == review_archive.read_bytes()
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(review_archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(audit_archive.stat().st_mode) == 0o600
    with tarfile.open(review_archive, "r:gz") as archive:
        review_names = {name.removeprefix("./") for name in archive.getnames() if name not in {".", "./"}}
        for member in archive.getmembers():
            assert stat.S_IMODE(member.mode) == (0o700 if member.isdir() else 0o600)
            if member.isfile():
                assert b"synthetic-xai-key" not in (archive.extractfile(member).read())
    assert review_names == set(screening.REVIEW_FILES)
    with tarfile.open(audit_archive, "r:gz") as archive:
        audit_names = {name.removeprefix("./") for name in archive.getnames()}
        for member in archive.getmembers():
            assert stat.S_IMODE(member.mode) == (0o700 if member.isdir() else 0o600)
            if member.isfile():
                assert b"synthetic-xai-key" not in (archive.extractfile(member).read())
    assert "private_seeds.json" in audit_names
    assert "blind_key.json" in audit_names
    assert "cost_ledger.json" in audit_names
    assert not ((set(screening.REVIEW_FILES) - {"SHA256SUMS"}) & audit_names)
    summary = json.loads((output / "private_audit/reliability_summary.json").read_text())
    assert summary["social_candidate_ids"] == list(screening.SOCIAL_CANDIDATE_IDS)
    assert summary["deployment_authorised"] is False
    assert summary["social_supplement_still_required"] is True

    shutil.rmtree(output)
    review_archive.unlink()
    audit_archive.unlink()


def test_ambiguous_transmission_blocks_all_later_provider_sends(
    monkeypatch: pytest.MonkeyPatch,
    research_scratch: Path,
) -> None:
    cases = synthetic_cases()
    output = research_scratch / "mrsMThatcher-reply-hybrid-screening-20260812T191000Z"
    provider = FakeProvider()
    calls: list[dict[str, Any]] = []
    plan_spec, manifests, sources, verified, provenance = _scripted_execution_material(
        monkeypatch, cases, calls
    )

    def ambiguous_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        provider.post_count += 1
        raise pilot.requests.exceptions.Timeout("synthetic ambiguous transmission")

    result = screening.execute_screening(
        _execute_args(output), verified, plan_spec, provenance, manifests, sources,
        get=provider.get, post=ambiguous_post,
    )
    assert provider.get_count == 1
    assert provider.post_count == 1
    assert len(calls) == 1
    assert result["planned_execution_count"] == 72
    assert result["completed_execution_count"] == 1
    assert result["operational_failure_execution_count"] == 72
    assert result["scorable_paired_case_count"] == 0
    assert result["unscorable_candidate_count"] == 36
    assert result["ambiguous_exposure_usd"] > 0
    assert result["run_blocked_kind"] == "ambiguous_provider_transmission"
    records = screening._index_jsonl(
        output / "private_audit/execution_records.jsonl", ("candidate_id", "profile")
    )
    assert len(records) == 72
    assert sum(row["attempted"] for row in records.values()) == 1


def test_ambiguous_received_response_with_missing_cost_is_retained_and_finalised(
    monkeypatch: pytest.MonkeyPatch,
    research_scratch: Path,
) -> None:
    cases = synthetic_cases()
    output = research_scratch / "mrsMThatcher-reply-hybrid-screening-20260812T191500Z"
    provider = FakeProvider()
    calls: list[dict[str, Any]] = []
    plan_spec, manifests, sources, verified, provenance = _scripted_execution_material(
        monkeypatch, cases, calls
    )

    def missing_cost(*_args: Any, **_kwargs: Any) -> FakeResponse:
        provider.post_count += 1
        return FakeResponse({
            "id": "fake-missing-cost",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        })

    result = screening.execute_screening(
        _execute_args(output), verified, plan_spec, provenance, manifests, sources,
        get=provider.get, post=missing_cost,
    )
    assert provider.post_count == 1
    assert result["run_blocked_kind"] == "ambiguous_provider_transmission"
    assert result["ambiguous_exposure_usd"] > 0
    records = screening._index_jsonl(
        output / "private_audit/execution_records.jsonl", ("candidate_id", "profile")
    )
    attempted = next(row for row in records.values() if row["attempted"])
    assert len(attempted["durable_response_cache_references"]) == 1
    assert Path(result["review_archive"]).is_file()
    assert Path(result["audit_archive"]).is_file()


def test_exhausted_bounded_429_is_local_and_remaining_executions_continue(
    monkeypatch: pytest.MonkeyPatch,
    research_scratch: Path,
) -> None:
    cases = synthetic_cases()
    output = research_scratch / "mrsMThatcher-reply-hybrid-screening-20260812T192000Z"
    provider = FakeProvider()
    calls: list[dict[str, Any]] = []
    plan_spec, manifests, sources, verified, provenance = _scripted_execution_material(
        monkeypatch, cases, calls
    )

    def rate_limited_then_success(*_args: Any, **_kwargs: Any) -> FakeResponse:
        provider.post_count += 1
        if provider.post_count <= 2:
            return FakeResponse(
                {"error": "synthetic rate limit"},
                429,
                headers={"Retry-After": "0"},
            )
        return FakeResponse({
            "id": f"fake-request-{provider.post_count}",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
                "cost_in_usd_ticks": 1000,
            },
        })

    result = screening.execute_screening(
        _execute_args(output), verified, plan_spec, provenance, manifests, sources,
        get=provider.get, post=rate_limited_then_success,
    )
    assert provider.get_count == 1
    assert provider.post_count == 73
    assert len(calls) == 72
    assert result["completed_execution_count"] == 72
    assert result["operational_failure_execution_count"] == 1
    assert result["scorable_paired_case_count"] == 35
    assert result["unscorable_candidate_count"] == 1
    assert result["run_blocked"] is False
    assert result["ambiguous_exposure_usd"] == 0
    ledger = json.loads((output / "private_audit/cost_ledger.json").read_text())
    statuses = Counter(row["status"] for row in ledger["operations"])
    assert statuses == {"completed": 71, "rate_limited": 1}


def test_full_run_cost_guard_stops_before_next_send_and_finalises(
    monkeypatch: pytest.MonkeyPatch,
    research_scratch: Path,
) -> None:
    cases = synthetic_cases()
    output = research_scratch / "mrsMThatcher-reply-hybrid-screening-20260812T193000Z"
    provider = FakeProvider()
    calls: list[dict[str, Any]] = []
    plan_spec, manifests, sources, verified, provenance = _scripted_execution_material(
        monkeypatch, cases, calls
    )

    def nearly_exhausting_response(*_args: Any, **_kwargs: Any) -> FakeResponse:
        provider.post_count += 1
        return FakeResponse({
            "id": "fake-near-ceiling",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
                "cost_in_usd_ticks": 14_999_999_000,
            },
        })

    result = screening.execute_screening(
        _execute_args(output), verified, plan_spec, provenance, manifests, sources,
        get=provider.get, post=nearly_exhausting_response,
    )
    assert provider.post_count == 1
    assert len(calls) == 2
    assert result["completed_execution_count"] == 2
    assert result["cost_ceiling_reached"] is True
    assert result["run_blocked_kind"] == "hard_cost_ceiling"
    assert result["known_cost_usd"] == pytest.approx(1.4999999)
    assert result["ambiguous_exposure_usd"] == 0
    assert Path(result["review_archive"]).is_file()
    assert Path(result["audit_archive"]).is_file()


def test_exact_social_ids_and_closed_plan() -> None:
    plan = screening.load_plan()
    assert tuple(plan["social_candidate_ids"]) == screening.SOCIAL_CANDIDATE_IDS
    assert plan["candidate_count"] == 36
    assert plan["planned_pipeline_executions"] == 72
    assert plan["all_36_primary_population"] is True
    assert plan["no_case_replacement"] is True
    assert plan["deployment_blocked_pending_social_supplement"] is True
