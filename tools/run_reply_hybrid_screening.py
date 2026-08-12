#!/usr/bin/env python3
"""Run one bounded, non-posting current-versus-hybrid reply screening.

Validate-only is the default and performs no network request.  Execute mode is
bound to a clean committed runner, immutable inputs, exact Git profile blobs,
the reviewed xAI transport, one durable output directory and a USD 1.50 cap.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reply_evidence import EvidenceRepository
from tools import pilot_ai_first_reply_strategy as pilot


RUNNER_VERSION = "reply-hybrid-screening-v1"
BASE_COMMIT = "42e5f67e42a9f653a23f32060caf7087837e4f7b"
TRANSPORT_COMMIT = "cc404c3bd45b97d7664ca4fec02cf1f4154a6a1a"
TRANSPORT_SHA256 = "7c677d236b76263f8c783f1208ca3613953a36519a11ae632050539cd2ecb0fc"
EVIDENCE_SHA256 = "d970feebcccb19acc734f807b2d46941bfbe364540f77a298bd21f3eee578ec9"
PLAN_PATH = PROJECT_ROOT / "reply_hybrid_screening_plan.json"
DEFAULT_INPUT = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-hybrid-evaluation-audit-data-20260812T144330Z"
)
DEFAULT_MODEL = "grok-4.3"
DEFAULT_XAI_BASE = "https://api.x.ai/v1"
PAID_ACKNOWLEDGEMENT = "YES_I_UNDERSTAND"
HARD_LIMIT_USD = 1.50
MAXIMUM_REPLY_LENGTH = 270
EXPECTED_SHA256SUMS_SHA256 = (
    "f52684112d6edff957d6894c0c8ad6ecb52656ea0d5f6bd55acd2ff5e9c7b7fc"
)
EXPECTED_CANDIDATE_IDS_SHA256 = (
    "836b5120ecfb450a2c0f5a17cb75f4ba63ae5eb872f882efc6db64ef74f23859"
)
EXPECTED_INPUT_HASHES = {
    "fresh_candidate_inventory.jsonl": "32b6807162dd02a12481b9a19c0ad3fd71e8a2b68065d787437cd6cdcb3c6de4",
    "context_audit.jsonl": "aeb7ce0c54bfc9a9158b161fc502b717ca39e3dc041254491e881f466f93bf37",
    "context_audit_summary.json": "9b552b1062e238d165220c774c63360c381fa5ddd855aae0358d9f1f7e6cfb6e",
    "source_inventory.json": "3adf4967e7f3f09e0a6699ff8dd34f2e75fefaa8538184a65f04c9560ab92dec",
    "profile_identity_verification.json": "91b834b767e543369fbd042ed1293578fd4c02e9f706bf605621d4a5d9766d7d",
    "freshness_boundary.json": "786192da342fc029c5008d46ce5a0361f9d1826dbff67ed2b814442d28ab0659",
    "run_manifest.json": "4abce54ab315b375b7f31a93e0f7e0f90f7b1776185cf13be09d1e61eb6265a5",
}
PROFILE_NAMES = ("hardened_current", "conservative_hybrid")
SOCIAL_CANDIDATE_IDS = (
    "candidate-2d6c8f80e105f5a17afc45e41fd76b9e96fb717d30f4ed1be2f0db7f8aa2a2b8",
    "candidate-d7588c23e1a73d0cebaf6e4f0550c8550602eb9ff0d0aaed287e07df88588f17",
)
PROMPT_FUNCTIONS = {
    "proposer": "_proposer_prompts",
    "reviewer": "_reviewer_prompts",
    "evidence": "_evidence_prompts",
    "no_reply_reviewer": "_no_reply_review_prompts",
    "claim_auditor": "_claim_auditor_prompts",
}
REVIEW_FILES = (
    "blind_quality_review.md",
    "blind_quality_review.csv",
    "blind_unscorable_cases.json",
    "review_instructions.md",
    "review_pack_manifest.json",
    "SHA256SUMS",
)
REVIEWER_FIELDS = (
    "pair_decision",
    "safety_A",
    "safety_B",
    "incorrect_no_reply_A",
    "incorrect_no_reply_B",
    "manufactured_political_lecture_A",
    "manufactured_political_lecture_B",
    "failed_only_because_brief_A",
    "failed_only_because_brief_B",
    "reviewer_note",
)
EXACT_CORPUS_PATH = Path(
    "/disks/disk1/research/"
    "mrsMThatcher-reply-hybrid-evaluation-audit-20260812T111858Z/"
    "semantic_alignment_research/quote_research_full_001"
)


class ScreeningError(RuntimeError):
    """An immutable screening contract failed."""


@dataclass(frozen=True)
class ScreeningCase:
    """The only candidate fields admitted to pipeline execution and review."""

    candidate_id: str
    incoming_contribution: str
    lane: str
    replay_context: dict[str, Any]
    recent_replies: list[str]
    resolved_quotation: dict[str, Any] | None
    context_hash: str
    recent_replies_hash: str
    resolved_quotation_hash: str
    context_audit_record_hash: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_document_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(path, json_document_bytes(value))


def write_text(path: Path, value: str) -> None:
    atomic_bytes(path, value.encode("utf-8"))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(canonical_json_bytes(row) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningError(f"invalid JSON file: {path}") from exc


def read_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                raise ScreeningError(f"blank JSONL row at {path}:{number}")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ScreeningError(f"non-object JSONL row at {path}:{number}")
            rows.append(row)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningError(f"invalid JSONL file: {path}") from exc
    return rows


def _git(*arguments: str, text: bool = True) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScreeningError(f"local Git query failed: {' '.join(arguments)}") from exc
    return result.stdout


def load_plan() -> dict[str, Any]:
    plan = read_json(PLAN_PATH)
    if (
        plan.get("candidate_count") != 36
        or plan.get("planned_pipeline_executions") != 72
        or plan.get("candidate_ids_sha256") != EXPECTED_CANDIDATE_IDS_SHA256
        or plan.get("hard_cost_limit_usd") != HARD_LIMIT_USD
        or tuple(plan.get("social_candidate_ids") or ()) != SOCIAL_CANDIDATE_IDS
        or plan.get("screening_only") is not True
        or plan.get("deployment_authorised") is not False
        or plan.get("social_supplement_still_required") is not True
        or plan.get("no_case_replacement") is not True
    ):
        raise ScreeningError("tracked screening plan differs from the closed design")
    return plan


def execution_provenance(
    expected_commit: str | None = None, *, require_clean: bool = False
) -> dict[str, Any]:
    commit = str(_git("rev-parse", "HEAD")).strip()
    branch = str(_git("branch", "--show-current")).strip()
    status = str(_git("status", "--porcelain=v1", "--untracked-files=all"))
    paths = {
        "runner": PROJECT_ROOT / "tools/run_reply_hybrid_screening.py",
        "plan": PLAN_PATH,
        "reply_evidence": PROJECT_ROOT / "reply_evidence.py",
        "factual_evidence": PROJECT_ROOT / "reply_factual_evidence.json",
        "pilot_transport": PROJECT_ROOT / "tools/pilot_ai_first_reply_strategy.py",
    }
    if require_clean:
        if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
            raise ScreeningError("execute requires --expected-runner-commit as one exact SHA")
        if commit != expected_commit:
            raise ScreeningError("runner commit does not match --expected-runner-commit")
        if status:
            raise ScreeningError("execute requires a completely clean worktree")
        for path in paths.values():
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            committed = _git("show", f"{commit}:{relative}", text=False)
            assert isinstance(committed, bytes)
            if hashlib.sha256(committed).hexdigest() != file_sha256(path):
                raise ScreeningError(f"checkout source differs from commit: {relative}")
    return {
        "runner_commit": commit,
        "expected_runner_commit": expected_commit,
        "branch": branch,
        "worktree_clean": not status,
        "source_sha256": {name: file_sha256(path) for name, path in paths.items()},
    }


def _extract_constants(tree: ast.Module) -> dict[str, Any]:
    constants: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not isinstance(node.value, ast.Constant):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _initial_system_prompt(tree: ast.Module, function_name: str) -> str:
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        raise ScreeningError(f"profile function inventory differs: {function_name}")
    for node in ast.walk(functions[0]):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "system" for target in targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
                break
    raise ScreeningError(f"profile prompt is not one initial literal: {function_name}")


def verify_profile_source(source: bytes, expected: dict[str, Any]) -> dict[str, Any]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ScreeningError("profile source is not valid UTF-8 Python") from exc
    constants = _extract_constants(tree)
    observed_versions = {
        key: constants.get(key) for key in expected["versions"]
    }
    prompt_hashes = {
        role: text_sha256(_initial_system_prompt(tree, function))
        for role, function in PROMPT_FUNCTIONS.items()
    }
    if observed_versions != expected["versions"]:
        raise ScreeningError("profile versions differ from the pinned plan")
    if prompt_hashes != expected["prompt_sha256"]:
        raise ScreeningError("profile prompt hashes differ from the pinned plan")
    return {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "versions": observed_versions,
        "prompt_sha256": prompt_hashes,
    }


def _operational_ast(source: bytes) -> str:
    tree = ast.parse(source.decode("utf-8"))
    removed = {"PROPOSER_PROMPT_VERSION": 0, "REVIEWER_PROMPT_VERSION": 0,
               "_proposer_prompts": 0, "_reviewer_prompts": 0}
    retained: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in removed:
            removed[node.name] += 1
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            matched = [name for name in names if name in removed]
            if matched:
                if len(matched) != 1 or len(names) != 1:
                    raise ScreeningError("profile version assignment shape differs")
                removed[matched[0]] += 1
                continue
        retained.append(node)
    if any(count != 1 for count in removed.values()):
        raise ScreeningError("profile AST removal inventory differs")
    tree.body = retained
    return ast.dump(tree, include_attributes=False)


def verify_and_stage_profiles(
    plan: dict[str, Any], private_dir: Path | None = None
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes], dict[str, Path]]:
    manifests: dict[str, dict[str, Any]] = {}
    sources: dict[str, bytes] = {}
    paths: dict[str, Path] = {}
    evidence_blobs: list[bytes] = []
    for profile in PROFILE_NAMES:
        expected = plan["profiles"][profile]
        commit = expected["commit"]
        if str(_git("rev-parse", f"{commit}^{{commit}}")).strip() != commit:
            raise ScreeningError(f"profile commit does not resolve exactly: {profile}")
        source = _git("show", f"{commit}:reply_strategy.py", text=False)
        evidence = _git("show", f"{commit}:reply_evidence.py", text=False)
        assert isinstance(source, bytes) and isinstance(evidence, bytes)
        manifest = verify_profile_source(source, expected)
        manifest.update({"profile": profile, "commit": commit})
        sources[profile] = source
        manifests[profile] = manifest
        evidence_blobs.append(evidence)
        if private_dir is not None:
            path = private_dir / "profile_sources" / f"{profile}_reply_strategy.py"
            atomic_bytes(path, source)
            paths[profile] = path
    checkout_evidence = (PROJECT_ROOT / "reply_evidence.py").read_bytes()
    if (
        any(blob != checkout_evidence for blob in evidence_blobs)
        or hashlib.sha256(checkout_evidence).hexdigest() != EVIDENCE_SHA256
    ):
        raise ScreeningError("reply_evidence.py differs across profiles or checkout")
    if _operational_ast(sources[PROFILE_NAMES[0]]) != _operational_ast(sources[PROFILE_NAMES[1]]):
        raise ScreeningError("profile modules contain a non-prompt operational difference")
    transport_checkout = (PROJECT_ROOT / "tools/pilot_ai_first_reply_strategy.py").read_bytes()
    transport_historical = _git(
        "show", f"{TRANSPORT_COMMIT}:tools/pilot_ai_first_reply_strategy.py", text=False
    )
    assert isinstance(transport_historical, bytes)
    if transport_checkout != transport_historical or hashlib.sha256(transport_checkout).hexdigest() != TRANSPORT_SHA256:
        raise ScreeningError("reviewed provider transport differs from its historical blob")
    return manifests, sources, paths


def load_isolated_profile(profile: str, path: Path) -> ModuleType:
    module_name = f"_reply_hybrid_{profile}_{file_sha256(path)[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ScreeningError(f"cannot load isolated profile: {profile}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _parse_sha256sums(path: Path) -> dict[str, str]:
    if file_sha256(path) != EXPECTED_SHA256SUMS_SHA256:
        raise ScreeningError("input SHA256SUMS hash differs")
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\x00]+)", line)
        if not match or match.group(2) in rows:
            raise ScreeningError("input SHA256SUMS format differs")
        rows[match.group(2)] = match.group(1)
    return rows


def _verify_component_hashes(row: dict[str, Any]) -> None:
    context = row["replay_context"]
    recent = [str(item["text"]) for item in row["recent_account_replies"]]
    expected = row["component_sha256"]
    observed = {
        "incoming_contribution_sha256": text_sha256(context["incoming_contribution"]),
        "quoted_post_sha256": value_sha256(context["quoted_post"]),
        "parent_thread_sha256": value_sha256(context["parent_thread"]),
        "clarification_request_sha256": value_sha256(context["clarification_request"]),
        "recent_account_replies_sha256": value_sha256(recent),
        "resolved_quotation_sha256": value_sha256(row["resolved_quotation"]),
        "replay_context_sha256": value_sha256(context),
        "production_context_at_candidate_sha256": value_sha256(
            row["production_context_at_candidate"]
        ),
    }
    if observed != expected:
        raise ScreeningError(f"stored component hash mismatch: {row.get('candidate_id')}")
    unhashed = dict(row)
    stored = unhashed.pop("context_audit_record_sha256", None)
    if stored != value_sha256(unhashed):
        raise ScreeningError(f"context-audit record hash mismatch: {row.get('candidate_id')}")


def _verify_source_inventory(source_inventory: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    corpus = source_inventory.get("quotation_corpus")
    if not isinstance(corpus, dict) or corpus.get("provider_or_network_lookup_used") is not False:
        raise ScreeningError("evidence-corpus inventory differs")
    corpus_path = Path(str(corpus.get("path") or ""))
    hashes = corpus.get("source_file_sha256")
    if (
        corpus_path != EXACT_CORPUS_PATH
        or corpus_path.is_symlink()
        or not corpus_path.is_dir()
        or not isinstance(hashes, dict)
        or not hashes
    ):
        raise ScreeningError("evidence-corpus path or hashes are unavailable")
    for relative, expected in hashes.items():
        if relative.startswith("implementation/"):
            path = PROJECT_ROOT / Path(relative).name
            committed_relative = Path(relative).name
        elif relative == "final_research_status.json":
            path = corpus_path / "final_unresolved/final_research_status.json"
            committed_relative = (
                "semantic_alignment_research/quote_research_full_001/"
                "final_unresolved/final_research_status.json"
            )
        else:
            path = corpus_path / relative
            committed_relative = (
                "semantic_alignment_research/quote_research_full_001/" + relative
            )
        if path.is_symlink() or not path.is_file() or file_sha256(path) != expected:
            raise ScreeningError(f"evidence-corpus source mismatch: {relative}")
        committed = _git("show", f"{BASE_COMMIT}:{committed_relative}", text=False)
        if not isinstance(committed, bytes) or hashlib.sha256(committed).hexdigest() != expected:
            raise ScreeningError(f"evidence-corpus Git binding mismatch: {relative}")
    factual = PROJECT_ROOT / "reply_factual_evidence.json"
    if file_sha256(factual) != "56e5e121b3ed089b0c9e095292d47432e1dc2e7ff9625e6fb0ec59fa1010b069":
        raise ScreeningError("factual evidence differs")
    identity = {
        "corpus_path": str(corpus_path),
        "source_file_sha256": hashes,
        "factual_evidence_sha256": file_sha256(factual),
        "reply_evidence_sha256": file_sha256(PROJECT_ROOT / "reply_evidence.py"),
    }
    identity["identity_sha256"] = value_sha256(identity)
    return corpus_path, identity


def verify_input_bundle(input_dir: Path) -> dict[str, Any]:
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise ScreeningError("input must be one regular directory")
    sums = _parse_sha256sums(input_dir / "SHA256SUMS")
    members = {
        path.name for path in input_dir.iterdir()
        if path.name != "SHA256SUMS"
    }
    if set(sums) != members:
        raise ScreeningError("complete input checksum membership differs")
    for name, expected in sums.items():
        path = input_dir / name
        if path.is_symlink() or not path.is_file() or file_sha256(path) != expected:
            raise ScreeningError(f"input checksum mismatch: {name}")
    if any(sums.get(name) != digest for name, digest in EXPECTED_INPUT_HASHES.items()):
        raise ScreeningError("required input member hash differs")

    inventory = read_jsonl_strict(input_dir / "fresh_candidate_inventory.jsonl")
    context_rows = read_jsonl_strict(input_dir / "context_audit.jsonl")
    inventory_ids = [str(row.get("candidate_id") or "") for row in inventory]
    if (
        len(inventory) != 36
        or len(set(inventory_ids)) != 36
        or value_sha256(sorted(inventory_ids)) != EXPECTED_CANDIDATE_IDS_SHA256
    ):
        raise ScreeningError("fresh 36-candidate membership differs")
    context_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in context_rows:
        context_by_id.setdefault(str(row.get("candidate_id") or ""), []).append(row)
    cases: list[ScreeningCase] = []
    historical_public_replies: dict[str, str | None] = {}
    for candidate_id in sorted(inventory_ids):
        inventory_row = next(row for row in inventory if row["candidate_id"] == candidate_id)
        joined = context_by_id.get(candidate_id) or []
        if len(joined) != 1:
            raise ScreeningError(f"candidate context join is not one-to-one: {candidate_id}")
        row = joined[0]
        if (
            inventory_row.get("replay_context") != row.get("replay_context")
            or inventory_row.get("component_sha256") != row.get("component_sha256")
            or inventory_row.get("context_audit_record_sha256")
            != row.get("context_audit_record_sha256")
        ):
            raise ScreeningError(f"inventory/context join payload differs: {candidate_id}")
        _verify_component_hashes(row)
        context = row["replay_context"]
        recent = [str(item["text"]) for item in row["recent_account_replies"]]
        if (
            not isinstance(context, dict)
            or len(recent) > 20
            or context.get("current_date") != "2026-08-12"
            or row.get("lane") != context.get("lane")
        ):
            raise ScreeningError(f"candidate execution context differs: {candidate_id}")
        cases.append(ScreeningCase(
            candidate_id=candidate_id,
            incoming_contribution=str(context["incoming_contribution"]),
            lane=str(context["lane"]),
            replay_context=json.loads(json.dumps(context)),
            recent_replies=recent,
            resolved_quotation=json.loads(json.dumps(row["resolved_quotation"])),
            context_hash=value_sha256(context),
            recent_replies_hash=value_sha256(recent),
            resolved_quotation_hash=value_sha256(row["resolved_quotation"]),
            context_audit_record_hash=str(row["context_audit_record_sha256"]),
        ))
        historical = inventory_row.get("historical_public_reply")
        if historical is not None and (not isinstance(historical, str) or not historical):
            raise ScreeningError(f"historical reply provenance is malformed: {candidate_id}")
        historical_public_replies[candidate_id] = historical

    summary = read_json(input_dir / "context_audit_summary.json")
    boundary = read_json(input_dir / "freshness_boundary.json")
    manifest = read_json(input_dir / "run_manifest.json")
    source_inventory = read_json(input_dir / "source_inventory.json")
    if (
        summary.get("candidate_counts", {}).get("eligible_candidates") != 36
        or summary.get("fresh_candidates_removed_for_text_duplication") != 0
        or boundary.get("development_cutoff") != "2026-08-10T05:16:15Z"
        or manifest.get("model_calls") != 0
        or manifest.get("provider_http_requests") != 0
        or manifest.get("posting_actions") != 0
        or manifest.get("production_state_changes") != 0
        or manifest.get("live_project_included") is not False
        or source_inventory.get("fresh_candidates_removed_for_text_duplication") != 0
        or source_inventory.get("fresh_history_reconstruction", {}).get("live_project_included") is not False
    ):
        raise ScreeningError("corrected audit aggregate contract differs")
    corpus_path, evidence_identity = _verify_source_inventory(source_inventory)
    repository = EvidenceRepository(
        corpus_path,
        factual_evidence_path=PROJECT_ROOT / "reply_factual_evidence.json",
    )
    for case in cases:
        resolved = repository.resolve_context_quotation(case.replay_context)
        if (
            value_sha256(resolved) != case.resolved_quotation_hash
            or (resolved or {}).get("quote_id") != (case.resolved_quotation or {}).get("quote_id")
            or (resolved or {}).get("resolved_context_hash")
            != (case.resolved_quotation or {}).get("resolved_context_hash")
        ):
            raise ScreeningError(f"quotation resolution mismatch: {case.candidate_id}")
    return {
        "cases": cases,
        "repository": repository,
        "corpus_path": corpus_path,
        "evidence_identity": evidence_identity,
        "historical_public_replies": historical_public_replies,
        "verification": {
            "input_directory": str(input_dir),
            "sha256sums_sha256": file_sha256(input_dir / "SHA256SUMS"),
            "member_sha256": sums,
            "candidate_count": len(cases),
            "candidate_ids_sha256": value_sha256(sorted(inventory_ids)),
            "context_join_count": len(cases),
            "quotation_resolution_count": sum(case.resolved_quotation is not None for case in cases),
            "development_cutoff": boundary["development_cutoff"],
        },
    }


def strategy_config(model: str, corpus_path: Path) -> dict[str, Any]:
    config = pilot.strategy_config(model, corpus_path)
    expected = {
        "enabled": True,
        "strategy_version": "ai-first-reply-v3",
        "proposer_model": model,
        "reviewer_model": model,
        "evidence_model": model,
        "research_corpus_path": str(corpus_path),
        "maximum_model_calls": 6,
        "proposer_timeout_seconds": 60,
        "evidence_timeout_seconds": 60,
        "reviewer_timeout_seconds": 60,
        "proposer_max_output_tokens": 900,
        "evidence_max_output_tokens": 1800,
        "reviewer_max_output_tokens": 900,
        "maximum_revisions": 1,
        "maximum_invalid_response_retries": 1,
        "maximum_claims": 6,
        "maximum_evidence_packets_per_claim": 6,
        "maximum_evidence_passages_per_claim": 24,
        "maximum_reply_sentences": 2,
        "fail_closed": True,
    }
    if config != expected:
        raise ScreeningError("reviewed production strategy configuration differs")
    return config


def hmac_key(seed: bytes, domain: str, *parts: str) -> str:
    if len(seed) != 32:
        raise ScreeningError("screening seeds must be exactly 32 bytes")
    message = "\0".join((domain, *parts)).encode("utf-8")
    return hmac.new(seed, message, hashlib.sha256).hexdigest()


def build_execution_plan(
    cases: Iterable[ScreeningCase], execution_seed: bytes, *, run_id: str, creation_time: str
) -> dict[str, Any]:
    ordered_cases = sorted(
        cases,
        key=lambda case: (hmac_key(execution_seed, "candidate-pair-order-v1", case.candidate_id), case.candidate_id),
    )
    executions: list[dict[str, Any]] = []
    for pair_index, case in enumerate(ordered_cases, 1):
        profiles = sorted(
            PROFILE_NAMES,
            key=lambda profile: (
                hmac_key(execution_seed, "profile-order-within-pair-v1", case.candidate_id, profile),
                profile,
            ),
        )
        for profile_index, profile in enumerate(profiles, 1):
            executions.append({
                "execution_index": len(executions) + 1,
                "pair_index": pair_index,
                "profile_index_within_pair": profile_index,
                "candidate_id": case.candidate_id,
                "profile": profile,
                "pair_creation_time": creation_time,
            })
    return {
        "schema_version": 1,
        "run_id": run_id,
        "candidate_count": len(ordered_cases),
        "planned_pipeline_executions": len(executions),
        "candidate_pair_adjacency": True,
        "no_case_replacement": True,
        "order_derivation": "HMAC-SHA256 with separate pair/profile domains",
        "executions": executions,
    }


def build_blind_assignments(
    cases: Iterable[ScreeningCase], response_label_seed: bytes
) -> dict[str, dict[str, str]]:
    assignments: dict[str, dict[str, str]] = {}
    for case in sorted(cases, key=lambda item: item.candidate_id):
        ordered = sorted(
            PROFILE_NAMES,
            key=lambda profile: (
                hmac_key(response_label_seed, "independent-response-label-v1", case.candidate_id, profile),
                profile,
            ),
        )
        assignments[case.candidate_id] = {"A": ordered[0], "B": ordered[1]}
    return assignments


def pipeline_execution_identity(
    *,
    run_id: str,
    case: ScreeningCase,
    profile: str,
    profile_manifest: dict[str, Any],
    evidence_identity: dict[str, Any],
    model: str,
    config_hash: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "candidate_id": case.candidate_id,
        "profile": profile,
        "profile_commit": profile_manifest["commit"],
        "context_hash": case.context_hash,
        "recent_account_replies_hash": case.recent_replies_hash,
        "resolved_quotation_hash": case.resolved_quotation_hash,
        "evidence_corpus_identity": evidence_identity["identity_sha256"],
        "model": model,
        "configuration_hash": config_hash,
    }


def execution_binding(identity: dict[str, Any]) -> dict[str, str]:
    digest = value_sha256(identity)
    return {
        "execution_identity": digest,
        "case_identity": f"{identity['candidate_id']}:{identity['profile']}:{digest}",
    }


class ReceiptTransport:
    """Record exact logical calls while delegating unchanged provider handling."""

    def __init__(
        self,
        delegate: pilot.PilotTransport,
        receipt_path: Path,
        existing: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.delegate = delegate
        self.receipt_path = receipt_path
        self.receipts = existing if existing is not None else {}
        self.identity: dict[str, Any] = {}
        self.binding: dict[str, str] = {}
        self.sequence = 0
        self.attempted_receipts: list[dict[str, Any]] = []

    def set_execution(self, identity: dict[str, Any]) -> None:
        self.identity = dict(identity)
        self.binding = execution_binding(identity)
        self.sequence = 0
        self.attempted_receipts = []
        self.delegate.set_case(self.binding["case_identity"])

    def __call__(self, **kwargs: Any) -> object:
        self.sequence += 1
        stage = str(kwargs["stage"])
        payload = {
            "model": kwargs["model"],
            "messages": [
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": kwargs["user_prompt"]},
            ],
            "temperature": 0,
            "max_tokens": kwargs["max_output_tokens"],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"ai_reply_{stage}",
                    "strict": True,
                    "schema": kwargs["response_schema"],
                },
            },
        }
        request_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        logical_call_id = f"{self.binding['case_identity']}:{self.sequence}:{stage}"
        receipt = {
            **self.identity,
            **self.binding,
            "stage": stage,
            "call_sequence": self.sequence,
            "logical_call_id": logical_call_id,
            "request_hash": request_hash,
            "system_prompt_sha256": text_sha256(kwargs["system_prompt"]),
            "user_prompt_sha256": text_sha256(kwargs["user_prompt"]),
            "response_schema_sha256": value_sha256(kwargs["response_schema"]),
            "temperature": 0,
            "max_output_tokens": kwargs["max_output_tokens"],
            "media_transmitted": False,
        }
        self.attempted_receipts.append(dict(receipt))
        prior_receipt = self.receipts.get(logical_call_id)
        if prior_receipt is not None and any(
            prior_receipt.get(key) != value for key, value in receipt.items()
        ):
            raise ScreeningError(f"prompt receipt changed on resume: {logical_call_id}")
        prior_operation = self.delegate.ledger.operation(logical_call_id, request_hash)
        try:
            response = self.delegate(**kwargs)
        except BaseException:
            operation = self.delegate.ledger.operation(logical_call_id, request_hash)
            if prior_receipt is None and operation is not None:
                stored = {**receipt, "transport_status": operation.get("status")}
                append_jsonl(self.receipt_path, stored)
                self.receipts[logical_call_id] = stored
            raise
        if prior_receipt is None:
            stored = {
                **receipt,
                "transport_status": (
                    "returned_from_completed_cache"
                    if prior_operation is not None and prior_operation.get("status") == "completed"
                    else "transmitted_and_completed"
                ),
            }
            append_jsonl(self.receipt_path, stored)
            self.receipts[logical_call_id] = stored
        return response


def _index_jsonl(path: Path, key_fields: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, Any]]:
    if not path.exists():
        return {}
    indexed: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in read_jsonl_strict(path):
        key = tuple(str(row.get(field) or "") for field in key_fields)
        if any(not value for value in key) or key in indexed:
            raise ScreeningError(f"duplicate or invalid durable record in {path.name}")
        indexed[key] = row
    return indexed


def _receipt_index(path: Path) -> dict[str, dict[str, Any]]:
    rows = _index_jsonl(path, ("logical_call_id",))
    return {key[0]: value for key, value in rows.items()}


def _operation_inventory(
    case_identity: str,
    ledger: pilot.PilotLedger,
    receipts: dict[str, dict[str, Any]],
    response_dir: Path,
    *,
    require_receipts: bool = True,
) -> dict[str, Any]:
    operations = [
        dict(row) for row in ledger.data.get("operations", [])
        if row.get("case_id") == case_identity
    ]
    operations.sort(key=lambda row: str(row.get("logical_call_id")))
    logical_ids = [str(row.get("logical_call_id") or "") for row in operations]
    if len(logical_ids) != len(set(logical_ids)):
        raise ScreeningError("duplicate logical call in durable ledger")
    matching_receipts: list[dict[str, Any]] = []
    for operation in operations:
        logical_id = str(operation["logical_call_id"])
        receipt = receipts.get(logical_id)
        if receipt is None:
            if require_receipts:
                raise ScreeningError(f"logical call lacks prompt receipt: {logical_id}")
        else:
            if (
                receipt.get("logical_call_id") != logical_id
                or receipt.get("request_hash") != operation.get("request_hash")
                or receipt.get("case_identity") != case_identity
            ):
                raise ScreeningError(f"prompt receipt differs from ledger: {logical_id}")
            matching_receipts.append(receipt)
        _verify_operation_cache(operation, response_dir)
    cost_ticks = sum(
        int(row.get("cost_in_usd_ticks") or 0)
        for row in operations if row.get("status") == "completed"
    )
    return {
        "logical_calls": operations,
        "prompt_receipts": matching_receipts,
        "logical_call_ids": logical_ids,
        "provider_request_ids": [
            str(row["request_id"]) for row in operations if row.get("request_id")
        ],
        "token_usage": {
            "input_tokens": sum(int(row.get("input_tokens") or 0) for row in operations),
            "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in operations),
            "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in operations),
            "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in operations),
        },
        "cost_in_usd_ticks": cost_ticks,
        "cost_usd": cost_ticks / pilot.USD_TICKS_PER_DOLLAR,
        "durable_response_cache_references": [
            f"provider_response_cache/{text_sha256(logical_id)}.json"
            for logical_id in logical_ids
            if any(
                operation.get("logical_call_id") == logical_id
                and (
                    operation.get("status") == "completed"
                    or (
                        operation.get("status") == "ambiguous"
                        and (
                            response_dir / f"{text_sha256(logical_id)}.json"
                        ).is_file()
                    )
                )
                for operation in operations
            )
        ],
    }


def _raw_usage(raw: dict[str, Any]) -> dict[str, int]:
    usage = raw.get("usage")
    if not isinstance(usage, dict) or type(usage.get("cost_in_usd_ticks")) is not int:
        raise ScreeningError("cached provider response lacks authoritative cost")
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "cached_tokens": int(prompt_details.get("cached_tokens") or usage.get("cached_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
        "cost_in_usd_ticks": usage["cost_in_usd_ticks"],
    }


def _verify_operation_cache(operation: dict[str, Any], response_dir: Path) -> None:
    logical_id = str(operation.get("logical_call_id") or "")
    status_value = operation.get("status")
    cache_path = response_dir / f"{text_sha256(logical_id)}.json"
    if status_value == "completed":
        if not cache_path.is_file() or cache_path.is_symlink():
            raise ScreeningError(f"completed logical call lacks response cache: {logical_id}")
        cached = read_json(cache_path)
        raw = cached.get("raw") if isinstance(cached, dict) else None
        if (
            cached.get("logical_call_id") != logical_id
            or cached.get("request_hash") != operation.get("request_hash")
            or not isinstance(raw, dict)
        ):
            raise ScreeningError(f"completed response cache identity differs: {logical_id}")
        usage = _raw_usage(raw)
        content = raw.get("choices", [{}])[0].get("message", {}).get("content")
        response_hash = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        expected = {
            "request_id": str(raw.get("id") or ""),
            "response_hash": response_hash,
            **usage,
        }
        if any(operation.get(key) != value for key, value in expected.items()):
            raise ScreeningError(f"completed cache accounting differs: {logical_id}")
    elif cache_path.exists():
        if (
            status_value not in {"sending", "ambiguous"}
            or not cache_path.is_file()
            or cache_path.is_symlink()
        ):
            raise ScreeningError(f"non-completed logical call has response cache: {logical_id}")
        cached = read_json(cache_path)
        raw = cached.get("raw") if isinstance(cached, dict) else None
        if (
            cached.get("logical_call_id") != logical_id
            or cached.get("request_hash") != operation.get("request_hash")
            or not isinstance(raw, dict)
        ):
            raise ScreeningError(f"recoverable response cache identity differs: {logical_id}")
        if status_value == "sending":
            _raw_usage(raw)


def classify_result(result: Any) -> dict[str, Any]:
    try:
        status = result.status
        reason = result.reason
        model_calls = result.model_call_count
        revisions = result.revision_count
        audit = list(result.audit)
        reply = result.reply
    except (AttributeError, TypeError) as exc:
        raise ScreeningError("pipeline returned a structurally inconsistent result") from exc
    if not isinstance(audit, list) or any(not isinstance(item, dict) for item in audit):
        raise ScreeningError("pipeline audit is structurally inconsistent")
    if (
        status == "approved"
        and isinstance(reply, str)
        and reply
        and isinstance(reason, str)
        and reason
        and type(model_calls) is int
        and 0 <= model_calls <= 6
        and type(revisions) is int
        and 0 <= revisions <= 1
    ):
        public_reply = str(reply)
        classified = "approved"
    elif (
        status == "no_reply"
        and reply is None
        and isinstance(reason, str)
        and reason
        and type(model_calls) is int
        and 0 <= model_calls <= 6
        and type(revisions) is int
        and 0 <= revisions <= 1
    ):
        public_reply = None
        classified = "no_reply"
    else:
        public_reply = None
        classified = "operational_failure"
        reason = str(reason or f"invalid_pipeline_status:{status}")
    return {
        "status": classified,
        "terminal_reason": reason,
        "public_reply": public_reply,
        "model_call_count": model_calls if type(model_calls) is int else None,
        "revision_count": revisions if type(revisions) is int else None,
        "pipeline_audit": audit,
        "pipeline_metadata": (
            json.loads(json.dumps(getattr(reply, "pipeline_metadata", None)))
            if reply is not None else None
        ),
        "approved_draft_record": (
            json.loads(json.dumps(getattr(reply, "draft_record", None)))
            if reply is not None else None
        ),
    }


def _resume_assessment(
    output: Path,
    ledger: pilot.PilotLedger,
    expected_run_identity: dict[str, Any],
    *,
    allowed_case_identities: set[str],
    terminal_failure_logical_ids: set[str],
    terminal_logical_ids: set[str],
    receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stored = read_json(output / "run_identity.json")
    if stored != expected_run_identity:
        raise ScreeningError("resume run identity differs")
    unsafe: list[str] = []
    response_dir = output / "private_audit/provider_response_cache"
    seen: set[str] = set()
    expected_cache_names: set[str] = set()
    for operation in ledger.data.get("operations", []):
        logical_id = operation.get("logical_call_id")
        if not isinstance(logical_id, str) or not logical_id or logical_id in seen:
            unsafe.append("duplicate_or_invalid_logical_call")
            continue
        seen.add(logical_id)
        if operation.get("case_id") not in allowed_case_identities:
            unsafe.append(f"unknown_case_identity:{logical_id}")
        request_hash = operation.get("request_hash")
        if not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash):
            unsafe.append(f"invalid_request_hash:{logical_id}")
        status_value = operation.get("status")
        cache = response_dir / f"{text_sha256(logical_id)}.json"
        try:
            _verify_operation_cache(operation, response_dir)
        except ScreeningError as exc:
            unsafe.append(str(exc))
        if cache.is_file():
            expected_cache_names.add(cache.name)
        if status_value == "completed":
            pass
        elif status_value == "prepared":
            if logical_id in terminal_logical_ids:
                unsafe.append(f"terminal_prepared_operation:{logical_id}")
        elif status_value == "sending":
            if not cache.is_file():
                unsafe.append(f"ambiguous_sending_without_cache:{logical_id}")
        elif status_value in {"rate_limited", "server_error", "http_error"}:
            if logical_id not in terminal_failure_logical_ids:
                unsafe.append(f"unterminated_definite_failure:{logical_id}:{status_value}")
        else:
            unsafe.append(f"unsafe_operation:{logical_id}:{status_value}")
        receipt = receipts.get(logical_id)
        if receipt is None and logical_id in terminal_logical_ids:
            unsafe.append(f"terminal_operation_missing_receipt:{logical_id}")
        elif receipt is not None and (
            receipt.get("request_hash") != request_hash
            or receipt.get("case_identity") != operation.get("case_id")
        ):
            unsafe.append(f"receipt_ledger_mismatch:{logical_id}")
    for logical_id, receipt in receipts.items():
        if logical_id not in seen:
            unsafe.append(f"orphan_prompt_receipt:{logical_id}")
        if receipt.get("logical_call_id") != logical_id:
            unsafe.append(f"receipt_identity_mismatch:{logical_id}")
    actual_cache_names = {
        path.name for path in response_dir.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if actual_cache_names != expected_cache_names:
        unsafe.append("provider_response_cache_membership_mismatch")

    known = sum(
        int(row.get("cost_in_usd_ticks") or 0)
        for row in ledger.data.get("operations", []) if row.get("status") == "completed"
    )
    ambiguous = sum(
        int(row.get("maximum_possible_cost_ticks") or 0)
        for row in ledger.data.get("operations", []) if row.get("status") == "ambiguous"
    )
    if (
        ledger.data.get("known_cost_in_usd_ticks") != known
        or ledger.data.get("ambiguous_exposure_in_usd_ticks") != ambiguous
        or ledger.data.get("known_cost_usd") != known / pilot.USD_TICKS_PER_DOLLAR
        or ledger.data.get("ambiguous_exposure_usd") != ambiguous / pilot.USD_TICKS_PER_DOLLAR
        or ledger.data.get("combined_exposure_usd")
        != (known + ambiguous) / pilot.USD_TICKS_PER_DOLLAR
        or known + ambiguous > int(HARD_LIMIT_USD * pilot.USD_TICKS_PER_DOLLAR)
    ):
        unsafe.append("cost_ledger_aggregate_mismatch")
    if ledger.data.get("blocked"):
        unsafe.append(f"ledger_blocked:{ledger.data.get('blocked_reason')}")
    assessment = {"resume_safe": not unsafe, "unsafe_reasons": unsafe}
    write_json(output / "private_audit/resume_assessment.json", assessment)
    if unsafe:
        raise ScreeningError("resume_safe=false: " + ";".join(unsafe))
    return assessment


def _validate_provider_metadata(metadata: Any, model: str) -> dict[str, Any]:
    fields = {
        "model", "retrieved_at", "usd_ticks_per_dollar",
        "prompt_text_token_price", "cached_prompt_text_token_price",
        "completion_text_token_price",
    }
    if not isinstance(metadata, dict) or set(metadata) != fields or metadata.get("model") != model:
        raise ScreeningError("provider model metadata differs")
    if metadata.get("usd_ticks_per_dollar") != pilot.USD_TICKS_PER_DOLLAR:
        raise ScreeningError("provider currency scale differs")
    for name in fields - {"model", "retrieved_at", "usd_ticks_per_dollar"}:
        if type(metadata.get(name)) is not int or metadata[name] <= 0:
            raise ScreeningError("provider authoritative price is unavailable")
    return metadata


def _validate_terminal_record(
    record: dict[str, Any],
    *,
    expected_identity: dict[str, Any],
    profile_manifest: dict[str, Any],
    ledger: pilot.PilotLedger,
    receipts: dict[str, dict[str, Any]],
    response_dir: Path,
) -> None:
    binding = execution_binding(expected_identity)
    fixed = {
        "candidate_id": expected_identity["candidate_id"],
        "profile": expected_identity["profile"],
        "profile_commit": profile_manifest["commit"],
        "execution_identity": binding["execution_identity"],
        "case_identity": binding["case_identity"],
        "profile_prompt_versions": profile_manifest["versions"],
        "profile_prompt_sha256": profile_manifest["prompt_sha256"],
    }
    if any(record.get(key) != value for key, value in fixed.items()):
        raise ScreeningError(
            f"terminal execution identity differs: {expected_identity['candidate_id']}:"
            f"{expected_identity['profile']}"
        )
    if record.get("status") not in {"approved", "no_reply", "operational_failure"}:
        raise ScreeningError("terminal execution status differs")
    if not isinstance(record.get("terminal_reason"), str) or not record["terminal_reason"]:
        raise ScreeningError("terminal execution reason is missing")
    if type(record.get("model_call_count")) is not int or not 0 <= record["model_call_count"] <= 6:
        raise ScreeningError("terminal model-call count differs")
    if type(record.get("revision_count")) is not int or not 0 <= record["revision_count"] <= 1:
        raise ScreeningError("terminal revision count differs")
    if type(record.get("attempted")) is not bool:
        raise ScreeningError("terminal attempted flag differs")
    attempted_receipts = record.get("attempted_prompt_receipts")
    if (
        not isinstance(attempted_receipts, list)
        or any(not isinstance(item, dict) for item in attempted_receipts)
        or len(attempted_receipts) != record["model_call_count"]
    ):
        raise ScreeningError("terminal attempted prompt inventory differs")
    if record["status"] == "approved":
        if not isinstance(record.get("public_reply"), str) or not record["public_reply"]:
            raise ScreeningError("approved terminal record lacks public reply")
    elif record.get("public_reply") is not None:
        raise ScreeningError("non-approved terminal record contains a public reply")
    if not isinstance(record.get("pipeline_audit"), list):
        raise ScreeningError("terminal pipeline audit differs")
    inventory = _operation_inventory(
        binding["case_identity"], ledger, receipts, response_dir
    )
    for key, value in inventory.items():
        if record.get(key) != value:
            raise ScreeningError(f"terminal provider accounting differs: {key}")
    receipt_bases = {
        logical_id: {
            key: value for key, value in receipt.items() if key != "transport_status"
        }
        for logical_id, receipt in receipts.items()
        if receipt.get("case_identity") == binding["case_identity"]
    }
    for attempted in attempted_receipts:
        logical_id = attempted.get("logical_call_id")
        persisted = receipt_bases.get(str(logical_id))
        if persisted is not None and attempted != persisted:
            raise ScreeningError("attempted prompt differs from durable receipt")
    if record["attempted"] is False and (
        record["model_call_count"] != 0
        or inventory["logical_call_ids"]
        or attempted_receipts
    ):
        raise ScreeningError("unattempted execution owns provider calls")
    if record["status"] in {"approved", "no_reply"}:
        if (
            record["model_call_count"] != len(inventory["logical_call_ids"])
            or any(row.get("status") != "completed" for row in inventory["logical_calls"])
        ):
            raise ScreeningError("completed outcome has inconsistent model-call accounting")


def _validate_record_and_ledger_union(
    records: dict[tuple[str, str], dict[str, Any]],
    ledger: pilot.PilotLedger,
    receipts: dict[str, dict[str, Any]],
) -> None:
    record_calls = [
        logical_id
        for record in records.values()
        for logical_id in record.get("logical_call_ids", [])
    ]
    ledger_calls = [
        str(row.get("logical_call_id") or "") for row in ledger.data.get("operations", [])
    ]
    if len(record_calls) != len(set(record_calls)) or set(record_calls) != set(ledger_calls):
        raise ScreeningError("terminal records do not exactly cover the durable ledger")
    if set(receipts) != set(ledger_calls):
        raise ScreeningError("prompt receipts do not exactly cover the durable ledger")


def _review_projection(case: ScreeningCase, response_a: str, response_b: str) -> dict[str, Any]:
    context = case.replay_context
    return {
        "candidate_id": case.candidate_id,
        "incoming_contribution": case.incoming_contribution,
        "lane": case.lane,
        "quoted_post_context": context["quoted_post"],
        "bounded_parent_thread": context["parent_thread"],
        "clarification_context": context["clarification_request"],
        "current_date": context["current_date"],
        "recent_account_replies": list(case.recent_replies),
        "resolved_quotation_metadata": case.resolved_quotation,
        "response_A": response_a,
        "response_B": response_b,
    }


def render_completed_outcome(record: dict[str, Any]) -> str:
    if record.get("status") == "approved" and isinstance(record.get("public_reply"), str) and record["public_reply"]:
        return record["public_reply"]
    if record.get("status") == "no_reply" and record.get("public_reply") is None:
        return "[NO_REPLY]"
    raise ScreeningError("operational failure cannot be rendered as a completed response")


def _markdown_quote(value: Any) -> str:
    if value is None:
        return "_(none)_"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        text = str(value)
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def build_review_pack(
    cases: list[ScreeningCase],
    records: dict[tuple[str, str], dict[str, Any]],
    assignments: dict[str, dict[str, str]],
    *,
    historical_public_replies: dict[str, str | None] | None = None,
) -> tuple[dict[str, bytes], list[str]]:
    markdown = ["# Blinded reply screening review", ""]
    csv_buffer = io.StringIO(newline="")
    fields = [
        "candidate_id", "incoming_contribution", "lane", "quoted_post_context",
        "bounded_parent_thread", "clarification_context", "current_date",
        "recent_account_replies", "resolved_quotation_metadata", "response_A", "response_B",
        *REVIEWER_FIELDS,
    ]
    writer = csv.DictWriter(csv_buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    unscorable: list[str] = []
    for case in sorted(cases, key=lambda item: item.candidate_id):
        by_profile = {profile: records.get((case.candidate_id, profile)) for profile in PROFILE_NAMES}
        if any(record is None or record.get("status") == "operational_failure" for record in by_profile.values()):
            unscorable.append(case.candidate_id)
            continue
        mapping = assignments[case.candidate_id]
        projection = _review_projection(
            case,
            render_completed_outcome(by_profile[mapping["A"]]),
            render_completed_outcome(by_profile[mapping["B"]]),
        )
        historical_reply = (historical_public_replies or {}).get(case.candidate_id)
        if historical_reply and historical_reply.encode("utf-8") in canonical_json_bytes(projection):
            raise ScreeningError(
                f"candidate historical public reply leaked into blind projection: {case.candidate_id}"
            )
        writer.writerow({
            **projection,
            "quoted_post_context": json.dumps(projection["quoted_post_context"], ensure_ascii=False, sort_keys=True),
            "bounded_parent_thread": json.dumps(projection["bounded_parent_thread"], ensure_ascii=False, sort_keys=True),
            "clarification_context": json.dumps(projection["clarification_context"], ensure_ascii=False, sort_keys=True),
            "recent_account_replies": json.dumps(projection["recent_account_replies"], ensure_ascii=False),
            "resolved_quotation_metadata": json.dumps(projection["resolved_quotation_metadata"], ensure_ascii=False, sort_keys=True),
            **{field: "" for field in REVIEWER_FIELDS},
        })
        markdown.extend([
            f"## {projection['candidate_id']}", "",
            f"Lane: {projection['lane']}", "", "Incoming contribution:", "",
            _markdown_quote(projection["incoming_contribution"]), "",
            "Quoted-post context:", "", _markdown_quote(projection["quoted_post_context"]), "",
            "Bounded parent thread:", "", _markdown_quote(projection["bounded_parent_thread"]), "",
            "Clarification context:", "", _markdown_quote(projection["clarification_context"]), "",
            f"Current date: {projection['current_date']}", "", "Recent-account replies:", "",
            _markdown_quote(projection["recent_account_replies"]), "",
            "Resolved-quotation metadata:", "", _markdown_quote(projection["resolved_quotation_metadata"]), "",
            "### Response A", "", projection["response_A"], "",
            "### Response B", "", projection["response_B"], "",
            "Reviewer fields are blank in the CSV.", "",
        ])
    instructions = (
        "# Review instructions\n\n"
        "Review only the paired cases in the blinded Markdown or CSV. Record one "
        "pair_decision of A_wins, B_wins, tie, or unscorable, and complete the blank "
        "safety/error fields. Do not attempt to infer profile identity. Cases listed "
        "in blind_unscorable_cases.json are excluded because at least one pipeline "
        "execution failed operationally; no partial output is disclosed.\n"
    )
    files = {
        "blind_quality_review.md": "\n".join(markdown).encode("utf-8"),
        "blind_quality_review.csv": csv_buffer.getvalue().encode("utf-8"),
        "blind_unscorable_cases.json": json_document_bytes({"candidate_ids": unscorable}),
        "review_instructions.md": instructions.encode("utf-8"),
    }
    manifest = {
        "schema_version": 1,
        "screening_only": True,
        "paired_case_count": len(cases) - len(unscorable),
        "unscorable_candidate_count": len(unscorable),
        "candidate_ids_sha256": value_sha256(sorted(case.candidate_id for case in cases)),
        "projection": [
            "candidate_id", "incoming_contribution", "lane", "quoted_post_context",
            "bounded_parent_thread", "clarification_context", "current_date",
            "recent_account_replies", "resolved_quotation_metadata", "response_A", "response_B",
        ],
        "allowed_pair_decisions": ["A_wins", "B_wins", "tie", "unscorable"],
        "file_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files["review_pack_manifest.json"] = json_document_bytes(manifest)
    sums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items()
    }
    files["SHA256SUMS"] = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(sums.items())
    ).encode("utf-8")
    return files, unscorable


def _write_review_pack(directory: Path, files: dict[str, bytes]) -> None:
    if set(files) != set(REVIEW_FILES):
        raise ScreeningError("review pack file inventory differs")
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise ScreeningError("stored review pack path differs")
        existing = {path.name for path in directory.iterdir()}
        if existing - set(files):
            raise ScreeningError("stored review pack file inventory differs")
        for name, content in files.items():
            path = directory / name
            if path.exists() and (path.is_symlink() or not path.is_file()):
                raise ScreeningError(f"stored review pack path differs: {name}")
            if not path.exists() or path.read_bytes() != content:
                atomic_bytes(path, content)
        return
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, content in files.items():
        atomic_bytes(directory / name, content)


def _forbidden_review_values(
    plan: dict[str, Any], manifests: dict[str, Any], execution_seed: bytes,
    response_seed: bytes, api_key: str,
) -> list[bytes]:
    values: set[str] = {execution_seed.hex(), response_seed.hex(), api_key}
    values.update(PROFILE_NAMES)
    values.update({
        "hardened current", "Hardened current", "hardened-current",
        "conservative hybrid", "Conservative hybrid", "conservative-hybrid",
    })
    for profile in PROFILE_NAMES:
        values.add(plan["profiles"][profile]["commit"])
        values.update(str(value) for value in manifests[profile]["prompt_sha256"].values())
        values.update(
            str(value) for value in manifests[profile]["versions"].values()
            if isinstance(value, str) and len(value) >= 8
        )
    values.update({
        "cost_in_usd_ticks", "cost_usd", "provider_request_ids", "retry_count",
        "rate_limit_events", "server_error_events", "historical_public_reply",
        "historical_outcome",
    })
    return [value.encode("utf-8") for value in values if value]


def assert_blind_pack_clean(directory: Path, forbidden: Iterable[bytes]) -> None:
    for path in directory.iterdir():
        if not path.is_file() or path.is_symlink():
            raise ScreeningError("review pack contains a non-regular file")
        content = path.read_bytes()
        for value in forbidden:
            if value and value in content:
                raise ScreeningError(f"private value leaked into review pack: {path.name}")


def assert_no_credentials(root: Path, api_key: str) -> None:
    forbidden = [b"Authorization:", b"Bearer "]
    if api_key:
        forbidden.append(api_key.encode("utf-8"))
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            content = path.read_bytes()
            if any(value in content for value in forbidden):
                raise ScreeningError(f"credential material leaked into {path}")


def _checksum_tree(directory: Path) -> dict[str, str]:
    hashes = {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"
    }
    atomic_bytes(
        directory / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())).encode("utf-8"),
    )
    return hashes


def verify_private_tree(root: Path) -> None:
    if stat.S_IMODE(root.stat().st_mode) != 0o700 or root.is_symlink():
        raise ScreeningError("run directory permissions differ")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ScreeningError("run directory contains a symlink")
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = 0o700 if path.is_dir() else 0o600
        if mode != expected:
            raise ScreeningError(f"private path mode differs: {path}")


def create_deterministic_archive(source: Path, destination: Path) -> str:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_handle:
            environment = dict(os.environ)
            environment["TZ"] = "UTC"
            tar_process = subprocess.Popen(
                [
                    "tar", "--sort=name", "--format=posix",
                    "--mtime=UTC 1970-01-01", "--owner=0", "--group=0",
                    "--numeric-owner", "-cf", "-", "-C", str(source), ".",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            assert tar_process.stdout is not None
            gzip_process = subprocess.Popen(
                ["gzip", "-n"],
                stdin=tar_process.stdout,
                stdout=output_handle,
                stderr=subprocess.PIPE,
                env=environment,
            )
            tar_process.stdout.close()
            gzip_stderr = gzip_process.communicate()[1]
            tar_stderr = tar_process.communicate()[1]
            if tar_process.returncode != 0 or gzip_process.returncode != 0:
                detail = (tar_stderr + gzip_stderr).decode("utf-8", "replace").strip()
                raise ScreeningError(f"deterministic archive creation failed: {detail}")
            output_handle.flush()
            os.fsync(output_handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return file_sha256(destination)


def publish_deterministic_archive(source: Path, destination: Path) -> str:
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{destination.name}.candidate.", dir=destination.parent
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    candidate.unlink()
    try:
        digest = create_deterministic_archive(source, candidate)
        if destination.exists():
            existing_matches = (
                not destination.is_symlink()
                and destination.is_file()
                and stat.S_IMODE(destination.stat().st_mode) == 0o600
                and file_sha256(destination) == digest
            )
            if existing_matches:
                candidate.unlink()
            else:
                if destination.is_symlink() or not destination.is_file():
                    raise ScreeningError(f"stored archive path differs: {destination}")
                os.replace(candidate, destination)
                destination.chmod(0o600)
        else:
            os.replace(candidate, destination)
            destination.chmod(0o600)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return digest
    finally:
        candidate.unlink(missing_ok=True)


def verify_archive(
    path: Path,
    *,
    api_key: str,
    exact_files: set[str] | None = None,
) -> None:
    forbidden = [b"Authorization:", b"Bearer "]
    if api_key:
        forbidden.append(api_key.encode("utf-8"))
    names: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                normalized = member.name.removeprefix("./")
                if normalized in {"", "."}:
                    continue
                if member.issym() or member.islnk():
                    raise ScreeningError(f"archive contains a link: {path}")
                if member.isfile():
                    names.add(normalized)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ScreeningError(f"archive file cannot be read: {path}")
                    content = extracted.read()
                    if any(value in content for value in forbidden):
                        raise ScreeningError(f"credential material leaked into archive: {path}")
                    if member.mode != 0o600:
                        raise ScreeningError(f"archive file mode differs: {path}:{normalized}")
                elif member.isdir() and member.mode != 0o700:
                    raise ScreeningError(f"archive directory mode differs: {path}:{normalized}")
    except (OSError, tarfile.TarError) as exc:
        raise ScreeningError(f"invalid deterministic archive: {path}") from exc
    if exact_files is not None and names != exact_files:
        raise ScreeningError(f"archive member inventory differs: {path}")


def _run_basis(
    *, provenance: dict[str, Any], input_verification: dict[str, Any],
    manifests: dict[str, Any], evidence_identity: dict[str, Any], model: str,
    config: dict[str, Any], execution_seed_commitment: str,
    response_seed_commitment: str,
) -> dict[str, Any]:
    return {
        "runner_version": RUNNER_VERSION,
        "runner_commit": provenance["runner_commit"],
        "input_sha256sums_sha256": input_verification["sha256sums_sha256"],
        "candidate_ids_sha256": input_verification["candidate_ids_sha256"],
        "profile_manifests_sha256": value_sha256(manifests),
        "evidence_identity_sha256": evidence_identity["identity_sha256"],
        "model": model,
        "configuration_sha256": value_sha256(config),
        "hard_cost_limit_usd": HARD_LIMIT_USD,
        "execution_order_seed_commitment": execution_seed_commitment,
        "response_label_seed_commitment": response_seed_commitment,
        "screening_only": True,
    }


def _safe_exception_reason(prefix: str, exc: Exception, api_key: str) -> str:
    detail = str(exc).replace("\x00", "")
    if api_key:
        detail = detail.replace(api_key, "[REDACTED]")
    detail = detail.replace("Authorization:", "[REDACTED_HEADER]:")
    detail = detail.replace("Bearer ", "[REDACTED_BEARER] ")
    if len(detail) > 1000:
        detail = detail[:1000] + "…"
    return f"{prefix}:{type(exc).__name__}:{detail}"


def _exception_classification(
    prefix: str,
    exc: Exception,
    transport: ReceiptTransport,
    api_key: str,
) -> dict[str, Any]:
    reason = _safe_exception_reason(prefix, exc, api_key)
    revision_count = int(any(
        str(receipt.get("stage") or "").startswith("revision_")
        for receipt in transport.attempted_receipts
    ))
    return {
        "status": "operational_failure",
        "terminal_reason": reason,
        "public_reply": None,
        "model_call_count": transport.sequence,
        "revision_count": revision_count,
        "pipeline_audit": [{
            "stage": "runner_exception",
            "status": "operational_failure",
            "reason": reason,
        }],
        "pipeline_metadata": None,
        "approved_draft_record": None,
    }


def execute_screening(
    args: argparse.Namespace,
    verified: dict[str, Any],
    plan_spec: dict[str, Any],
    provenance: dict[str, Any],
    manifests: dict[str, Any],
    profile_sources: dict[str, bytes],
    *,
    get: Callable[..., Any] = pilot.requests.get,
    post: Callable[..., Any] = pilot.requests.post,
) -> dict[str, Any]:
    requested_output = args.output.absolute()
    if requested_output.is_symlink():
        raise ScreeningError("run output must not be a symlink")
    output = requested_output.resolve(strict=False)
    if output == Path("/tmp") or Path("/tmp") in output.parents:
        raise ScreeningError("run output must not be under /tmp")
    if args.resume:
        if not output.is_dir():
            raise ScreeningError("resume requires the same existing output directory")
        verify_private_tree(output)
        private = output / "private_audit"
        seeds_doc = read_json(private / "private_seeds.json")
        execution_seed = bytes.fromhex(str(seeds_doc["execution_order_seed_hex"]))
        response_seed = bytes.fromhex(str(seeds_doc["response_label_seed_hex"]))
        creation_time = str(seeds_doc["pair_creation_time"])
    else:
        if output.exists():
            raise ScreeningError("new execution output already exists")
        output.mkdir(mode=0o700, parents=False)
        output.chmod(0o700)
        private = output / "private_audit"
        private.mkdir(mode=0o700)
        execution_seed = secrets.token_bytes(32)
        response_seed = secrets.token_bytes(32)
        while response_seed == execution_seed:
            response_seed = secrets.token_bytes(32)
        creation_time = utc_now()
        write_json(private / "private_seeds.json", {
            "execution_order_seed_hex": execution_seed.hex(),
            "response_label_seed_hex": response_seed.hex(),
            "pair_creation_time": creation_time,
        })
    if len(execution_seed) != 32 or len(response_seed) != 32 or execution_seed == response_seed:
        raise ScreeningError("private seed state differs")

    source_dir = private / "profile_sources"
    source_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged_paths: dict[str, Path] = {}
    for profile, source in profile_sources.items():
        path = source_dir / f"{profile}_reply_strategy.py"
        if path.exists() and path.read_bytes() != source:
            raise ScreeningError("staged profile source changed")
        if not path.exists():
            atomic_bytes(path, source)
        staged_paths[profile] = path
    modules = {profile: load_isolated_profile(profile, staged_paths[profile]) for profile in PROFILE_NAMES}

    config = strategy_config(args.model, verified["corpus_path"])
    execution_commitment = hashlib.sha256(execution_seed).hexdigest()
    response_commitment = hashlib.sha256(response_seed).hexdigest()
    basis = _run_basis(
        provenance=provenance,
        input_verification=verified["verification"],
        manifests=manifests,
        evidence_identity=verified["evidence_identity"],
        model=args.model,
        config=config,
        execution_seed_commitment=execution_commitment,
        response_seed_commitment=response_commitment,
    )
    run_id = value_sha256(basis)
    execution_plan = build_execution_plan(
        verified["cases"], execution_seed, run_id=run_id, creation_time=creation_time
    )
    assignments = build_blind_assignments(verified["cases"], response_seed)
    run_identity = {
        **basis,
        "run_id": run_id,
        "execution_plan_sha256": value_sha256(execution_plan),
        "maximum_rate_limit_retries": args.maximum_rate_limit_retries,
        "maximum_server_error_retries": args.maximum_server_error_retries,
        "xai_base": args.xai_base,
    }
    blind_key_document = {
        "derivation": "independent HMAC-SHA256 response-label seed",
        "assignments": assignments,
    }
    if not args.resume:
        write_json(output / "run_identity.json", run_identity)
        write_json(private / "run_identity.json", run_identity)
        write_json(private / "input_verification.json", verified["verification"])
        write_json(private / "profile_manifests.json", manifests)
        write_json(private / "evidence_corpus_identity.json", verified["evidence_identity"])
        write_json(private / "execution_plan.json", execution_plan)
        write_json(private / "blind_key.json", blind_key_document)
        write_text(private / "prompt_receipts.jsonl", "")
        write_text(private / "execution_records.jsonl", "")
        (private / "provider_response_cache").mkdir(mode=0o700)
    else:
        expected_documents = {
            output / "run_identity.json": run_identity,
            private / "run_identity.json": run_identity,
            private / "input_verification.json": verified["verification"],
            private / "profile_manifests.json": manifests,
            private / "evidence_corpus_identity.json": verified["evidence_identity"],
            private / "execution_plan.json": execution_plan,
            private / "blind_key.json": blind_key_document,
        }
        for path, expected in expected_documents.items():
            if not path.is_file() or read_json(path) != expected:
                raise ScreeningError(f"resume durable source differs: {path.name}")
    verify_private_tree(output)

    ledger_path = private / "cost_ledger.json"
    if args.resume and not ledger_path.is_file():
        raise ScreeningError("resume_safe=false: durable cost ledger is missing")
    if args.resume:
        ledger_document = read_json(ledger_path)
        if ledger_document.get("blocked"):
            raise ScreeningError(
                "resume_safe=false: durable cost ledger is blocked: "
                + str(ledger_document.get("blocked_reason") or "unknown")
            )
    ledger = pilot.PilotLedger(
        ledger_path,
        model=args.model,
        hard_limit_usd=HARD_LIMIT_USD,
        run_version=f"{RUNNER_VERSION}:{run_id}",
    )
    receipts = _receipt_index(private / "prompt_receipts.jsonl")
    records = _index_jsonl(private / "execution_records.jsonl", ("candidate_id", "profile"))
    case_by_id = {case.candidate_id: case for case in verified["cases"]}
    expected_executions: dict[tuple[str, str], tuple[dict[str, Any], dict[str, str]]] = {}
    for planned in execution_plan["executions"]:
        case = case_by_id[planned["candidate_id"]]
        identity = pipeline_execution_identity(
            run_id=run_id,
            case=case,
            profile=planned["profile"],
            profile_manifest=manifests[planned["profile"]],
            evidence_identity=verified["evidence_identity"],
            model=args.model,
            config_hash=value_sha256(config),
        )
        key = (case.candidate_id, planned["profile"])
        if key in expected_executions:
            raise ScreeningError("execution plan repeats a candidate/profile identity")
        expected_executions[key] = (identity, execution_binding(identity))
    unknown_records = set(records) - set(expected_executions)
    if unknown_records:
        raise ScreeningError("execution journal contains an unplanned identity")
    response_dir = private / "provider_response_cache"
    for key, record in records.items():
        identity, _binding = expected_executions[key]
        _validate_terminal_record(
            record,
            expected_identity=identity,
            profile_manifest=manifests[key[1]],
            ledger=ledger,
            receipts=receipts,
            response_dir=response_dir,
        )
    terminal_logical_ids = {
        logical_id for record in records.values()
        for logical_id in record.get("logical_call_ids", [])
    }
    terminal_failure_logical_ids = {
        logical_id for record in records.values()
        if record.get("status") == "operational_failure"
        for logical_id in record.get("logical_call_ids", [])
    }
    if args.resume:
        _resume_assessment(
            output,
            ledger,
            run_identity,
            allowed_case_identities={binding["case_identity"] for _identity, binding in expected_executions.values()},
            terminal_failure_logical_ids=terminal_failure_logical_ids,
            terminal_logical_ids=terminal_logical_ids,
            receipts=receipts,
        )

    global_block_path = private / "global_block.json"
    prior_global_kind: str | None = None
    prior_global_reason: str | None = None
    if global_block_path.exists():
        prior_global = read_json(global_block_path)
        prior_global_kind = str(prior_global.get("kind") or "") or None
        prior_global_reason = str(prior_global.get("reason") or "") or None
        if prior_global_kind is None or prior_global_reason is None:
            raise ScreeningError("stored global blocker is invalid")

    metadata_path = private / "provider_model_metadata.json"
    phase_path = private / "provider_phase_identity.json"
    metadata: dict[str, Any] | None = None
    provider_setup_failure: str | None = None
    if metadata_path.exists() or phase_path.exists():
        if not metadata_path.is_file() or not phase_path.is_file():
            raise ScreeningError("provider phase state is incomplete")
        metadata = _validate_provider_metadata(read_json(metadata_path), args.model)
        expected_phase = {
            "run_id": run_id,
            "run_identity_sha256": file_sha256(output / "run_identity.json"),
            "provider_model_metadata_sha256": file_sha256(metadata_path),
            "model": args.model,
            "xai_base": args.xai_base,
            "prices": {
                key: metadata[key] for key in (
                    "prompt_text_token_price", "cached_prompt_text_token_price",
                    "completion_text_token_price",
                )
            },
        }
        if read_json(phase_path) != expected_phase:
            raise ScreeningError("provider phase identity changed")
        if (
            args.resume
            and set(records) != set(expected_executions)
            and prior_global_kind is None
        ):
            try:
                current_metadata = _validate_provider_metadata(
                    pilot.fetch_model_metadata(
                        api_key=args.api_key,
                        base_url=args.xai_base,
                        model=args.model,
                        get=get,
                    ),
                    args.model,
                )
                stable_fields = {
                    "model", "usd_ticks_per_dollar", "prompt_text_token_price",
                    "cached_prompt_text_token_price", "completion_text_token_price",
                }
                if any(current_metadata[field] != metadata[field] for field in stable_fields):
                    provider_setup_failure = "provider_model_metadata_changed_on_resume"
                write_json(private / "resume_model_metadata_verification.json", {
                    "stable_metadata_match": provider_setup_failure is None,
                    "stored_metadata_sha256": file_sha256(metadata_path),
                    "current_metadata_sha256": value_sha256(current_metadata),
                    "verified_at": utc_now(),
                })
            except Exception as exc:
                provider_setup_failure = _safe_exception_reason(
                    "provider_metadata_resume_verification_failed", exc, args.api_key
                )
    else:
        if ledger.data.get("operations"):
            raise ScreeningError("provider metadata is missing after billed operations")
        if prior_global_kind is not None:
            metadata = None
        else:
            try:
                metadata = _validate_provider_metadata(
                    pilot.fetch_model_metadata(
                        api_key=args.api_key, base_url=args.xai_base, model=args.model, get=get
                    ),
                    args.model,
                )
                write_json(metadata_path, metadata)
                write_json(phase_path, {
                    "run_id": run_id,
                    "run_identity_sha256": file_sha256(output / "run_identity.json"),
                    "provider_model_metadata_sha256": file_sha256(metadata_path),
                    "model": args.model,
                    "xai_base": args.xai_base,
                    "prices": {
                        key: metadata[key] for key in (
                            "prompt_text_token_price", "cached_prompt_text_token_price",
                            "completion_text_token_price",
                        )
                    },
                })
            except Exception as exc:
                provider_setup_failure = _safe_exception_reason(
                    "provider_metadata_authentication_or_model_failure", exc, args.api_key
                )
                write_json(private / "provider_setup_failure.json", {
                    "terminal_reason": provider_setup_failure,
                    "provider_model_calls_started": False,
                })

    transport: ReceiptTransport | None = None
    if metadata is not None and provider_setup_failure is None and set(records) != set(expected_executions):
        try:
            delegate = pilot.PilotTransport(
                api_key=args.api_key,
                base_url=args.xai_base,
                model_metadata=metadata,
                ledger=ledger,
                response_dir=response_dir,
                post=post,
                maximum_rate_limit_retries=args.maximum_rate_limit_retries,
                maximum_server_error_retries=args.maximum_server_error_retries,
            )
            transport = ReceiptTransport(delegate, private / "prompt_receipts.jsonl", receipts)
        except Exception as exc:
            provider_setup_failure = _safe_exception_reason(
                "provider_transport_setup_failure", exc, args.api_key
            )

    global_stop_kind: str | None = prior_global_kind or (
        "authentication_or_model_identity_failure" if provider_setup_failure else None
    )
    global_stop_reason: str | None = prior_global_reason or provider_setup_failure
    cost_ceiling_triggered = global_stop_kind == "hard_cost_ceiling"
    if global_stop_kind is not None and not global_block_path.exists():
        write_json(global_block_path, {
            "kind": global_stop_kind,
            "reason": global_stop_reason,
            "recorded_at": utc_now(),
        })
    for planned in execution_plan["executions"]:
        key = (planned["candidate_id"], planned["profile"])
        if key in records:
            continue
        case = case_by_id[planned["candidate_id"]]
        identity, binding = expected_executions[key]
        attempted = global_stop_kind is None
        if not attempted:
            classified = {
                "status": "operational_failure",
                "terminal_reason": f"run_blocked_before_execution:{global_stop_reason}",
                "public_reply": None,
                "model_call_count": 0,
                "revision_count": 0,
                "pipeline_audit": [{
                    "stage": "runner_global_block", "status": "operational_failure",
                    "reason": global_stop_reason,
                }],
                "pipeline_metadata": None,
                "approved_draft_record": None,
            }
            attempted_receipts: list[dict[str, Any]] = []
        else:
            if transport is None:
                raise ScreeningError("provider transport is unavailable without a global blocker")
            transport.set_execution(identity)
            result_returned = False
            try:
                result = modules[planned["profile"]].run_reply_pipeline(
                    context=json.loads(json.dumps(case.replay_context)),
                    config=json.loads(json.dumps(config)),
                    repository=verified["repository"],
                    transport=transport,
                    maximum_reply_length=MAXIMUM_REPLY_LENGTH,
                    recent_replies=list(case.recent_replies),
                    media_context=None,
                    creation_time=planned["pair_creation_time"],
                )
                result_returned = True
                try:
                    classified = classify_result(result)
                except ScreeningError as exc:
                    classified = _exception_classification(
                        "inconsistent_pipeline_result", exc, transport, args.api_key
                    )
            except pilot.CostLimitReached as exc:
                classified = _exception_classification(
                    "hard_cost_ceiling", exc, transport, args.api_key
                )
                global_stop_kind = "hard_cost_ceiling"
                global_stop_reason = classified["terminal_reason"]
                cost_ceiling_triggered = True
            except (pilot.RateLimitReached, pilot.ServerErrorReached) as exc:
                classified = _exception_classification(
                    "bounded_definite_provider_failure", exc, transport, args.api_key
                )
            except pilot.DefiniteHTTPError as exc:
                classified = _exception_classification(
                    "definite_provider_http_failure", exc, transport, args.api_key
                )
                owned = [
                    row for row in ledger.data.get("operations", [])
                    if row.get("case_id") == binding["case_identity"]
                ]
                status_code = owned[-1].get("http_status_code") if owned else None
                if status_code in {401, 403, 404}:
                    global_stop_kind = "authentication_or_model_identity_failure"
                    global_stop_reason = classified["terminal_reason"]
            except ScreeningError as exc:
                classified = _exception_classification(
                    "corrupt_durable_state", exc, transport, args.api_key
                )
                global_stop_kind = "corrupt_durable_state"
                global_stop_reason = classified["terminal_reason"]
            except pilot.PilotError as exc:
                prefix = "ambiguous_provider_transmission" if ledger.data.get("blocked") else "provider_identity_or_durable_state_failure"
                classified = _exception_classification(prefix, exc, transport, args.api_key)
                global_stop_kind = (
                    "ambiguous_provider_transmission"
                    if ledger.data.get("blocked") else "corrupt_durable_state"
                )
                global_stop_reason = classified["terminal_reason"]
            except Exception as exc:
                prefix = "ambiguous_provider_transmission" if ledger.data.get("blocked") else "pipeline_exception"
                classified = _exception_classification(prefix, exc, transport, args.api_key)
                if ledger.data.get("blocked"):
                    global_stop_kind = "ambiguous_provider_transmission"
                    global_stop_reason = classified["terminal_reason"]
            attempted_receipts = [dict(item) for item in transport.attempted_receipts]
            if global_stop_kind is not None and not global_block_path.exists():
                write_json(global_block_path, {
                    "kind": global_stop_kind,
                    "reason": global_stop_reason,
                    "recorded_at": utc_now(),
                })
            if result_returned and classified["model_call_count"] != transport.sequence:
                reported = classified["model_call_count"]
                classified = {
                    **classified,
                    "status": "operational_failure",
                    "terminal_reason": "inconsistent_pipeline_model_call_count",
                    "public_reply": None,
                    "model_call_count": transport.sequence,
                    "pipeline_audit": [
                        *classified["pipeline_audit"],
                        {
                            "stage": "runner_accounting",
                            "status": "operational_failure",
                            "reported_model_call_count": reported,
                            "observed_model_call_count": transport.sequence,
                        },
                    ],
                    "pipeline_metadata": None,
                    "approved_draft_record": None,
                }
        inventory = _operation_inventory(
            binding["case_identity"], ledger, receipts, response_dir
        )
        if attempted and classified["status"] in {"approved", "no_reply"} and (
            classified["model_call_count"] != len(inventory["logical_call_ids"])
            or classified["model_call_count"] != len(attempted_receipts)
        ):
            classified = {
                **classified,
                "status": "operational_failure",
                "terminal_reason": "completed_outcome_accounting_mismatch",
                "public_reply": None,
                "model_call_count": len(attempted_receipts),
                "pipeline_metadata": None,
                "approved_draft_record": None,
            }
        row = {
            "candidate_id": case.candidate_id,
            "profile": planned["profile"],
            "profile_commit": manifests[planned["profile"]]["commit"],
            "execution_identity": binding["execution_identity"],
            "case_identity": binding["case_identity"],
            "profile_prompt_versions": manifests[planned["profile"]]["versions"],
            "profile_prompt_sha256": manifests[planned["profile"]]["prompt_sha256"],
            "attempted": attempted,
            "attempted_prompt_receipts": attempted_receipts,
            **classified,
            **inventory,
        }
        append_jsonl(private / "execution_records.jsonl", row)
        records[key] = row
    if set(records) != set(expected_executions):
        raise ScreeningError("terminal execution record inventory differs from the plan")
    for key, record in records.items():
        identity, _binding = expected_executions[key]
        _validate_terminal_record(
            record,
            expected_identity=identity,
            profile_manifest=manifests[key[1]],
            ledger=ledger,
            receipts=receipts,
            response_dir=response_dir,
        )
    _validate_record_and_ledger_union(records, ledger, receipts)

    review_files, unscorable = build_review_pack(
        verified["cases"],
        records,
        assignments,
        historical_public_replies=verified.get("historical_public_replies"),
    )
    review_dir = output / "review_pack"
    _write_review_pack(review_dir, review_files)
    assert_blind_pack_clean(
        review_dir,
        _forbidden_review_values(plan_spec, manifests, execution_seed, response_seed, args.api_key),
    )
    historical_values = {
        candidate_id: value for candidate_id, value in verified.get("historical_public_replies", {}).items()
        if value
    }
    review_combined = b"\n".join(review_files.values())
    write_json(private / "historical_reply_exclusion_receipt.json", {
        "candidate_specific_historical_reply_absence_verified": True,
        "historical_reply_candidate_count": len(historical_values),
        "historical_reply_value_sha256_by_candidate": {
            candidate_id: text_sha256(value) for candidate_id, value in historical_values.items()
        },
        "cross_candidate_value_collisions_from_required_context": sum(
            value.encode("utf-8") in review_combined for value in historical_values.values()
        ),
        "historical_fields_projected": False,
    })
    attempted_execution_count = sum(record["attempted"] for record in records.values())
    operational_failure_count = sum(
        row["status"] == "operational_failure" for row in records.values()
    )
    known_ticks = int(ledger.data.get("known_cost_in_usd_ticks") or 0)
    ambiguous_ticks = int(ledger.data.get("ambiguous_exposure_in_usd_ticks") or 0)
    reliability = {
        "planned_execution_count": 72,
        "completed_execution_count": attempted_execution_count,
        "terminal_execution_record_count": len(records),
        "completed_outcome_count": sum(
            row["status"] in {"approved", "no_reply"} for row in records.values()
        ),
        "operational_failure_execution_count": operational_failure_count,
        "scorable_paired_case_count": 36 - len(unscorable),
        "unscorable_candidate_count": len(unscorable),
        "profile_reliability": {
            profile: {
                "completed": sum(
                    row["profile"] == profile and row["status"] in {"approved", "no_reply"}
                    for row in records.values()
                ),
                "operational_failure": sum(
                    row["profile"] == profile and row["status"] == "operational_failure"
                    for row in records.values()
                ),
                "attempted": sum(
                    row["profile"] == profile and row["attempted"]
                    for row in records.values()
                ),
                "unattempted_after_global_block": sum(
                    row["profile"] == profile and not row["attempted"]
                    for row in records.values()
                ),
            }
            for profile in PROFILE_NAMES
        },
        "profile_cost": {
            profile: {
                "cost_in_usd_ticks": sum(
                    int(row["cost_in_usd_ticks"])
                    for row in records.values() if row["profile"] == profile
                ),
                "cost_usd": sum(
                    int(row["cost_in_usd_ticks"])
                    for row in records.values() if row["profile"] == profile
                ) / pilot.USD_TICKS_PER_DOLLAR,
            } for profile in PROFILE_NAMES
        },
        "known_cost_in_usd_ticks": known_ticks,
        "known_cost_usd": known_ticks / pilot.USD_TICKS_PER_DOLLAR,
        "ambiguous_exposure_in_usd_ticks": ambiguous_ticks,
        "ambiguous_exposure_usd": ambiguous_ticks / pilot.USD_TICKS_PER_DOLLAR,
        "hard_cost_limit_usd": HARD_LIMIT_USD,
        "cost_ceiling_reached": cost_ceiling_triggered,
        "run_blocked": global_stop_kind is not None,
        "run_blocked_kind": global_stop_kind,
        "run_blocked_reason": global_stop_reason,
        "deployment_authorised": False,
        "screening_only": True,
        "social_supplement_still_required": True,
        "social_candidate_ids": list(SOCIAL_CANDIDATE_IDS),
        "no_case_replacement": True,
    }
    write_json(private / "reliability_summary.json", reliability)
    write_json(private / "exact_failure_inventory.json", {
        "failures": [row for row in records.values() if row["status"] == "operational_failure"]
    })
    write_json(private / "private_run_manifest.json", {
        "run_identity": run_identity,
        "review_pack_file_sha256": {name: file_sha256(review_dir / name) for name in REVIEW_FILES},
        "deployment_authorised": False,
        "screening_only": True,
        "social_supplement_still_required": True,
        "run_identity_file_sha256": file_sha256(output / "run_identity.json"),
    })
    write_json(private / "run_status.json", {
        "status": "blocked" if global_stop_kind else "complete",
        "resume_safe": False,
        "global_block_kind": global_stop_kind,
        "global_block_reason": global_stop_reason,
        "completed_execution_count": attempted_execution_count,
        "terminal_execution_record_count": len(records),
        "known_cost_usd": known_ticks / pilot.USD_TICKS_PER_DOLLAR,
        "ambiguous_exposure_usd": ambiguous_ticks / pilot.USD_TICKS_PER_DOLLAR,
    })
    assert_no_credentials(output, args.api_key)
    verify_private_tree(output)

    stamp_match = re.fullmatch(r"mrsMThatcher-reply-hybrid-screening-(\d{8}T\d{6}Z)", output.name)
    stamp = stamp_match.group(1) if stamp_match else creation_time.replace("-", "").replace(":", "")[:15] + "Z"
    review_archive = output.parent / f"mrsMThatcher-reply-hybrid-screening-{stamp}-review-pack.tgz"
    audit_archive = output.parent / f"mrsMThatcher-reply-hybrid-screening-{stamp}-audit-key-pack.tgz"
    review_archive_sha256 = publish_deterministic_archive(review_dir, review_archive)
    write_json(private / "archive_manifest.json", {
        "review_archive": str(review_archive),
        "review_archive_sha256": review_archive_sha256,
        "audit_archive": str(audit_archive),
    })
    _checksum_tree(private)
    assert_no_credentials(output, args.api_key)
    verify_private_tree(output)
    audit_archive_sha256 = publish_deterministic_archive(private, audit_archive)
    verify_archive(review_archive, api_key=args.api_key, exact_files=set(REVIEW_FILES))
    verify_archive(
        audit_archive,
        api_key=args.api_key,
        exact_files={
            path.relative_to(private).as_posix()
            for path in private.rglob("*") if path.is_file() and not path.is_symlink()
        },
    )
    return {
        **reliability,
        "run_output_directory": str(output),
        "review_archive": str(review_archive),
        "review_archive_sha256": review_archive_sha256,
        "audit_archive": str(audit_archive),
        "audit_archive_sha256": audit_archive_sha256,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--xai-base", default=DEFAULT_XAI_BASE)
    parser.add_argument("--hard-cost-limit-usd", type=float)
    parser.add_argument("--maximum-rate-limit-retries", type=int, default=1)
    parser.add_argument("--maximum-server-error-retries", type=int, default=1)
    parser.add_argument("--expected-runner-commit")
    parser.add_argument("--paid-acknowledgement")
    parser.add_argument("--resume", action="store_true")
    return parser


def validate_arguments(args: argparse.Namespace, environ: dict[str, str]) -> None:
    if args.xai_base != DEFAULT_XAI_BASE or pilot.validate_xai_base(args.xai_base) != DEFAULT_XAI_BASE:
        raise ScreeningError("endpoint must be exactly https://api.x.ai/v1")
    if args.model != DEFAULT_MODEL:
        raise ScreeningError("model must be exactly grok-4.3")
    if args.resume and not args.execute:
        raise ScreeningError("--resume requires --execute")
    if not args.execute:
        return
    if args.output is None:
        raise ScreeningError("--execute requires --output")
    if args.paid_acknowledgement != PAID_ACKNOWLEDGEMENT:
        raise ScreeningError(f"--execute requires --paid-acknowledgement {PAID_ACKNOWLEDGEMENT}")
    if args.hard_cost_limit_usd != HARD_LIMIT_USD:
        raise ScreeningError("--execute requires --hard-cost-limit-usd 1.50")
    if args.maximum_rate_limit_retries != 1 or args.maximum_server_error_retries != 1:
        raise ScreeningError("execute retry limits must both be exactly one")
    api_key = environ.get("XAI_API_KEY")
    if not api_key:
        raise ScreeningError("XAI_API_KEY is absent; execution did not begin")
    args.api_key = api_key


def run(args: argparse.Namespace, environ: dict[str, str] | None = None) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    validate_arguments(args, environment)
    plan = load_plan()
    verified = verify_input_bundle(args.input.resolve())
    manifests, sources, _paths = verify_and_stage_profiles(plan)
    config = strategy_config(args.model, verified["corpus_path"])
    if not args.execute:
        return {
            "mode": "validate-only",
            "network_requests": 0,
            "candidate_count": len(verified["cases"]),
            "planned_execution_count": 72,
            "candidate_ids_sha256": verified["verification"]["candidate_ids_sha256"],
            "profiles_verified": list(PROFILE_NAMES),
            "configuration_sha256": value_sha256(config),
            "input_sha256sums_sha256": verified["verification"]["sha256sums_sha256"],
        }
    provenance = execution_provenance(args.expected_runner_commit, require_clean=True)
    return execute_screening(args, verified, plan, provenance, manifests, sources)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    try:
        result = run(parser.parse_args(argv))
    except ScreeningError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
