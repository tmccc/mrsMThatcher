#!/usr/bin/env python3
"""Build a no-cost, immutable-source audit for a future reply-profile evaluation.

This program reconstructs candidate and replay-context evidence from retained
local snapshots.  It contains no response generator, provider client, search
client, posting path, or paid-execution mode.  Historical outcomes are retained
as provenance only and are never used as quality labels.
"""

from __future__ import annotations

import argparse
import ast
import csv
import difflib
import hashlib
import html
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

# Import only the repository's deterministic, offline source-role validators.
# Executing a script by path otherwise exposes ``tools/`` but not the repository
# root on ``sys.path``.
_REPOSITORY_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

from historical_context_packet_corrections import apply_packet_corrections
from historical_context_source_roles import public_sources, validate_and_attach_audit


TOOL_VERSION = "reply-hybrid-evaluation-audit-v1"
SCHEMA_VERSION = 1
NORMALISATION_VERSION = "reply-candidate-normalisation-v1"
NEAR_DUPLICATE_VERSION = "sequence-matcher-autojunk-false-v1"
NEAR_DUPLICATE_THRESHOLD = 0.92
CONTEXT_AUDIT_RULE_VERSION = "reply-holdout-context-audit-v1-extended"
SEMANTIC_INVENTORY_VERSION = "fresh-reply-semantic-inventory-v1"
CURRENT_PROFILE_COMMIT = "f07957e8b2388d8258821cb21b25f2a43681cbbd"
HYBRID_PROFILE_COMMIT = "0b7cd1da11d7c9ff5b3051c3d6fc4a70ae45176d"
HISTORICAL_SOURCE_COMMITS = {
    "reply_history_reconstruction": "bdb6a5b18468bbda02f2c908f8a7699601ee5d18",
    "evaluation_pool_tooling": "6931ecd294f8427291d8a31ca2cb5d0a8ad3ca7c",
    "frozen_replay_pack_tooling": "df7f53bfcb9ccd6b38e8b8631bd1d4a1eca1c5e6",
    "completed_calibration_holdout_runner": "cc404c3bd45b97d7664ca4fec02cf1f4154a6a1a",
}
HISTORICAL_SOURCE_HASHES = {
    "reply_history_reconstruction": "2417500c01df5d1338a1280a7f168eb3df690995c6eca3cbfd653e28cb6197ce",
    "evaluation_pool_tooling": "12d60d1f3d5f2108510eb87c8f560d4a2f5ebe9832a546971da01bf68d3b61b0",
    "frozen_replay_pack_tooling": "843982170e729e006ba0c10325885bf12e57d6c9f8f73c573b60837e706e3c4a",
    "completed_calibration_holdout_runner": "479bc170c5cbb4db2ea6efbcc4fb21bb90688bce8bb514b51f2c02153f9c4581",
}
HISTORICAL_SOURCE_PATHS = {
    "reply_history_reconstruction": "tools/reconstruct_reply_history.py",
    "evaluation_pool_tooling": "tools/build_reply_evaluation_pool.py",
    "frozen_replay_pack_tooling": "tools/build_reply_replay_pack.py",
    "completed_calibration_holdout_runner": "tools/run_reply_prompt_calibration.py",
}

PROFILE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "hardened_current": {
        "commit": CURRENT_PROFILE_COMMIT,
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
            "proposer": "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72",
            "reviewer": "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e",
            "evidence": "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b",
            "no_reply_reviewer": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
            "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
        },
    },
    "conservative_hybrid": {
        "commit": HYBRID_PROFILE_COMMIT,
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
            "proposer": "7f69a8bb30296247a049a34a27625885cd5f30813f0eda92f99beb85fbf9cb10",
            "reviewer": "b8e632c41bfba03f792a6a4b93bd16c2fd38a2410429a80268cbe0c93c43127d",
            "evidence": "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b",
            "no_reply_reviewer": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
            "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
        },
    },
}

PROMPT_FUNCTIONS = {
    "proposer": "_proposer_prompts",
    "reviewer": "_reviewer_prompts",
    "evidence": "_evidence_prompts",
    "no_reply_reviewer": "_no_reply_review_prompts",
    "claim_auditor": "_claim_auditor_prompts",
}

ARTIFACT_NAMES = {
    "history_corpus": "mrsMThatcher-reply-history-full-20260810T091145Z",
    "evaluation_shortlist": "mrsMThatcher-reply-evaluation-shortlist-20260810T121659Z",
    "replay_pack": "mrsMThatcher-reply-replay-pack-committed-20260810T185001Z",
    "calibration_run": "mrsMThatcher-reply-prompt-calibration-run-20260811T054317Z",
    "holdout_context_plan": "mrsMThatcher-reply-prompt-holdout-plan-recovered-20260811T105139Z",
    "holdout_operational_plan": "mrsMThatcher-reply-prompt-holdout-operational-cleared-plan-20260811T124917Z",
    "holdout_run": "mrsMThatcher-reply-prompt-holdout-operational-run-20260811T130609Z",
}

EXPECTED_MEMBERSHIP_HASHES = {
    "development_48": "9ff920d7003784ab0f7c15bd40293fccc1d3ab6d0fafeef2d4a668ae35aecd7a",
    "calibration_6": "b6d97683ad2f08aab4bad55ec093ec8bb26c6b4b29b144e6a6344c0785b62b97",
    "holdout_42": "753139b9eb097872d19597823e2b25cd321eecc2e77bace01c5ad6c0ba650a25",
    "primary_33": "46225610d5f02b25a6b12fc21be1a61cec7f9973ade4d02485406a5b3c3ae4da",
    "failed_candidates_9": "cceaccef4cff6b7651702f370de11b3c4b4509d38578299cb600d4075c6d02e9",
    "failed_executions_10": "2fa4055eb57eef82f582bb4b35d444e2cdc767660f4f7a49f3bc90f39bebeea5",
}

CONTEXT_DEPENDENCY_FLAG_ORDER = (
    "mention_or_symbol_only_contribution",
    "short_elliptical_question",
    "unresolved_third_person_pronoun",
    "demonstrative_reference",
    "what_did_mean_question",
    "source_or_attribution_question",
    "quote_or_above_reference",
    "missing_quoted_post_text",
    "missing_or_empty_bounded_parent_context",
)
CONTEXT_PATTERNS = {
    "unresolved_third_person_pronoun": (
        r"\b(?:he|him|his|himself|she|her|hers|herself|they|them|their|"
        r"theirs|themself|themselves|its|itself)\b"
    ),
    "demonstrative_reference": r"\b(?:this|that|it|these|those)\b",
    "what_did_mean_question": r"\bwhat\s+(?:did|do|does)\b[^?\n]{0,160}\bmean\b",
    "source_or_attribution_question": (
        r"(?:\b(?:source|citation|reference|attribution|author|authorship|"
        r"speaker|origin|provenance)\b[^?\n]*\?|"
        r"\b(?:who|whose)\b[^?\n]{0,160}\?|"
        r"\bwhere\b[^?\n]{0,120}\b(?:from|published|printed|recorded)\b[^?\n]*\?|"
        r"\b(?:when|where)\b[^?\n]{0,120}\b(?:said|written|published|delivered|"
        r"spoken|recorded)\b[^?\n]*\?|"
        r"\bdid\b[^?\n]{0,120}\b(?:say|write|author|deliver)\b[^?\n]*\?)"
    ),
    "quote_or_above_reference": r"\b(?:the\s+quote|these\s+words|the\s+above)\b",
}

SEMANTIC_TAGS = (
    "genuine_social_courtesy",
    "substantive_agreement_with_reason_or_principle",
    "civil_challenge_criticism_or_disagreement",
    "analogy_distinction_or_recommendation",
    "direct_factual_or_historical_question",
    "safe_contribution_specific_wit_opportunity",
    "unsupported_allegation_or_sensitive_factual_correction_context",
    "justified_safety_no_reply",
    "other_safe_conversational_contribution",
)

OUTPUT_FILENAMES = (
    "run_manifest.json",
    "source_inventory.json",
    "profile_identity_verification.json",
    "development_exclusion_registry.json",
    "freshness_boundary.json",
    "fresh_candidate_inventory.jsonl",
    "excluded_candidates.jsonl",
    "context_audit.jsonl",
    "context_audit_summary.json",
    "manual_context_review.md",
    "manual_context_clearance.csv",
    "strata_inventory.json",
    "sampling_readiness.json",
    "no_cost_audit_report.md",
)

LONDON = ZoneInfo("Europe/London")
WORD_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
CONSIDERATION_RE = re.compile(
    r"^Considering (?P<lane>mention|hot_post_reply|quote tweet) "
    r"id=(?P<target>\d+) author_id=(?P<author>\d+) "
    r"(?:(?:original_post_id=(?P<quote>\d+) )?)text=(?P<text>.+)$"
)
LOG_HEADER_RE = re.compile(
    rb"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    rb"(?P<level>[A-Z]+)\s+(?P<source>[^ ]+) - (?P<message>.*)\n?$"
)
POST_ID_RE = re.compile(r"\d+")
HASH_RE = re.compile(r"[0-9a-f]{64}")
PROVIDER_CREDENTIAL_ENV_NAMES = (
    "XAI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)
READ_ONLY_SUBPROCESS_COMMANDS = frozenset({"git", "zfs", "zpool"})


class AuditError(RuntimeError):
    """A fail-closed audit precondition or integrity check failed."""


def provider_credentials_are_absent() -> bool:
    """Return whether every guarded provider credential name is absent from the environment."""
    return all(name not in os.environ for name in PROVIDER_CREDENTIAL_ENV_NAMES)


def _run_read_only_command(
    executable: str,
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    """Run one allow-listed local read-only metadata command without a shell."""
    if executable not in READ_ONLY_SUBPROCESS_COMMANDS:
        raise AuditError(f"subprocess executable is not read-only allow-listed: {executable}")
    if any(type(argument) is not str or "\x00" in argument for argument in arguments):
        raise AuditError("subprocess arguments must be NUL-free strings")
    return subprocess.run(
        [executable, *arguments],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        shell=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value as compact, deterministic UTF-8 JSON."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def json_document_bytes(value: Any) -> bytes:
    """Encode a value as deterministic, human-readable JSON with a newline."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )


def value_sha256(value: Any) -> str:
    """Return the SHA-256 of one canonical JSON value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def text_sha256(value: str) -> str:
    """Return the SHA-256 of exact UTF-8 text."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a regular, non-symlink file."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise AuditError(f"source is not a regular non-symlink file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    """Parse a timezone-aware timestamp and return it in UTC."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuditError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise AuditError(f"timestamp lacks an explicit timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _parse_source_timestamp(value: str) -> datetime:
    """Parse retained source time, treating documented naive values as London time."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuditError(f"invalid retained source timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LONDON)
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    """Render a datetime as seconds-precision UTC text."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def strictly_after(candidate_timestamp: str, cutoff: str) -> bool:
    """Return whether the candidate timestamp is strictly later than the cutoff."""
    return parse_utc(candidate_timestamp) > parse_utc(cutoff)


def normalise_incoming(text: str) -> str:
    """Apply the frozen evaluation-pool lexical normalisation."""
    return " ".join(WORD_RE.findall(str(text).casefold()))


def near_duplicate_ratio(left: str, right: str) -> float:
    """Return the frozen deterministic lexical near-duplicate ratio."""
    return difflib.SequenceMatcher(
        None, normalise_incoming(left), normalise_incoming(right), autojunk=False
    ).ratio()


def clean_context_text(text: str) -> str:
    """Apply the production HTML, URL, and whitespace context normalisation."""
    cleaned = html.unescape(str(text or ""))
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    return " ".join(cleaned.split()).strip()


def trim_context_text(text: str, maximum_chars: int) -> str:
    """Apply the production word-aware context bound."""
    cleaned = clean_context_text(text)
    if maximum_chars <= 0:
        return ""
    if len(cleaned) <= maximum_chars:
        return cleaned
    if maximum_chars <= 3:
        return cleaned[:maximum_chars]
    prefix = cleaned[: maximum_chars - 3].rsplit(" ", 1)[0].rstrip(".,;:")
    if not prefix:
        prefix = cleaned[: maximum_chars - 3]
    return prefix + "..."


def read_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, refusing blank, malformed, or non-object records."""
    rows: list[dict[str, Any]] = []
    data = Path(path).read_bytes()
    if data and not data.endswith(b"\n"):
        raise AuditError(f"JSONL file is not newline terminated: {path}")
    for line_number, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            raise AuditError(f"blank JSONL record at {path}:{line_number}")
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditError(f"malformed JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise AuditError(f"non-object JSONL record at {path}:{line_number}")
        rows.append(row)
    return rows


def ensure_safe_source_path(path: Path, allowed_root: Path) -> Path:
    """Resolve a source below its allowed root and reject symlinks and live input."""
    path = Path(path)
    allowed_root = Path(allowed_root).resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AuditError(f"source path is unavailable: {path}") from exc
    if resolved == Path("/disks/disk1/etc/mrsMThatcher") or Path(
        "/disks/disk1/etc/mrsMThatcher"
    ) in resolved.parents:
        raise AuditError("mutable live production input is forbidden")
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise AuditError(f"source escapes allowed root: {path}")
    current = path
    while True:
        if current.is_symlink():
            raise AuditError(f"source path contains a symlink: {current}")
        if current == allowed_root or current.parent == current:
            break
        current = current.parent
    return resolved


def prepare_output_directory(path: Path, allowed_root: Path) -> Path:
    """Create a new private output directory and refuse existing non-empty paths."""
    path = Path(path)
    root = Path(allowed_root).resolve(strict=True)
    try:
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise AuditError(f"output parent is unavailable: {path.parent}") from exc
    if resolved_parent != root and root not in resolved_parent.parents:
        raise AuditError(f"output must be below {root}")
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise AuditError(f"output is not a safe directory: {path}")
        resolved = path.resolve(strict=True)
        if resolved == root or root not in resolved.parents:
            raise AuditError(f"output must be below {root}")
        if any(path.iterdir()):
            raise AuditError(f"output directory is not empty: {path}")
    else:
        path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path.resolve(strict=True)


def canonical_rebuild_command_line(argv: Sequence[str]) -> str:
    """Return a rebuild command with only its required fresh output path normalized."""
    tokens = [sys.executable, str(Path(__file__).resolve())]
    index = 0
    output_seen = False
    while index < len(argv):
        token = str(argv[index])
        if token == "--output":
            if output_seen or index + 1 >= len(argv):
                raise AuditError("command line must contain exactly one --output value")
            tokens.extend((token, "<OUTPUT_DIRECTORY>"))
            output_seen = True
            index += 2
            continue
        if token.startswith("--output="):
            if output_seen:
                raise AuditError("command line contains repeated --output values")
            tokens.append("--output=<OUTPUT_DIRECTORY>")
            output_seen = True
            index += 1
            continue
        tokens.append(token)
        index += 1
    if not output_seen:
        raise AuditError("command line lacks --output")
    return shlex.join(tokens)


def _git(repo: Path, *arguments: str, text: bool = True) -> str | bytes:
    """Run a read-only Git query against a local repository."""
    try:
        completed = _run_read_only_command("git", arguments, cwd=repo, text=text)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"read-only Git query failed: {' '.join(arguments)}") from exc
    return completed.stdout


def _extract_literal_constants(tree: ast.Module) -> dict[str, Any]:
    """Extract top-level literal assignments without executing profile code."""
    constants: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value_node = node.value
        if not isinstance(value_node, ast.Constant):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value_node.value
    return constants


def _extract_initial_system_prompt(tree: ast.Module, function_name: str) -> str:
    """Extract the first literal assignment to ``system`` in one prompt builder."""
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        raise AuditError(f"profile prompt function is missing: {function_name}")
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "system" for target in targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
                raise AuditError(f"{function_name} initial system prompt is not literal")
    raise AuditError(f"{function_name} does not assign a system prompt")


def verify_profile_source(source: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Statically verify profile versions and prompt hashes against expectations."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AuditError("profile source is not valid Python") from exc
    constants = _extract_literal_constants(tree)
    observed_constants = {
        name: constants.get(name) for name in expected.get("constants", {})
    }
    prompt_hashes = {
        role: text_sha256(_extract_initial_system_prompt(tree, function_name))
        for role, function_name in PROMPT_FUNCTIONS.items()
    }
    if observed_constants != expected.get("constants"):
        raise AuditError(
            f"profile version mismatch: expected {expected.get('constants')!r}, "
            f"observed {observed_constants!r}"
        )
    if prompt_hashes != expected.get("prompt_sha256"):
        raise AuditError(
            f"profile prompt hash mismatch: expected {expected.get('prompt_sha256')!r}, "
            f"observed {prompt_hashes!r}"
        )
    return {
        "constants": observed_constants,
        "prompt_sha256": prompt_hashes,
        "verification": "pass",
        "verification_method": "static_ast_literal_extraction_without_import_or_execution",
    }


def verify_profile_identities(repository: Path) -> dict[str, Any]:
    """Verify both immutable profiles directly from their exact Git objects."""
    profiles: dict[str, Any] = {}
    for name, expected in PROFILE_EXPECTATIONS.items():
        commit = str(expected["commit"])
        resolved = str(_git(repository, "rev-parse", f"{commit}^{{commit}}")).strip()
        if resolved != commit:
            raise AuditError(f"profile commit does not resolve exactly: {commit}")
        source_bytes = _git(
            repository, "show", f"{commit}:reply_strategy.py", text=False
        )
        assert isinstance(source_bytes, bytes)
        try:
            source = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuditError(f"profile source is not UTF-8 at {commit}") from exc
        result = verify_profile_source(source, expected)
        result.update(
            {
                "commit": commit,
                "reply_strategy_blob": str(
                    _git(repository, "rev-parse", f"{commit}:reply_strategy.py")
                ).strip(),
                "reply_strategy_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        )
        evidence_bytes = _git(
            repository, "show", f"{commit}:reply_evidence.py", text=False
        )
        assert isinstance(evidence_bytes, bytes)
        result["reply_evidence_blob"] = str(
            _git(repository, "rev-parse", f"{commit}:reply_evidence.py")
        ).strip()
        result["reply_evidence_source_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
        profiles[name] = result
    frozen_roles = ("evidence", "no_reply_reviewer", "claim_auditor")
    for role in frozen_roles:
        if (
            profiles["hardened_current"]["prompt_sha256"][role]
            != profiles["conservative_hybrid"]["prompt_sha256"][role]
        ):
            raise AuditError(f"frozen {role} prompt differs between profiles")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "profiles": profiles,
        "shared_frozen_prompt_hashes_identical": True,
        "profile_code_imported": False,
        "profile_code_executed": False,
    }


def verify_historical_tool_identities(repository: Path) -> dict[str, Any]:
    """Verify every historical reference tool directly from its exact Git commit."""
    receipts: dict[str, Any] = {}
    for name, commit in HISTORICAL_SOURCE_COMMITS.items():
        resolved = str(_git(repository, "rev-parse", f"{commit}^{{commit}}")).strip()
        if resolved != commit:
            raise AuditError(f"historical source commit does not resolve exactly: {name}")
        source_path = HISTORICAL_SOURCE_PATHS[name]
        source = _git(repository, "show", f"{commit}:{source_path}", text=False)
        if not isinstance(source, bytes):
            raise AuditError(f"historical source was not read as bytes: {name}")
        digest = hashlib.sha256(source).hexdigest()
        if digest != HISTORICAL_SOURCE_HASHES[name]:
            raise AuditError(f"historical source hash mismatch: {name}")
        blob = str(_git(repository, "rev-parse", f"{commit}:{source_path}")).strip()
        receipts[name] = {
            "commit": commit,
            "source_path": source_path,
            "git_blob": blob,
            "source_sha256": digest,
            "verification": "pass",
        }
    return receipts


def verify_sha256sums(directory: Path) -> dict[str, str]:
    """Verify every entry in a local SHA256SUMS file without path traversal."""
    directory = Path(directory)
    sums_path = directory / "SHA256SUMS"
    if not sums_path.is_file() or sums_path.is_symlink():
        raise AuditError(f"checksum manifest is missing or unsafe: {sums_path}")
    observed: dict[str, str] = {}
    for line_number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\0\r\n]+)", line)
        if not match:
            raise AuditError(f"invalid checksum entry at {sums_path}:{line_number}")
        expected, relative = match.groups()
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise AuditError(f"unsafe checksum path at {sums_path}:{line_number}")
        target = directory / relative_path
        actual = file_sha256(target)
        if actual != expected:
            raise AuditError(f"checksum mismatch: {target}")
        if relative in observed:
            raise AuditError(f"duplicate checksum path: {relative}")
        observed[relative] = actual
    if not observed:
        raise AuditError(f"empty checksum manifest: {sums_path}")
    return observed


def write_sha256sums(directory: Path) -> dict[str, str]:
    """Write and independently verify sorted hashes for every completed output."""
    directory = Path(directory)
    sums_path = directory / "SHA256SUMS"
    if sums_path.exists():
        raise AuditError(f"refusing to overwrite checksum manifest: {sums_path}")
    hashes = {
        path.name: file_sha256(path)
        for path in sorted(directory.iterdir(), key=lambda item: item.name)
        if path.name != "SHA256SUMS" and path.is_file() and not path.is_symlink()
    }
    if not hashes:
        raise AuditError("cannot checksum an empty output")
    sums_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    sums_path.chmod(0o600)
    if verify_sha256sums(directory) != hashes:
        raise AuditError("independent checksum verification disagreed")
    return hashes


def candidate_ids_sha256(candidate_ids: Iterable[str]) -> str:
    """Hash a candidate-ID set as one canonical sorted list."""
    return value_sha256(sorted(set(candidate_ids)))


def _csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a UTF-8 CSV as dictionaries and reject duplicate headers."""
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise AuditError(f"CSV has missing or duplicate headers: {path}")
        return [dict(row) for row in reader]


def _verify_blind_key(
    path: Path,
    expected_candidate_ids: set[str],
    review_rows: Sequence[dict[str, str]],
) -> dict[str, Any]:
    """Verify blind assignments and every retained review-row label binding."""
    document = json.loads(path.read_text(encoding="utf-8"))
    assignments = document.get("assignments") if isinstance(document, dict) else None
    if not isinstance(assignments, dict) or set(assignments) != expected_candidate_ids:
        raise AuditError(f"blind key candidate membership mismatch: {path}")
    expected_labels = {"Response A", "Response B", "Response C"}
    expected_profiles = {"historical", "current", "compact"}
    for candidate_id, assignment in assignments.items():
        if (
            not isinstance(assignment, dict)
            or set(assignment) != expected_labels
            or set(assignment.values()) != expected_profiles
        ):
            raise AuditError(f"blind key is not one exact profile permutation: {candidate_id}")
    labels_by_candidate: dict[str, set[str]] = defaultdict(set)
    for row in review_rows:
        candidate_id = str(row.get("candidate_id") or "")
        label = str(row.get("response_label") or "")
        if candidate_id not in assignments or label not in expected_labels:
            raise AuditError(f"blind review row is not bound by blind key: {candidate_id}")
        labels_by_candidate[candidate_id].add(label)
    if any(labels != expected_labels for labels in labels_by_candidate.values()):
        raise AuditError(f"blind review candidate lacks one row per response label: {path}")
    return {
        "verified": True,
        "assignment_count": len(assignments),
        "review_candidate_count": len(labels_by_candidate),
        "profile_permutation": sorted(expected_profiles),
        "response_labels": sorted(expected_labels),
    }


def _artifact_receipt(path: Path, *, sums: dict[str, str] | None = None) -> dict[str, Any]:
    """Return an exact path/hash receipt for one immutable artifact file."""
    relative = path.name
    digest = file_sha256(path)
    if sums is not None and sums.get(relative) != digest:
        raise AuditError(f"artifact is not bound by its checksum manifest: {path}")
    return {"path": str(path), "sha256": digest}


def _case_maximum(
    cases: Sequence[dict[str, Any]], candidate_ids: set[str]
) -> dict[str, Any]:
    """Return the conservative timestamp maximum for one development subset."""
    selected = [case for case in cases if case["candidate_id"] in candidate_ids]
    if not selected:
        raise AuditError("development subset is empty")
    case = max(selected, key=lambda row: parse_utc(str(row["terminal_timestamp"])))
    return {
        "maximum_timestamp": str(case["terminal_timestamp"]),
        "candidate_id": str(case["candidate_id"]),
        "target_id": str(case["validated_context"]["target_id"]),
        "first_timestamp": str(case["first_timestamp"]),
    }


def _scan_recorded_development_ids(root: Path) -> set[str]:
    """Scan retained experiment files for recorded candidate identities."""
    found: set[str] = set()
    pattern = re.compile(rb"candidate-[0-9a-f]{64}")
    for path in sorted(root.glob("mrsMThatcher-reply-prompt-*")):
        if not path.is_dir() or path.is_symlink():
            continue
        for source in sorted(path.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            with source.open("rb") as handle:
                tail = b""
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    data = tail + chunk
                    found.update(match.decode("ascii") for match in pattern.findall(data))
                    tail = data[-80:]
    return found


def build_development_registry(
    immutable_research_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Recover and verify all old cases, memberships, scores, and failures."""
    directories = {
        key: immutable_research_root / name for key, name in ARTIFACT_NAMES.items()
    }
    verified_sums: dict[str, dict[str, str]] = {}
    for key, directory in directories.items():
        if key == "holdout_operational_plan":
            # This plan is retained for source inventory; its checksum is verified below.
            pass
        if not directory.is_dir() or directory.is_symlink():
            raise AuditError(f"required historical artifact is unavailable: {directory}")
        if (directory / "SHA256SUMS").exists():
            verified_sums[key] = verify_sha256sums(directory)

    replay_dir = directories["replay_pack"]
    calibration_dir = directories["calibration_run"]
    holdout_dir = directories["holdout_run"]
    replay_sums = verified_sums["replay_pack"]
    calibration_sums = verified_sums["calibration_run"]
    holdout_sums = verified_sums["holdout_run"]

    cases = read_jsonl_strict(replay_dir / "frozen_cases.jsonl")
    calibration_rows = read_jsonl_strict(replay_dir / "calibration_cases.jsonl")
    baselines = {
        row["candidate_id"]: row
        for row in read_jsonl_strict(replay_dir / "historical_baselines.jsonl")
    }
    if len(cases) != 48 or len({row.get("candidate_id") for row in cases}) != 48:
        raise AuditError("frozen replay pack does not contain exactly 48 unique cases")
    all_ids = {str(row["candidate_id"]) for row in cases}
    calibration_ids = {str(row["candidate_id"]) for row in calibration_rows}
    holdout_ids = all_ids - calibration_ids
    if len(calibration_ids) != 6 or len(holdout_ids) != 42:
        raise AuditError("calibration/holdout membership is not exactly 6/42")

    holdout_review_rows = _csv_rows(holdout_dir / "blind_review.csv")
    primary_ids = {row["candidate_id"] for row in holdout_review_rows}
    if len(primary_ids) != 33 or any(
        sum(item["candidate_id"] == candidate_id for item in holdout_review_rows) != 3
        for candidate_id in primary_ids
    ):
        raise AuditError("completed holdout does not bind exactly 33 three-response cases")
    calibration_review_rows = _csv_rows(calibration_dir / "blind_review.csv")
    calibration_blind_verification = _verify_blind_key(
        calibration_dir / "blind_key.json",
        calibration_ids,
        calibration_review_rows,
    )
    holdout_blind_verification = _verify_blind_key(
        holdout_dir / "blind_key.json",
        holdout_ids,
        holdout_review_rows,
    )
    score_fields = {
        "safety_and_factual_integrity_pass",
        "mode_choice_1_to_5",
        "specificity_1_to_5",
        "added_value_1_to_5",
        "voice_1_to_5",
        "wit_appropriateness_1_to_5",
        "no_reply_correctness_1_to_5",
        "overall_rank",
        "reviewer_note",
    }
    populated_score_cells = sum(
        bool(str(row.get(field) or "").strip())
        for row in calibration_review_rows + holdout_review_rows
        for field in score_fields
    )

    audit_by_execution = {
        str(row["execution_identity_sha256"]): row
        for row in read_jsonl_strict(holdout_dir / "pipeline_audits.jsonl")
    }
    failures: list[dict[str, Any]] = []
    for failure in read_jsonl_strict(holdout_dir / "execution_failures.jsonl"):
        execution_id = str(failure["execution_identity_sha256"])
        audit = audit_by_execution.get(execution_id)
        terminal_stage = None
        if audit:
            invalid = [
                item
                for item in audit.get("audit", [])
                if isinstance(item, dict) and item.get("status") == "invalid"
            ]
            if invalid:
                terminal_stage = str(invalid[-1].get("stage") or "")
        if not terminal_stage:
            terminal_stage = re.sub(r"_invalid$", "", str(failure.get("reason") or ""))
        failures.append(
            {
                "candidate_id": str(failure["candidate_id"]),
                "profile": str(failure["variant"]),
                "terminal_stage": terminal_stage,
                "terminal_reason": str(failure["reason"]),
                "execution_identity_sha256": execution_id,
                "pipeline_audit_sha256": failure.get("pipeline_audit_sha256"),
                "failure_source": _artifact_receipt(
                    holdout_dir / "execution_failures.jsonl", sums=holdout_sums
                ),
            }
        )
    failed_ids = {row["candidate_id"] for row in failures}
    if len(failures) != 10 or len(failed_ids) != 9:
        raise AuditError("completed holdout failures are not 10 executions/9 candidates")

    membership_hashes = {
        "development_48": candidate_ids_sha256(all_ids),
        "calibration_6": candidate_ids_sha256(calibration_ids),
        "holdout_42": candidate_ids_sha256(holdout_ids),
        "primary_33": candidate_ids_sha256(primary_ids),
        "failed_candidates_9": candidate_ids_sha256(failed_ids),
        "failed_executions_10": value_sha256(
            sorted(row["execution_identity_sha256"] for row in failures)
        ),
    }
    if membership_hashes != EXPECTED_MEMBERSHIP_HASHES:
        raise AuditError(f"development membership hashes disagree: {membership_hashes!r}")

    recorded_ids = _scan_recorded_development_ids(immutable_research_root)
    extra_recorded_ids = recorded_ids - all_ids
    if extra_recorded_ids:
        raise AuditError(
            "retained prompt-development artifacts contain unknown cases: "
            + ", ".join(sorted(extra_recorded_ids))
        )

    frozen_source = _artifact_receipt(
        replay_dir / "frozen_cases.jsonl", sums=replay_sums
    )
    registry_cases: list[dict[str, Any]] = []
    runtime_cases: dict[str, dict[str, Any]] = {}
    for case in sorted(cases, key=lambda row: str(row["candidate_id"])):
        candidate_id = str(case["candidate_id"])
        context = dict(case["validated_context"])
        recent_account_replies = case.get("recent_account_replies") or []
        if not isinstance(recent_account_replies, list):
            raise AuditError(
                f"development recent-account-reply payload is invalid: {candidate_id}"
            )
        quote_context_recovery = case.get("quote_context_recovery")
        context_with_replay_inputs = {
            **context,
            "recent_account_replies": recent_account_replies,
        }
        incoming = str(context["incoming_contribution"])
        runtime_cases[candidate_id] = {
            "candidate_id": candidate_id,
            "target_id": str(context["target_id"]),
            "thread_id": str(context.get("thread_id") or "") or None,
            "incoming_contribution": incoming,
            "context": context_with_replay_inputs,
        }
        baseline = baselines.get(candidate_id, case.get("historical_baseline", {}))
        registry_cases.append(
            {
                "candidate_id": candidate_id,
                "target_id": str(context["target_id"]),
                "thread_id": str(context.get("thread_id") or "") or None,
                "calibration_member": candidate_id in calibration_ids,
                "holdout_member": candidate_id in holdout_ids,
                "scored_primary_flag": candidate_id in primary_ids,
                "first_relevant_timestamp": str(case["first_timestamp"]),
                "final_relevant_timestamp": str(case["terminal_timestamp"]),
                "incoming_contribution_sha256": text_sha256(incoming),
                "normalised_incoming_fingerprint": text_sha256(normalise_incoming(incoming)),
                "validated_context_fingerprint": value_sha256(context),
                "recent_account_replies_fingerprint": value_sha256(
                    recent_account_replies
                ),
                "quotation_recovery_fingerprint": value_sha256(
                    quote_context_recovery
                ),
                "full_context_fingerprint": value_sha256(
                    context_with_replay_inputs
                ),
                "context_leakage_fingerprint": value_sha256(
                    context_leakage_payload(context_with_replay_inputs)
                ),
                "historical_outcome": baseline.get("historical_outcome"),
                "source_artifact": frozen_source,
                "source_row_sha256": case.get("source_row_sha256"),
                "operational_failure_involvement": candidate_id in failed_ids,
                "failed_execution_identities": sorted(
                    row["execution_identity_sha256"]
                    for row in failures
                    if row["candidate_id"] == candidate_id
                ),
            }
        )

    subset_ids = {
        "calibration_cases": calibration_ids,
        "holdout_candidates": holdout_ids,
        "scored_primary_cases": primary_ids,
        "operational_failure_candidates": failed_ids,
        "all_recorded_development_cases": all_ids,
    }
    maxima = {name: _case_maximum(cases, ids) for name, ids in subset_ids.items()}
    final_name, final_max = max(
        maxima.items(), key=lambda item: parse_utc(item[1]["maximum_timestamp"])
    )
    cutoff = str(final_max["maximum_timestamp"])

    artifact_receipts = {
        "reply_history_corpus": {
            "directory": str(directories["history_corpus"]),
            "sha256sums_sha256": file_sha256(
                directories["history_corpus"] / "SHA256SUMS"
            ),
            "files": {
                name: digest
                for name, digest in verified_sums["history_corpus"].items()
                if name in {
                    "run_manifest.json",
                    "conversational_candidates.jsonl",
                    "unique_log_records.jsonl",
                    "record_occurrences.jsonl",
                    "source_files.jsonl",
                }
            },
        },
        "evaluation_shortlist": {
            "directory": str(directories["evaluation_shortlist"]),
            "sha256sums_sha256": file_sha256(
                directories["evaluation_shortlist"] / "SHA256SUMS"
            ),
            "files": {
                name: digest
                for name, digest in verified_sums["evaluation_shortlist"].items()
                if name in {
                    "normalised_candidates.jsonl",
                    "evaluation_eligible_candidates.jsonl",
                    "excluded_candidates.jsonl",
                    "input_text_clusters.jsonl",
                }
            },
        },
        "frozen_replay_pack": {
            "directory": str(replay_dir),
            "sha256sums_sha256": file_sha256(replay_dir / "SHA256SUMS"),
            "files": {
                name: digest
                for name, digest in replay_sums.items()
                if name in {
                    "frozen_cases.jsonl",
                    "calibration_cases.jsonl",
                    "historical_baselines.jsonl",
                    "model_inputs.jsonl",
                    "recent_account_replies.jsonl",
                    "quote_context_recovery.jsonl",
                    "leakage_audit.json",
                    "source_verification.json",
                }
            },
        },
        "completed_calibration": {
            "directory": str(calibration_dir),
            "sha256sums_sha256": file_sha256(calibration_dir / "SHA256SUMS"),
            "blind_key_sha256": calibration_sums["blind_key.json"],
            "blind_review_sha256": calibration_sums["blind_review.csv"],
            "cost_ledger_sha256": calibration_sums["cost_ledger.json"],
            "pipeline_audits_sha256": calibration_sums["pipeline_audits.jsonl"],
            "pipeline_results_sha256": calibration_sums["pipeline_results.jsonl"],
            "prompt_receipts_sha256": calibration_sums["prompt_receipts.jsonl"],
        },
        "completed_holdout": {
            "directory": str(holdout_dir),
            "sha256sums_sha256": file_sha256(holdout_dir / "SHA256SUMS"),
            "blind_key_sha256": holdout_sums["blind_key.json"],
            "blind_review_sha256": holdout_sums["blind_review.csv"],
            "cost_ledger_sha256": holdout_sums["cost_ledger.json"],
            "pipeline_audits_sha256": holdout_sums["pipeline_audits.jsonl"],
            "execution_failures_sha256": holdout_sums["execution_failures.jsonl"],
            "pipeline_results_sha256": holdout_sums["pipeline_results.jsonl"],
            "run_manifest_sha256": holdout_sums["run_manifest.json"],
            "reliability_summary_sha256": holdout_sums[
                "holdout_reliability_summary.json"
            ],
            "context_files": {
                name: digest
                for name, digest in holdout_sums.items()
                if "context" in name or "clearance" in name
            },
        },
        "context_recovery_and_clearance": {
            "recovered_plan_directory": str(directories["holdout_context_plan"]),
            "recovered_plan_sha256sums_sha256": file_sha256(
                directories["holdout_context_plan"] / "SHA256SUMS"
            ),
            "recovered_plan_files": {
                name: digest
                for name, digest in verified_sums["holdout_context_plan"].items()
                if "context" in name or "clearance" in name
            },
            "operational_plan_directory": str(directories["holdout_operational_plan"]),
            "operational_plan_sha256sums_sha256": file_sha256(
                directories["holdout_operational_plan"] / "SHA256SUMS"
            ),
            "operational_plan_files": {
                name: digest
                for name, digest in verified_sums["holdout_operational_plan"].items()
                if "context" in name or "clearance" in name
            },
        },
    }
    registry = {
        "schema_version": SCHEMA_VERSION,
        "registry_version": "reply-development-exclusion-registry-v1",
        "counts": {
            "development_cases": len(registry_cases),
            "calibration_cases": len(calibration_ids),
            "holdout_candidates": len(holdout_ids),
            "scored_primary_cases": len(primary_ids),
            "failed_executions": len(failures),
            "unique_failed_candidates": len(failed_ids),
        },
        "membership_sha256": membership_hashes,
        "cases": registry_cases,
        "failed_executions": sorted(
            failures,
            key=lambda row: (
                row["candidate_id"], row["profile"], row["execution_identity_sha256"]
            ),
        ),
        "recorded_prompt_development_identity_scan": {
            "recorded_candidate_count": len(recorded_ids),
            "unknown_candidate_count": 0,
            "scope": str(immutable_research_root / "mrsMThatcher-reply-prompt-*"),
        },
        "locked_score_and_blind_key_audit": {
            "calibration_blind_key_verification": calibration_blind_verification,
            "holdout_blind_key_verification": holdout_blind_verification,
            "calibration_review_row_count": len(calibration_review_rows),
            "holdout_review_row_count": len(holdout_review_rows),
            "populated_human_score_or_note_cells": populated_score_cells,
            "populated_locked_score_artifact_recovered": populated_score_cells > 0,
            "discrepancy": (
                "Retained blind keys are checksummed, but every retained human-score "
                "and reviewer-note field is blank; no historical winner or score is inferred."
            ),
        },
        "artifact_receipts": artifact_receipts,
    }
    boundary = {
        "schema_version": SCHEMA_VERSION,
        "boundary_version": "reply-development-freshness-boundary-v1",
        "development_cutoff": cutoff,
        "comparison_rule": "candidate_timestamp > development_cutoff",
        "comparison_operator": ">",
        "timestamp_basis": (
            "latest verified candidate/event timestamp, never a branch, commit, "
            "snapshot-creation, or experiment-execution timestamp"
        ),
        "source_maxima": [],
        "final_maximum_source_subset": final_name,
        "final_maximum_candidate_id": final_max["candidate_id"],
        "final_maximum_target_id": final_max["target_id"],
        "artefacts_used": artifact_receipts,
        "discrepancy_policy": "use the most conservative later verified boundary",
        "verified_discrepancies_affecting_cutoff": [],
    }
    membership_sources = {
        "calibration_cases": [replay_dir / "calibration_cases.jsonl"],
        "holdout_candidates": [
            replay_dir / "frozen_cases.jsonl",
            replay_dir / "calibration_cases.jsonl",
        ],
        "scored_primary_cases": [holdout_dir / "blind_review.csv"],
        "operational_failure_candidates": [holdout_dir / "execution_failures.jsonl"],
        "all_recorded_development_cases": [replay_dir / "frozen_cases.jsonl"],
    }
    for name, maximum in maxima.items():
        boundary["source_maxima"].append(
            {
                "source_subset": name,
                **maximum,
                "timestamp_artifact_path": str(replay_dir / "frozen_cases.jsonl"),
                "timestamp_artifact_sha256": replay_sums["frozen_cases.jsonl"],
                "membership_artifacts": [
                    {"path": str(path), "sha256": file_sha256(path)}
                    for path in membership_sources[name]
                ],
            }
        )
    return registry, boundary, runtime_cases


def context_leakage_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Return the semantic context payload used only for leakage detection."""
    quoted = context.get("quoted_post")
    parents = context.get("parent_thread") or []
    clarification = context.get("clarification_request")
    recent_replies = context.get("recent_account_replies") or []
    resolved_quotation = context.get("resolved_quotation")
    return {
        "lane": str(context.get("lane") or ""),
        "incoming_contribution": clean_context_text(
            str(context.get("incoming_contribution") or "")
        ),
        "quoted_post": (
            {
                "author_role": str(quoted.get("author_role") or ""),
                "text": clean_context_text(str(quoted.get("text") or "")),
            }
            if isinstance(quoted, dict)
            else None
        ),
        "parent_thread": [
            {
                "author_role": str(parent.get("author_role") or ""),
                "text": clean_context_text(str(parent.get("text") or "")),
            }
            for parent in parents
            if isinstance(parent, dict)
        ],
        "clarification_request": (
            {
                "original_question": clean_context_text(
                    str(clarification.get("original_question") or "")
                ),
                "correction": clean_context_text(
                    str(clarification.get("correction") or "")
                ),
            }
            if isinstance(clarification, dict)
            else None
        ),
        "recent_account_replies": [
            clean_context_text(
                str(
                    (
                        reply.get("text")
                        if reply.get("text") is not None
                        else reply.get("reply_text")
                    )
                    if isinstance(reply, dict)
                    else reply
                )
            )
            for reply in recent_replies
        ],
        "resolved_quotation": resolved_quotation,
    }


def context_leakage_text(
    context: dict[str, Any], *, candidate_dependent_only: bool = False
) -> str:
    """Render stable context text, optionally excluding shared replay-history fields."""
    payload = context_leakage_payload(context)
    parts = [
        f"lane {payload['lane']}",
        f"incoming {payload['incoming_contribution']}",
    ]
    quoted = payload["quoted_post"]
    if quoted:
        parts.append(f"quoted {quoted['author_role']} {quoted['text']}")
    for parent in payload["parent_thread"]:
        parts.append(f"parent {parent['author_role']} {parent['text']}")
    clarification = payload["clarification_request"]
    if clarification:
        parts.append(f"original {clarification['original_question']}")
        parts.append(f"correction {clarification['correction']}")
    if not candidate_dependent_only:
        for recent_reply in payload["recent_account_replies"]:
            parts.append(f"recent account reply {recent_reply}")
        if payload["resolved_quotation"] is not None:
            parts.append(
                "resolved quotation "
                + canonical_json_bytes(payload["resolved_quotation"]).decode("utf-8")
            )
    return "\n".join(parts)


def context_dependency_flags(context: dict[str, Any]) -> list[str]:
    """Return the frozen ordered context-dependency warnings for one candidate."""
    incoming = str(context.get("incoming_contribution") or "")
    folded = incoming.casefold()
    flags: list[str] = []
    contribution_without_handles = re.sub(
        r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", incoming
    )
    if incoming.strip() and not re.findall(
        r"[^\W_]+", contribution_without_handles, flags=re.UNICODE
    ):
        flags.append("mention_or_symbol_only_contribution")
    words = re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", incoming)
    if "?" in incoming and len(words) <= 8:
        flags.append("short_elliptical_question")
    for flag, pattern in CONTEXT_PATTERNS.items():
        if re.search(pattern, folded, re.IGNORECASE):
            flags.append(flag)
    quoted = context.get("quoted_post")
    quoted_text = str(quoted.get("text") or "") if isinstance(quoted, dict) else ""
    if context.get("lane") == "quote_tweet" and not quoted_text.strip():
        flags.append("missing_quoted_post_text")
    parent_texts = [
        str(parent.get("text") or "")
        for parent in context.get("parent_thread") or []
        if isinstance(parent, dict)
    ]
    dependency_flags = set(flags) - {
        "missing_quoted_post_text",
        "missing_or_empty_bounded_parent_context",
    }
    if dependency_flags and not quoted_text.strip() and not any(
        text.strip() for text in parent_texts
    ):
        flags.append("missing_or_empty_bounded_parent_context")
    return [flag for flag in CONTEXT_DEPENDENCY_FLAG_ORDER if flag in flags]


def context_temporal_violations(
    context_records: Sequence[dict[str, Any]], candidate_timestamp: str
) -> list[dict[str, Any]]:
    """Report future parent, quotation, or recent-reply records deterministically."""
    candidate_time = parse_utc(candidate_timestamp)
    violations: list[dict[str, Any]] = []
    for record in context_records:
        created_at = record.get("created_at")
        if not created_at:
            violations.append(
                {
                    "kind": record.get("kind"),
                    "post_id": record.get("post_id"),
                    "reason": "missing_context_timestamp",
                }
            )
            continue
        record_time = _parse_source_timestamp(str(created_at))
        strictly_prior = record.get("kind") == "recent_account_reply"
        unsafe = record_time >= candidate_time if strictly_prior else record_time > candidate_time
        if unsafe:
            violations.append(
                {
                    "kind": record.get("kind"),
                    "post_id": record.get("post_id"),
                    "created_at": str(created_at),
                    "candidate_timestamp": candidate_timestamp,
                    "reason": (
                        "recent_reply_not_strictly_prior"
                        if strictly_prior
                        else "context_postdates_candidate"
                    ),
                }
            )
    return sorted(
        violations,
        key=lambda item: (str(item.get("kind")), str(item.get("post_id"))),
    )


def resolve_cache_records(
    records: Sequence[dict[str, Any]], *, allow_ordered_resolution: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve identical cache copies or fail closed on conflicting source rows."""
    if not records:
        raise AuditError("cache record set is empty")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        record = item.get("record") if "record" in item else item
        if not isinstance(record, dict):
            raise AuditError("cache source record is not an object")
        grouped[value_sha256(record)].append(item)
    if len(grouped) == 1:
        digest = next(iter(grouped))
        first = records[0].get("record") if "record" in records[0] else records[0]
        return dict(first), {
            "status": "identical_across_immutable_sources",
            "source_record_sha256": digest,
            "source_copy_count": len(records),
        }
    if not allow_ordered_resolution:
        raise AuditError(
            "conflicting source records cannot be resolved from identical provenance"
        )
    ordered = sorted(
        records,
        key=lambda item: (
            int(item.get("snapshot_creation_epoch", 0)),
            str(item.get("snapshot_name") or ""),
        ),
    )
    last = ordered[-1]
    record = last.get("record") if "record" in last else last
    return dict(record), {
        "status": "resolved_by_explicit_immutable_snapshot_order",
        "source_record_sha256": value_sha256(record),
        "conflicting_sha256": sorted(grouped),
        "selected_snapshot": last.get("snapshot_name"),
    }


def _snapshot_properties(dataset: str, snapshot: str) -> dict[str, Any]:
    """Read exact numeric ZFS snapshot metadata without modifying ZFS state."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", snapshot):
        raise AuditError(f"unsafe snapshot name: {snapshot!r}")
    target = f"{dataset}@{snapshot}"
    try:
        completed = _run_read_only_command(
            "zfs",
            [
                "get",
                "-Hp",
                "-o",
                "property,value",
                "guid,createtxg,creation,referenced,mountpoint",
                target,
            ],
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"could not read ZFS metadata for {target}") from exc
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        field, separator, value = line.partition("\t")
        if not separator:
            raise AuditError(f"unexpected ZFS property output for {target}")
        properties[field] = value
    required = {"guid", "createtxg", "creation", "referenced", "mountpoint"}
    if set(properties) != required:
        raise AuditError(f"incomplete ZFS metadata for {target}: {properties!r}")
    creation_epoch = int(properties["creation"])
    return {
        "dataset": dataset,
        "snapshot_name": snapshot,
        "snapshot_guid": int(properties["guid"]),
        "creation_txg": int(properties["createtxg"]),
        "creation_epoch": creation_epoch,
        "creation_time_utc": utc_text(
            datetime.fromtimestamp(creation_epoch, tz=timezone.utc)
        ),
        "referenced_bytes": int(properties["referenced"]),
        "dataset_mountpoint": properties["mountpoint"],
        "metadata_source": "zfs get -Hp numeric properties",
        "snapshot_name_timestamp_inference_used": False,
    }


def _verify_snapshot_window_completeness(
    dataset: str,
    snapshot_root: Path,
    project_relative_path: Path,
    selected: Sequence[dict[str, Any]],
    cutoff: str,
    audit_created_at: str,
) -> dict[str, Any]:
    """Prove the selected immutable chain spans every retained complete post-cutoff snapshot."""
    try:
        completed = _run_read_only_command(
            "zfs",
            [
                "list",
                "-Hp",
                "-t",
                "snapshot",
                "-o",
                "name,creation,createtxg,guid,referenced",
                "-s",
                "creation",
                dataset,
            ],
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"could not enumerate retained snapshots for {dataset}") from exc
    cutoff_epoch = int(parse_utc(cutoff).timestamp())
    audit_epoch = int(parse_utc(audit_created_at).timestamp())
    retained: list[dict[str, Any]] = []
    prefix = dataset + "@"
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 5 or not fields[0].startswith(prefix):
            raise AuditError("unexpected retained-snapshot inventory output")
        name = fields[0][len(prefix) :]
        creation_epoch = int(fields[1])
        project = snapshot_root / name / project_relative_path
        required = (
            project / "bot_state.json",
            project / "mrsMThatcher2.py",
            project / "reply_strategy.py",
            project / "mrsMThatcher.log",
        )
        complete = (
            project.is_dir()
            and not project.is_symlink()
            and all(path.is_file() and not path.is_symlink() for path in required)
            and (
                not (project / "mrsMThatcher.log").read_bytes()
                or (project / "mrsMThatcher.log").read_bytes().endswith(b"\n")
            )
        )
        retained.append(
            {
                "snapshot_name": name,
                "creation_epoch": creation_epoch,
                "creation_txg": int(fields[2]),
                "snapshot_guid": int(fields[3]),
                "referenced_bytes": int(fields[4]),
                "project_source_complete": complete,
            }
        )
    complete_post_cutoff = [
        item
        for item in retained
        if item["project_source_complete"]
        and cutoff_epoch < item["creation_epoch"] <= audit_epoch
    ]
    if not complete_post_cutoff:
        raise AuditError("no complete retained snapshot exists after development cutoff")
    expected_names = [item["snapshot_name"] for item in complete_post_cutoff]
    selected_names = [str(item["snapshot_name"]) for item in selected]
    if selected_names != expected_names:
        raise AuditError(
            "selected snapshots omit or add retained complete post-cutoff sources; "
            f"expected {expected_names!r}, observed {selected_names!r}"
        )
    enumerated_by_name = {item["snapshot_name"]: item for item in retained}
    for item in selected:
        enumerated = enumerated_by_name[item["snapshot_name"]]
        if any(
            int(item[field]) != int(enumerated[field])
            for field in ("creation_epoch", "creation_txg", "snapshot_guid", "referenced_bytes")
        ):
            raise AuditError(
                f"selected snapshot metadata changed during audit: {item['snapshot_name']}"
            )
    return {
        "verification": "pass",
        "enumeration_command": (
            "zfs list -Hp -t snapshot -o name,creation,createtxg,guid,referenced "
            f"-s creation {dataset}"
        ),
        "retained_snapshot_count": len(retained),
        "retained_inventory_sha256": value_sha256(retained),
        "complete_post_cutoff_snapshot_names": expected_names,
        "first_boundary_covering_snapshot": expected_names[0],
        "latest_complete_retained_snapshot": expected_names[-1],
        "selection_matches_complete_retained_window": True,
        "cutoff": cutoff,
        "audit_created_at": audit_created_at,
    }


def _snapshot_log_paths(project: Path) -> list[Path]:
    """Return oldest-to-newest log rotations for one immutable project source."""
    paths = [project / f"mrsMThatcher.log.{index}" for index in range(5, 0, -1)]
    paths.append(project / "mrsMThatcher.log")
    return [path for path in paths if path.exists()]


def _decode_consideration(message: str) -> dict[str, str] | None:
    """Decode one logged candidate consideration without evaluating arbitrary code."""
    match = CONSIDERATION_RE.fullmatch(message)
    if not match:
        return None
    try:
        text = ast.literal_eval(match.group("text"))
    except (SyntaxError, ValueError) as exc:
        raise AuditError(f"malformed logged contribution literal: {message[:120]!r}") from exc
    if not isinstance(text, str):
        raise AuditError("logged contribution literal is not text")
    lane = match.group("lane").replace(" ", "_")
    return {
        "lane": lane,
        "target_id": match.group("target"),
        "author_id": match.group("author"),
        "quoted_post_id": match.group("quote") or "",
        "incoming_text": text,
    }


def reconstruct_considerations(
    snapshot_projects: Sequence[tuple[dict[str, Any], Path]], cutoff: str
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Reconstruct and deduplicate all post-cutoff candidate consideration records."""
    cutoff_time = parse_utc(cutoff)
    unique_records: dict[tuple[str, str], dict[str, Any]] = {}
    malformed_records: dict[tuple[str, str], dict[str, Any]] = {}
    source_files: list[dict[str, Any]] = []
    for snapshot, project in snapshot_projects:
        stream_sequence = 0
        for file_sequence, path in enumerate(_snapshot_log_paths(project), 1):
            raw_file = path.read_bytes()
            if raw_file and not raw_file.endswith(b"\n"):
                raise AuditError(f"snapshot log is not newline terminated: {path}")
            source_files.append(
                {
                    "snapshot_name": snapshot["snapshot_name"],
                    "relative_path": path.name,
                    "source_path": str(path),
                    "source_file_sequence": file_sequence,
                    "byte_size": len(raw_file),
                    "sha256": hashlib.sha256(raw_file).hexdigest(),
                    "regular_non_symlink": path.is_file() and not path.is_symlink(),
                }
            )
            if path.is_symlink() or not path.is_file():
                raise AuditError(f"snapshot log is unsafe: {path}")
            for line_number, raw_line in enumerate(raw_file.splitlines(keepends=True), 1):
                stream_sequence += 1
                header = LOG_HEADER_RE.match(raw_line)
                if not header:
                    continue
                timestamp_local = datetime.strptime(
                    header.group("timestamp").decode("ascii"), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=LONDON)
                timestamp = timestamp_local.astimezone(timezone.utc)
                if timestamp <= cutoff_time:
                    continue
                raw_digest = hashlib.sha256(raw_line).hexdigest()
                timestamp_text = utc_text(timestamp)
                key = (timestamp_text, raw_digest)
                location = {
                    "snapshot_name": snapshot["snapshot_name"],
                    "source_path": str(path),
                    "relative_path": path.name,
                    "source_file_sequence": file_sequence,
                    "source_stream_sequence": stream_sequence,
                    "line_number": line_number,
                    "occurrence_id": "occurrence-"
                    + value_sha256(
                        {
                            "snapshot": snapshot["snapshot_name"],
                            "path": path.name,
                            "line": line_number,
                            "raw_sha256": raw_digest,
                        }
                    ),
                }
                try:
                    message = header.group("message").decode("utf-8")
                except UnicodeDecodeError as exc:
                    if not header.group("message").startswith(b"Considering "):
                        raise AuditError(
                            f"log message is not UTF-8: {path}:{line_number}"
                        ) from exc
                    message = ""
                    decode_error: AuditError | None = AuditError(
                        "candidate consideration message is not UTF-8"
                    )
                else:
                    decode_error = None
                try:
                    candidate = (
                        _decode_consideration(message) if decode_error is None else None
                    )
                except AuditError as exc:
                    candidate = None
                    decode_error = exc
                if candidate is None:
                    if decode_error is None and not message.startswith("Considering "):
                        continue
                    if decode_error is None:
                        decode_error = AuditError(
                            "candidate consideration record does not match frozen grammar"
                        )
                    target_match = re.search(r"\bid=(\d+)\b", message)
                    issue = malformed_records.setdefault(
                        key,
                        {
                            "candidate_id": None,
                            "target_id": (
                                target_match.group(1) if target_match else None
                            ),
                            "lane": None,
                            "first_consideration_timestamp": timestamp_text,
                            "source_record_fingerprint": value_sha256(
                                {
                                    "timestamp": timestamp_text,
                                    "raw_record_sha256": raw_digest,
                                }
                            ),
                            "source_locations": [],
                            "exclusion_reasons": [
                                {
                                    "reason": "malformed_candidate_consideration_record",
                                    "raw_record_sha256": raw_digest,
                                    "error_type": type(decode_error).__name__,
                                    "error_message_sha256": text_sha256(
                                        str(decode_error)
                                    ),
                                }
                            ],
                        },
                    )
                    issue["source_locations"].append(location)
                    continue
                if key in unique_records:
                    if unique_records[key]["candidate"] != candidate:
                        raise AuditError("identical raw record key decoded inconsistently")
                    unique_records[key]["source_locations"].append(location)
                    continue
                unique_records[key] = {
                    "record_id": "record-"
                    + value_sha256(
                        {
                            "timestamp": timestamp_text,
                            "raw_record_sha256": raw_digest,
                        }
                    ),
                    "record_fingerprint": value_sha256(
                        {"timestamp": timestamp_text, "raw_record_sha256": raw_digest}
                    ),
                    "timestamp": timestamp_text,
                    "original_timestamp_text": header.group("timestamp").decode("ascii"),
                    "timezone_interpretation": "Europe/London_then_UTC",
                    "raw_record_sha256": raw_digest,
                    "candidate": candidate,
                    "source_locations": [location],
                }

    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in unique_records.values():
        by_target[record["candidate"]["target_id"]].append(record)
    candidates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for target_id, records in sorted(by_target.items()):
        records.sort(key=lambda row: (parse_utc(row["timestamp"]), row["record_id"]))
        identities = {
            value_sha256(
                {
                    key: record["candidate"][key]
                    for key in (
                        "lane",
                        "target_id",
                        "author_id",
                        "quoted_post_id",
                        "incoming_text",
                    )
                }
            )
            for record in records
        }
        candidate = dict(records[0]["candidate"])
        if len(identities) != 1:
            conflicts.append(
                {
                    "target_id": target_id,
                    "reason": "conflicting_candidate_consideration_records",
                    "record_ids": [record["record_id"] for record in records],
                }
            )
            candidate["source_conflict"] = True
        else:
            candidate["source_conflict"] = False
        candidate.update(
            {
                "first_consideration_timestamp": records[0]["timestamp"],
                "final_consideration_timestamp": records[-1]["timestamp"],
                "consideration_record_count": len(records),
                "consideration_records": records,
            }
        )
        candidates.append(candidate)
    counts = {
        "raw_post_cutoff_consideration_occurrences": sum(
            len(record["source_locations"]) for record in unique_records.values()
        ),
        "deduplicated_post_cutoff_consideration_records": len(unique_records),
        "unique_post_cutoff_targets": len(candidates),
        "candidate_source_conflicts": len(conflicts),
        "malformed_candidate_source_records": len(malformed_records),
    }
    return candidates, counts, source_files, list(malformed_records.values())


def load_snapshot_cache(
    snapshot_projects: Sequence[tuple[dict[str, Any], Path]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    set[str],
    str,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    """Load an immutable cache union, retaining row-local defects for exclusion."""
    cache_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cache_issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    own_auto_reply_ids: set[str] = set()
    states: list[dict[str, Any]] = []
    state_receipts: list[dict[str, Any]] = []
    for snapshot, project in snapshot_projects:
        path = project / "bot_state.json"
        if path.is_symlink() or not path.is_file():
            raise AuditError(f"snapshot bot_state is unavailable or unsafe: {path}")
        raw = path.read_bytes()
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuditError(f"snapshot bot_state is malformed: {path}") from exc
        if not isinstance(state, dict) or not isinstance(state.get("tweet_cache"), dict):
            raise AuditError(f"snapshot bot_state lacks tweet_cache: {path}")
        states.append(state)
        state_receipts.append(
            {
                "snapshot_name": snapshot["snapshot_name"],
                "source_path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
                "tweet_cache_record_count": len(state["tweet_cache"]),
            }
        )
        own_auto_reply_ids.update(
            str(item) for item in state.get("own_auto_reply_ids", [])
        )
        for target_id, record in state["tweet_cache"].items():
            target_id = str(target_id)
            if not isinstance(record, dict):
                cache_issues[target_id].append(
                    {
                        "reason": "malformed_cache_record_non_object",
                        "snapshot_name": snapshot["snapshot_name"],
                        "stable_source_position": f"tweet_cache/{target_id}",
                        "source_value_sha256": value_sha256(record),
                    }
                )
                continue
            internal_id = str(record.get("id") or "")
            if not POST_ID_RE.fullmatch(target_id) or internal_id != target_id:
                cache_issues[target_id].append(
                    {
                        "reason": "invalid_or_unstable_cache_identity",
                        "snapshot_name": snapshot["snapshot_name"],
                        "stable_source_position": f"tweet_cache/{target_id}",
                        "cache_key": target_id,
                        "record_id": internal_id or None,
                        "source_record_sha256": value_sha256(record),
                    }
                )
                continue
            cache_sources[target_id].append(
                {
                    "record": record,
                    "snapshot_name": snapshot["snapshot_name"],
                    "snapshot_creation_epoch": snapshot["creation_epoch"],
                    "bot_state_sha256": hashlib.sha256(raw).hexdigest(),
                    "stable_source_position": f"tweet_cache/{target_id}",
                }
            )
    cache: dict[str, dict[str, Any]] = {}
    provenance: dict[str, list[dict[str, Any]]] = {}
    for target_id, sources in sorted(cache_sources.items()):
        try:
            record, resolution = resolve_cache_records(sources)
        except AuditError:
            cache_issues[target_id].append(
                {
                    "reason": "conflicting_cache_source_records",
                    "source_copies": [
                        {
                            "snapshot_name": source["snapshot_name"],
                            "stable_source_position": source["stable_source_position"],
                            "source_record_sha256": value_sha256(source["record"]),
                        }
                        for source in sources
                    ],
                }
            )
            continue
        cache[target_id] = record
        provenance[target_id] = [
            {
                "snapshot_name": source["snapshot_name"],
                "bot_state_sha256": source["bot_state_sha256"],
                "stable_source_position": source["stable_source_position"],
                "cache_record_sha256": value_sha256(source["record"]),
                "resolution": resolution["status"],
            }
            for source in sources
        ]
    account_author_ids = {
        str(cache[target_id].get("author_id") or "")
        for target_id in own_auto_reply_ids
        if target_id in cache and cache[target_id].get("author_id")
    }
    if len(account_author_ids) != 1:
        raise AuditError(
            f"could not derive one account author identity: {sorted(account_author_ids)!r}"
        )
    return (
        cache,
        provenance,
        own_auto_reply_ids,
        next(iter(account_author_ids)),
        state_receipts,
        dict(sorted(cache_issues.items())),
    )


def _references(record: dict[str, Any], reference_type: str) -> list[str]:
    """Return stable referenced-post IDs of one requested type."""
    references = record.get("referenced_tweets", []) or []
    if not isinstance(references, list):
        raise AuditError("cached referenced_tweets is not a list")
    result: list[str] = []
    for reference in references:
        if not isinstance(reference, dict):
            raise AuditError("cached referenced_tweets contains a non-object")
        reference_kind = reference.get("type")
        if not isinstance(reference_kind, str) or not reference_kind:
            raise AuditError("cached reference lacks a stable type")
        reference_id = str(reference.get("id") or "")
        if not POST_ID_RE.fullmatch(reference_id):
            raise AuditError("cached reference lacks a stable numeric ID")
        if reference_kind == reference_type:
            result.append(reference_id)
    return result


def _tweet_context_text(record: dict[str, Any]) -> str:
    """Return production-equivalent text or a bounded image-summary marker."""
    cleaned = clean_context_text(str(record.get("text") or ""))
    if cleaned:
        return cleaned
    summary = clean_context_text(str(record.get("image_summary") or ""))
    return f"[Image/meme summary: {summary}]" if summary else ""


def _context_post(
    record: dict[str, Any], account_author_id: str, maximum_chars: int = 500
) -> dict[str, str]:
    """Build one production-role-labelled bounded context post."""
    author_id = str(record.get("author_id") or "")
    role = "account" if author_id == account_author_id else ("user" if author_id else "unknown")
    return {
        "post_id": str(record.get("id") or "unknown"),
        "author_role": role,
        "text": trim_context_text(_tweet_context_text(record), maximum_chars),
    }


def _record_temporal_receipt(
    kind: str,
    record: dict[str, Any],
    provenance: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return the source and time receipt for one context component."""
    post_id = str(record.get("id") or "")
    return {
        "kind": kind,
        "post_id": post_id,
        "created_at": record.get("created_at"),
        "cached_epoch": record.get("cached_epoch"),
        "source_record_sha256": value_sha256(record),
        "source_provenance": provenance.get(post_id, []),
    }


CLARIFICATION_CUE_RE = re.compile(
    r"\b(?:you\s+)?(?:did(?:n't|\s+not)|does(?:n't|\s+not)|have(?:n't|\s+not))\s+answer(?:ed)?\b"
    r"|\b(?:your|that|the)\s+(?:reply|answer)\s+(?:did(?:n't|\s+not)|does(?:n't|\s+not))\s+answer\b"
    r"|\b(?:that(?:'s|\s+is|\s+was)\s+)?not\s+(?:what|the\s+question)\s+(?:i\s+)?asked\b"
    r"|\banswer\s+(?:my|the)\s+question\b"
    r"|\b(?:you\s+)?(?:avoided|evaded)\s+(?:my|the)\s+question\b",
    re.IGNORECASE,
)
CLARIFICATION_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
CLARIFICATION_STOPWORDS = {
    "answer", "asked", "did", "does", "from", "have", "people", "question",
    "that", "the", "their", "then", "they", "this", "towards", "what", "when",
    "where", "which", "who", "with", "you", "your",
}
CLARIFICATION_REPLY_WINDOW_SECONDS = 24 * 60 * 60


def _clarification_tokens(text: str) -> set[str]:
    """Return production-equivalent nontrivial clarification tokens."""
    without_handles = re.sub(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", text)
    return {
        token.lower()
        for token in CLARIFICATION_TOKEN_RE.findall(without_handles)
        if token.lower() not in CLARIFICATION_STOPWORDS
    }


def _clarification_request(
    target: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    own_auto_reply_ids: set[str],
    account_author_id: str,
    states: Sequence[dict[str, Any]],
    *,
    current: int,
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """Reconstruct a same-author direct clarification and its binding metadata."""
    thread_id = str(target.get("conversation_id") or target.get("id") or "")
    author_id = str(target.get("author_id") or "")
    clarification_records: dict[str, dict[str, Any]] = {}
    for state in states:
        records = state.get("clarification_reply_records", {})
        if not isinstance(records, dict):
            continue
        for record_thread_id, record in records.items():
            if not isinstance(record, dict):
                continue
            key = str(record_thread_id)
            try:
                completed_epoch = int(record.get("completed_epoch", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise AuditError(
                    f"clarification record has invalid completion time: {key}"
                ) from exc
            if completed_epoch <= 0:
                raise AuditError(
                    f"clarification record lacks a stable completion time: {key}"
                )
            normalised = dict(record)
            normalised["completed_epoch"] = completed_epoch
            existing = clarification_records.get(key)
            if existing is not None and value_sha256(existing) != value_sha256(normalised):
                raise AuditError(
                    f"clarification record conflicts across immutable snapshots: {key}"
                )
            clarification_records[key] = normalised
    records_at_candidate = {
        key: record
        for key, record in clarification_records.items()
        if int(record["completed_epoch"]) < current
    }
    gate_binding = {
        "terminal_thread_gate_checked": True,
        "thread_was_terminal": thread_id in records_at_candidate,
        "recent_author_gate_checked": True,
        "author_used_clarification_recently": any(
            str(record.get("author_id") or "") == author_id
            and int(record["completed_epoch"])
            > current - CLARIFICATION_REPLY_WINDOW_SECONDS
            for record in records_at_candidate.values()
        ),
        "candidate_processing_epoch": current,
        "clarification_window_seconds": CLARIFICATION_REPLY_WINDOW_SECONDS,
        "prior_record_count_at_candidate": len(records_at_candidate),
        "future_records_ignored": len(clarification_records) - len(records_at_candidate),
    }
    if gate_binding["thread_was_terminal"] or gate_binding[
        "author_used_clarification_recently"
    ]:
        return None, gate_binding
    parents = _references(target, "replied_to")
    if not parents or parents[0] not in own_auto_reply_ids:
        return None, gate_binding
    prior_id = parents[0]
    prior = cache.get(prior_id)
    if not prior or str(prior.get("author_id") or "") != account_author_id:
        return None, gate_binding
    original_ids = _references(prior, "replied_to")
    if not original_ids or original_ids[0] not in cache:
        return None, gate_binding
    original = cache[original_ids[0]]
    if (
        str(original.get("author_id") or "") != author_id
        or str(original.get("conversation_id") or original.get("id") or "") != thread_id
    ):
        return None, gate_binding
    question = str(original.get("text") or "")
    correction = str(target.get("text") or "")
    if "?" not in question:
        return None, gate_binding
    explicit = bool(CLARIFICATION_CUE_RE.search(correction))
    restated = "?" in correction and bool(
        _clarification_tokens(question) & _clarification_tokens(correction)
    )
    if not explicit and not restated:
        return None, gate_binding
    request = {
        "original_question": trim_context_text(question, 10_000),
        "correction": trim_context_text(correction, 10_000),
    }
    binding = {
        **gate_binding,
        "thread_id": thread_id,
        "prior_bot_reply_id": prior_id,
        "original_question_id": original_ids[0],
        "trigger": "explicit_correction" if explicit else "restated_question",
    }
    return request, binding


QUOTE_WORD_RE = re.compile(
    r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE
)
QUOTE_PASSAGE_FIELDS = (
    "verified_text", "quote_text", "historical_context", "source_event",
    "immediate_subject", "intended_argument", "literal_meaning",
    "broader_principle", "mechanism", "claimed_consequence", "date", "speaker",
    "entities",
)
RESOLVED_QUOTATION_FIELDS = (
    "quote_text", "verified_text", "verification_status", "research_confidence",
    "speaker", "source_event", "date", "historical_context", "literal_meaning",
    "intended_argument", "text_variation_notes", "stable_locator",
)
QUOTE_SENTINELS = {"", "unknown", "unresolved", "no verified text available."}


def _quote_words(value: Any) -> tuple[str, ...]:
    """Return production-equivalent case-folded quotation words."""
    return tuple(
        word.casefold().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
        for word in QUOTE_WORD_RE.findall(str(value or ""))
    )


def _contains_words(source: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    """Return whether a complete word sequence occurs contiguously."""
    if not candidate or len(candidate) > len(source):
        return False
    return any(
        source[index : index + len(candidate)] == candidate
        for index in range(len(source) - len(candidate) + 1)
    )


class _QuoteResolver:
    """A local-only replica of production's deterministic quotation resolver."""

    def __init__(self, research_dir: Path):
        """Load source-grounded packets and build exact word-match indexes."""
        self.research_dir = research_dir
        files = {
            "research_packets.json": research_dir / "research_packets.json",
            "corpus_manifest.json": research_dir / "corpus_manifest.json",
            "final_research_status.json": research_dir
            / "final_unresolved"
            / "final_research_status.json",
            "historical_context_source_role_audit.json": research_dir
            / "historical_context_source_role_audit.json",
            "historical_context_packet_corrections.json": research_dir
            / "historical_context_packet_corrections.json",
        }
        packet_document = json.loads(files["research_packets.json"].read_text(encoding="utf-8"))
        manifest = json.loads(files["corpus_manifest.json"].read_text(encoding="utf-8"))
        status = json.loads(files["final_research_status.json"].read_text(encoding="utf-8"))
        audit = json.loads(
            files["historical_context_source_role_audit.json"].read_text(encoding="utf-8")
        )
        corrections = json.loads(
            files["historical_context_packet_corrections.json"].read_text(encoding="utf-8")
        )
        source_file_hashes = audit.get("source_file_hashes")
        if not isinstance(source_file_hashes, dict) or not source_file_hashes:
            raise AuditError("quotation source-role audit lacks source-file hashes")
        for relative_name, expected_hash in sorted(source_file_hashes.items()):
            relative_path = Path(str(relative_name))
            if (
                relative_path.is_absolute()
                or ".." in relative_path.parts
                or not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash or ""))
            ):
                raise AuditError("quotation source-role audit contains an unsafe receipt")
            source_path = research_dir / relative_path
            if source_path.is_symlink() or not source_path.is_file():
                raise AuditError(f"quotation audit source is unavailable: {relative_name}")
            if file_sha256(source_path) != expected_hash:
                raise AuditError(f"quotation audit source hash disagrees: {relative_name}")
            files[str(relative_name)] = source_path
        implementation_names = (
            "historical_context_packet_corrections.py",
            "historical_context_source_curated_evidence.py",
            "historical_context_source_independent_review.py",
            "historical_context_source_openai_manifest.py",
            "historical_context_source_recovery.py",
            "historical_context_source_research_manifest.py",
            "historical_context_source_resolution.py",
            "historical_context_source_roles.py",
        )
        for name in implementation_names:
            path = _REPOSITORY_SOURCE_ROOT / name
            if path.is_symlink() or not path.is_file():
                raise AuditError(f"quotation validator source is unavailable: {name}")
            files[f"implementation/{name}"] = path
        self.source_hashes = {
            name: file_sha256(path) for name, path in sorted(files.items())
        }
        packets = packet_document.get("items")
        if not isinstance(packets, dict) or len(packets) != status.get("completed_quotes"):
            raise AuditError("quotation packet corpus count is inconsistent")
        manifest_ids = {
            str(row.get("quote_id")) for row in manifest.get("records", []) if isinstance(row, dict)
        }
        unresolved = set(status.get("unresolved_quote_ids", []))
        if set(packets) | unresolved != manifest_ids or set(packets) & unresolved:
            raise AuditError("quotation corpus completed/unresolved partition is inconsistent")
        eligible_ids = {
            quote_id
            for quote_id, packet in packets.items()
            if self._is_attribution_eligible(packet)
        }
        curated_path = research_dir / "historical_context_source_curated_evidence.json"
        try:
            attached_packets = validate_and_attach_audit(
                packets,
                unresolved,
                research_dir=research_dir,
                attribution_eligible_ids=eligible_ids,
                required=True,
            )
            curated_evidence = json.loads(curated_path.read_text(encoding="utf-8"))
            packets = apply_packet_corrections(
                corrections,
                attached_packets,
                curated_evidence,
            )
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            raise AuditError("quotation corpus sidecar validation failed") from exc
        self.packets: dict[str, dict[str, Any]] = {}
        self.primary_sources: dict[str, dict[str, str]] = {}
        self.evidence_ids: dict[str, list[str]] = {}
        self.match_texts: dict[str, tuple[tuple[str, ...], ...]] = {}
        for quote_id, packet in sorted(packets.items()):
            if not isinstance(packet, dict):
                raise AuditError("quotation packet is not an object")
            if quote_id not in eligible_ids:
                continue
            packet = dict(packet)
            self.packets[quote_id] = packet
            primary = self._select_primary_source(packet)
            self.primary_sources[quote_id] = primary
            source_record = {
                "quote_id": quote_id,
                "source_title": primary["title"],
                "source_url": primary["url"],
                "stable_locator": str(packet.get("stable_locator") or ""),
                "verification_status": str(packet.get("verification_status") or ""),
                "research_confidence": str(packet.get("research_confidence") or "low"),
                "sources": packet.get("sources", []),
            }
            source_hash = value_sha256(source_record)
            ids: list[tuple[str, str]] = []
            for field in QUOTE_PASSAGE_FIELDS:
                raw = packet.get(field)
                if isinstance(raw, list):
                    passage = "; ".join(
                        str(item).strip() for item in raw if str(item).strip()
                    )
                else:
                    passage = " ".join(str(raw or "").split())
                if not passage:
                    continue
                evidence_id = value_sha256(
                    {
                        "version": "claim-evidence-v2",
                        "quote_id": quote_id,
                        "field": field,
                        "passage": passage,
                        "source_hash": source_hash,
                    }
                )
                ids.append((field, evidence_id))
            self.evidence_ids[quote_id] = [
                evidence_id for _field, evidence_id in sorted(ids, key=lambda item: item)
            ]
            candidates: list[tuple[str, ...]] = []
            for field in ("verified_text", "quote_text"):
                text = " ".join(str(packet.get(field) or "").split())
                words = _quote_words(text)
                if text.casefold() not in QUOTE_SENTINELS and words and words not in candidates:
                    candidates.append(words)
            self.match_texts[quote_id] = tuple(candidates)

    @staticmethod
    def _is_attribution_eligible(packet: Any) -> bool:
        """Apply production's canonical principal-speaker eligibility rule."""
        if not isinstance(packet, dict):
            return False
        verification = str(packet.get("verification_status") or "").strip().casefold()
        if verification == "misattributed":
            return False
        speaker = re.sub(r"\s+", " ", str(packet.get("speaker") or "").strip())
        principal = re.split(r"\s*(?:\(|/)\s*", speaker, maxsplit=1)[0]
        return principal.strip().casefold() == "margaret thatcher"

    @staticmethod
    def _select_primary_source(packet: dict[str, Any]) -> dict[str, str]:
        """Apply production's audited public-source selection policy exactly."""
        role_priority = {
            "wording_verification": 0,
            "attribution_support": 1,
            "source_event_support": 2,
            "historical_context_support": 3,
        }
        sources = public_sources(packet)
        if not sources:
            return {
                "title": "No reliable source located",
                "url": "",
                "source_type": "unavailable",
            }
        source = min(
            sources,
            key=lambda row: min(
                (
                    role_priority.get(str(role), 9)
                    for role in row.get("roles", [])
                ),
                default=9,
            ),
        )
        return {
            "title": str(source["title"]),
            "url": str(source["url"]),
            "source_type": str(source["source_type"]),
        }

    def _matches(self, text: str) -> list[tuple[int, str, str]]:
        """Return production-equivalent full and 12-word quotation matches."""
        source_words = _quote_words(text)
        if not source_words:
            return []
        source_windows = {
            source_words[index : index + 12]
            for index in range(max(0, len(source_words) - 11))
        }
        matches: list[tuple[int, str, str]] = []
        for quote_id, candidates in self.match_texts.items():
            best: tuple[int, str] | None = None
            for candidate_words in candidates:
                if source_words == candidate_words:
                    candidate = (3, "exact_text")
                elif _contains_words(source_words, candidate_words):
                    candidate = (2, "full_text")
                elif len(candidate_words) >= 12 and any(
                    candidate_words[index : index + 12] in source_windows
                    for index in range(len(candidate_words) - 11)
                ):
                    candidate = (1, "unique_contiguous_excerpt")
                else:
                    continue
                if best is None or candidate[0] > best[0]:
                    best = candidate
            if best:
                matches.append((best[0], quote_id, best[1]))
        return matches

    def _strongest(self, text: str) -> tuple[tuple[str, str] | None, bool]:
        """Return one unique strongest quotation match and ambiguity status."""
        matches = self._matches(text)
        if not matches:
            return None, False
        strength = max(item[0] for item in matches)
        strongest = {(quote_id, basis) for score, quote_id, basis in matches if score == strength}
        quote_ids = {quote_id for quote_id, _basis in strongest}
        if len(quote_ids) != 1:
            return None, True
        quote_id = next(iter(quote_ids))
        basis = sorted(basis for candidate, basis in strongest if candidate == quote_id)[0]
        return (quote_id, basis), False

    def _record(self, quote_id: str, section: str, basis: str) -> dict[str, Any]:
        """Build source-grounded resolved quotation metadata."""
        packet = self.packets[quote_id]
        primary = self.primary_sources[quote_id]
        record = {
            "quote_id": quote_id,
            "matched_context_section": section,
            "match_basis": basis,
            **{field: packet.get(field) for field in RESOLVED_QUOTATION_FIELDS},
            "primary_source": {
                "title": primary["title"],
                "url": primary["url"],
            },
            "evidence_ids": self.evidence_ids[quote_id],
        }
        record["resolved_context_hash"] = value_sha256(record)
        return record

    def resolve(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve one quotation using production's incoming-first section order."""
        contextual: list[tuple[str, str]] = []
        quoted = context.get("quoted_post")
        if isinstance(quoted, dict):
            contextual.append(("quoted_post", str(quoted.get("text") or "")))
        for parent in reversed(context.get("parent_thread") or []):
            if isinstance(parent, dict) and parent.get("author_role") == "account":
                contextual.append(("parent_thread", str(parent.get("text") or "")))
        incoming, incoming_ambiguous = self._strongest(
            str(context.get("incoming_contribution") or "")
        )
        if incoming_ambiguous:
            return None
        contextual_matches: list[tuple[str, tuple[str, str]]] = []
        for section, text in contextual:
            match, ambiguous = self._strongest(text)
            if ambiguous:
                if incoming is None:
                    return None
                continue
            if match:
                contextual_matches.append((section, match))
        if incoming:
            quote_id, basis = incoming
            if contextual_matches and all(match[0] == quote_id for _section, match in contextual_matches):
                section, (_same, contextual_basis) = contextual_matches[0]
                return self._record(quote_id, section, contextual_basis)
            return self._record(quote_id, "incoming_contribution", basis)
        if contextual_matches:
            section, (quote_id, basis) = contextual_matches[0]
            return self._record(quote_id, section, basis)
        return None


def _history_by_target(history_corpus: Path) -> dict[str, dict[str, Any]]:
    """Load checksummed reconstructed history as outcome-only source evidence."""
    verify_sha256sums(history_corpus)
    manifest = json.loads((history_corpus / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("live_project_included") is not False:
        raise AuditError("history corpus included mutable live project input")
    if manifest.get("extractor_git_commit") != HISTORICAL_SOURCE_COMMITS[
        "reply_history_reconstruction"
    ]:
        raise AuditError("history corpus extractor commit differs")
    if manifest.get("extractor_source_sha256") != HISTORICAL_SOURCE_HASHES[
        "reply_history_reconstruction"
    ]:
        raise AuditError("history corpus extractor source hash differs")
    rows = read_jsonl_strict(history_corpus / "conversational_candidates.jsonl")
    by_target: dict[str, dict[str, Any]] = {}
    for row in rows:
        target_id = str(row.get("target_id") or "")
        if not POST_ID_RE.fullmatch(target_id) or target_id in by_target:
            raise AuditError(f"history has invalid or duplicate target: {target_id!r}")
        by_target[target_id] = row
    return by_target


def _history_identity_issues(
    history: dict[str, Any],
    candidate: dict[str, Any],
    target: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cross-check reconstructed-history identity against immutable log and cache evidence."""
    if not history:
        return [], {"history_row_present": False}
    expected_lane = "quote-tweet" if candidate["lane"] == "quote_tweet" else candidate["lane"]
    expected_thread = str(target.get("conversation_id") or target.get("id") or "")
    expected_quote = str(candidate.get("quoted_post_id") or "") or None
    comparisons = {
        "target_id": (str(history.get("target_id") or ""), str(candidate["target_id"])),
        "lane": (str(history.get("lane") or ""), expected_lane),
        "author_id": (str(history.get("author_id") or ""), str(candidate["author_id"])),
        "incoming_text_sha256": (
            text_sha256(str(history.get("incoming_text") or "")),
            text_sha256(str(target.get("text") or "")),
        ),
        "first_timestamp": (
            str(history.get("first_timestamp") or ""),
            str(candidate["first_consideration_timestamp"]),
        ),
    }
    if history.get("thread_id"):
        comparisons["thread_id"] = (str(history["thread_id"]), expected_thread)
    if history.get("quoted_post_id") or expected_quote:
        comparisons["quoted_post_id"] = (
            str(history.get("quoted_post_id") or "") or None,
            expected_quote,
        )
    issues = [
        {
            "reason": "fresh_history_identity_mismatch",
            "field": field,
            "history_value_sha256": value_sha256(observed),
            "immutable_source_value_sha256": value_sha256(expected),
        }
        for field, (observed, expected) in comparisons.items()
        if observed != expected
    ]
    status = str(history.get("reconstruction_status") or "")
    ambiguous_resolution: dict[str, Any] | None = None
    if status == "ambiguous":
        try:
            ambiguous_resolution = _ordered_conflict_resolution(history)
        except AuditError as exc:
            issues.append(
                {
                    "reason": "unresolved_ambiguous_fresh_history_identity",
                    "conflict_evidence_sha256": value_sha256(
                        history.get("conflict_evidence") or []
                    ),
                    "resolution_error_sha256": text_sha256(str(exc)),
                }
            )
        if ambiguous_resolution is None and not any(
            issue["reason"] == "unresolved_ambiguous_fresh_history_identity"
            for issue in issues
        ):
            issues.append(
                {
                    "reason": "unresolved_ambiguous_fresh_history_identity",
                    "conflict_evidence_sha256": value_sha256(
                        history.get("conflict_evidence") or []
                    ),
                }
            )
    return issues, {
        "history_row_present": True,
        "history_candidate_id": history.get("candidate_id"),
        "reconstruction_status": status,
        "ambiguous_identity_resolution": ambiguous_resolution,
        "identity_field_sha256_pairs": {
            field: {
                "history": value_sha256(observed),
                "immutable_source": value_sha256(expected),
                "match": observed == expected,
            }
            for field, (observed, expected) in comparisons.items()
        },
    }


def _processing_epoch(candidate: dict[str, Any]) -> int:
    """Return the first immutable consideration time as an integer epoch."""
    return int(parse_utc(candidate["first_consideration_timestamp"]).timestamp())


def build_replay_context_audit(
    candidate: dict[str, Any],
    target: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
    own_auto_reply_ids: set[str],
    account_author_id: str,
    states: Sequence[dict[str, Any]],
    frozen_current_date: str,
    quote_resolver: _QuoteResolver,
    reply_candidate_by_post_id: dict[str, str],
    reply_candidate_ids_by_text_sha256: dict[str, set[str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reconstruct and audit one production-faithful frozen replay context."""
    target_id = str(candidate["target_id"])
    candidate_timestamp_raw = str(target.get("created_at") or "")
    if not candidate_timestamp_raw:
        raise AuditError(f"candidate target lacks created_at: {target_id}")
    candidate_timestamp = utc_text(_parse_source_timestamp(candidate_timestamp_raw))
    incoming_exact = str(target.get("text") or "") == str(candidate["incoming_text"])
    problems: list[dict[str, Any]] = []
    if not incoming_exact:
        problems.append({"reason": "incoming_contribution_source_mismatch"})
    thread_id = str(target.get("conversation_id") or target_id)
    lane = "quote_tweet" if candidate["lane"] == "quote_tweet" else str(candidate["lane"])
    temporal_records: list[dict[str, Any]] = []
    parent_thread: list[dict[str, str]] = []
    quoted_post: dict[str, str] | None = None
    parent_binding: list[dict[str, Any]] = []
    quote_binding: dict[str, Any] | None = None

    if lane == "quote_tweet":
        quoted_ids = _references(target, "quoted")
        retweeted_ids = _references(target, "retweeted")
        expected_quote = str(candidate.get("quoted_post_id") or "")
        if retweeted_ids or quoted_ids != [expected_quote]:
            problems.append(
                {
                    "reason": "quote_tweet_structured_identity_mismatch",
                    "expected_quoted_post_id": expected_quote,
                    "structured_quoted_post_ids": quoted_ids,
                    "structured_retweeted_post_ids": retweeted_ids,
                }
            )
        original = cache.get(expected_quote)
        if not original:
            problems.append(
                {"reason": "missing_quote_tweet_context", "quoted_post_id": expected_quote}
            )
        else:
            quoted_post = _context_post(original, account_author_id)
            quote_binding = _record_temporal_receipt(
                "quoted_post", original, provenance
            )
            temporal_records.append(quote_binding)
    else:
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        parent_ids = _references(target, "replied_to")
        parent_id = parent_ids[0] if parent_ids else ""
        immediate_parent_id = parent_id
        while parent_id and len(chain) < 3:
            if parent_id in seen:
                problems.append({"reason": "parent_chain_loop", "post_id": parent_id})
                break
            seen.add(parent_id)
            parent = cache.get(parent_id)
            if not parent:
                problems.append(
                    {"reason": "missing_parent_context", "post_id": parent_id}
                )
                break
            chain.append(parent)
            next_ids = _references(parent, "replied_to")
            parent_id = next_ids[0] if next_ids else ""
        chain.reverse()
        processing_epoch = _processing_epoch(candidate)
        cap_context = [
            record
            for record in cache.values()
            if str(record.get("id") or "") != target_id
            and record.get("post_type") in {"author_cap_context", "author_cap_quote_context"}
            and str(record.get("author_id") or "") == str(target.get("author_id") or "")
            and str(record.get("conversation_id") or record.get("id") or "") == thread_id
            and int(record.get("cached_epoch", 0) or 0) < processing_epoch
        ]
        recovered_cap_quote: dict[str, str] | None = None
        recovered_cap_quote_binding: dict[str, Any] | None = None
        for cap_record in reversed(
            sorted(
                cap_context,
                key=lambda record: (
                    int(str(record.get("id")))
                    if str(record.get("id", "")).isdigit()
                    else 0,
                    str(record.get("id") or ""),
                ),
            )
        ):
            if cap_record.get("post_type") != "author_cap_quote_context":
                continue
            cap_quote_ids = _references(cap_record, "quoted")
            if not cap_quote_ids:
                continue
            recovered_quote_id = cap_quote_ids[0]
            recovered_quote = cache.get(recovered_quote_id)
            if recovered_quote is None:
                recovered_cap_quote = {
                    "post_id": recovered_quote_id or "unknown",
                    "author_role": "unknown",
                    "text": "[Quoted post unavailable.]",
                }
                recovered_cap_quote_binding = {
                    "kind": "quoted_post",
                    "post_id": recovered_quote_id or "unknown",
                    "recovery_basis": "newest_author_cap_quote_context",
                    "source_cap_post_id": str(cap_record.get("id") or ""),
                    "source_record_missing": True,
                }
            else:
                recovered_cap_quote = _context_post(
                    recovered_quote, account_author_id
                )
                recovered_cap_quote_binding = _record_temporal_receipt(
                    "quoted_post", recovered_quote, provenance
                )
                recovered_cap_quote_binding["recovery_basis"] = (
                    "newest_author_cap_quote_context"
                )
                recovered_cap_quote_binding["source_cap_post_id"] = str(
                    cap_record.get("id") or ""
                )
            break
        merged = {
            str(record.get("id") or ""): record
            for record in [*chain, *cap_context]
            if str(record.get("id") or "") and str(record.get("id")) != target_id
        }
        selected = sorted(
            merged.values(),
            key=lambda record: (
                int(str(record.get("id"))) if str(record.get("id", "")).isdigit() else 0,
                str(record.get("id") or ""),
            ),
        )[-3:]
        if immediate_parent_id and immediate_parent_id in cache and all(
            str(record.get("id") or "") != immediate_parent_id for record in selected
        ):
            selected = sorted(
                [cache[immediate_parent_id], *selected[-2:]],
                key=lambda record: (
                    int(str(record.get("id")))
                    if str(record.get("id", "")).isdigit()
                    else 0,
                    str(record.get("id") or ""),
                ),
            )
        remaining = 1500
        for record in reversed(selected):
            post = _context_post(record, account_author_id, min(500, remaining))
            remaining -= len(post["text"])
            parent_thread.insert(0, post)
            receipt = _record_temporal_receipt("parent_thread", record, provenance)
            parent_binding.insert(0, receipt)
            temporal_records.append(receipt)
        quoted_ids = _references(target, "quoted")
        if quoted_ids:
            quoted = cache.get(quoted_ids[0])
            if not quoted:
                problems.append(
                    {"reason": "missing_quoted_post_context", "post_id": quoted_ids[0]}
                )
            else:
                quoted_post = _context_post(quoted, account_author_id)
                quote_binding = _record_temporal_receipt(
                    "quoted_post", quoted, provenance
                )
                temporal_records.append(quote_binding)
        elif recovered_cap_quote is not None:
            quoted_post = recovered_cap_quote
            quote_binding = recovered_cap_quote_binding
            if quote_binding is not None and not quote_binding.get(
                "source_record_missing"
            ):
                temporal_records.append(quote_binding)

    clarification, clarification_binding = _clarification_request(
        target,
        cache,
        own_auto_reply_ids,
        account_author_id,
        states,
        current=_processing_epoch(candidate),
    )
    replay_context: dict[str, Any] = {
        "target_id": target_id,
        "thread_id": thread_id,
        "lane": lane,
        "incoming_contribution": trim_context_text(str(target.get("text") or ""), 10_000),
        "quoted_post": quoted_post,
        "parent_thread": parent_thread,
        "clarification_request": clarification,
        "current_date": frozen_current_date,
    }
    production_context = dict(replay_context)
    production_context["current_date"] = parse_utc(
        candidate["first_consideration_timestamp"]
    ).astimezone(LONDON).strftime("%Y-%m-%d")

    recent_rows: list[dict[str, Any]] = []
    processing_epoch = _processing_epoch(candidate)
    candidate_time = parse_utc(candidate_timestamp)
    for record in cache.values():
        if (
            record.get("post_type") != "auto_reply"
            or str(record.get("author_id") or "") != account_author_id
            or not str(record.get("text") or "").strip()
            or int(record.get("cached_epoch", 0) or 0) >= processing_epoch
        ):
            continue
        created_at = record.get("created_at")
        if not created_at or _parse_source_timestamp(str(created_at)) >= candidate_time:
            continue
        recent_rows.append(record)
    recent_rows.sort(
        key=lambda record: (
            int(record.get("cached_epoch", 0) or 0), str(record.get("id") or "")
        ),
        reverse=True,
    )
    recent_rows = recent_rows[:20]
    recent_receipts = [
        _record_temporal_receipt("recent_account_reply", record, provenance)
        for record in recent_rows
    ]
    temporal_records.extend(recent_receipts)
    temporal_violations = context_temporal_violations(
        temporal_records, candidate_timestamp
    )
    problems.extend(temporal_violations)
    current_candidate_id = str(candidate.get("candidate_id") or "")
    self_reply_leak_evidence: list[dict[str, Any]] = []
    for record in recent_rows:
        reply_id = str(record.get("id") or "")
        structural_target = target_id in _references(record, "replied_to")
        provenance_candidate_id = reply_candidate_by_post_id.get(reply_id)
        text_candidate_ids = reply_candidate_ids_by_text_sha256.get(
            text_sha256(clean_context_text(str(record.get("text") or ""))), set()
        )
        bases: list[str] = []
        if structural_target:
            bases.append("reply_to_target_reference")
        if current_candidate_id and provenance_candidate_id == current_candidate_id:
            bases.append("reply_post_candidate_identity")
        if current_candidate_id and current_candidate_id in text_candidate_ids:
            bases.append("historical_reply_text_identity")
        if bases:
            self_reply_leak_evidence.append(
                {
                    "post_id": reply_id,
                    "bases": bases,
                    "reply_candidate_id": provenance_candidate_id,
                    "reply_text_sha256": text_sha256(
                        clean_context_text(str(record.get("text") or ""))
                    ),
                }
            )
    self_reply_leaks = [item["post_id"] for item in self_reply_leak_evidence]
    if self_reply_leaks:
        problems.append(
            {
                "reason": "historical_reply_leaked_into_own_input",
                "post_ids": self_reply_leaks,
                "identity_evidence": self_reply_leak_evidence,
            }
        )

    persisted_records = [
        item
        for state in states
        for item in state.get("ai_reply_history", [])
        if isinstance(item, dict) and str(item.get("target_id") or "") == target_id
    ]
    persisted_unique = {
        value_sha256(item): item for item in persisted_records
    }
    persisted_checks: dict[str, Any] = {
        "matching_persisted_history_record_count": len(persisted_unique),
        "contribution_hash_checked": False,
        "production_context_hash_checked": False,
    }
    if persisted_unique:
        persisted = max(
            persisted_unique.values(), key=lambda item: int(item.get("attempt_epoch", 0) or 0)
        )
        expected_contribution = text_sha256(replay_context["incoming_contribution"])
        expected_context = value_sha256(production_context)
        persisted_checks.update(
            {
                "contribution_hash_checked": True,
                "contribution_hash_match": persisted.get("contribution_hash")
                == expected_contribution,
                "production_context_hash_checked": True,
                "production_context_hash_match": persisted.get("context_hash")
                == expected_context,
                "persisted_context_hash": persisted.get("context_hash"),
                "reconstructed_production_context_hash": expected_context,
                "persisted_resolved_quote_id": persisted.get("resolved_quote_id"),
                "persisted_resolved_quote_context_hash": persisted.get(
                    "resolved_quote_context_hash"
                ),
            }
        )
        if not persisted_checks["contribution_hash_match"]:
            problems.append({"reason": "persisted_contribution_hash_mismatch"})
        if not persisted_checks["production_context_hash_match"]:
            problems.append({"reason": "persisted_production_context_hash_mismatch"})

    resolved_quotation = quote_resolver.resolve(replay_context)
    if persisted_unique:
        persisted_quote_id = persisted_checks.get("persisted_resolved_quote_id")
        resolved_quote_id = (
            resolved_quotation.get("quote_id") if resolved_quotation else None
        )
        persisted_checks["resolved_quote_id_match"] = persisted_quote_id == resolved_quote_id
        if persisted_quote_id != resolved_quote_id:
            problems.append({"reason": "persisted_resolved_quote_id_mismatch"})
        persisted_quote_hash = persisted_checks.get(
            "persisted_resolved_quote_context_hash"
        )
        reconstructed_quote_hash = (
            resolved_quotation.get("resolved_context_hash")
            if resolved_quotation
            else None
        )
        persisted_checks["reconstructed_resolved_quote_context_hash"] = (
            reconstructed_quote_hash
        )
        persisted_checks["resolved_quote_context_hash_match"] = (
            persisted_quote_hash == reconstructed_quote_hash
        )
        if persisted_quote_hash != reconstructed_quote_hash:
            problems.append(
                {"reason": "persisted_resolved_quote_context_hash_mismatch"}
            )

    flags = context_dependency_flags(replay_context)
    component_hashes = {
        "incoming_contribution_sha256": text_sha256(
            replay_context["incoming_contribution"]
        ),
        "quoted_post_sha256": value_sha256(quoted_post),
        "parent_thread_sha256": value_sha256(parent_thread),
        "clarification_request_sha256": value_sha256(clarification),
        "recent_account_replies_sha256": value_sha256(
            [str(record["text"]) for record in recent_rows]
        ),
        "resolved_quotation_sha256": value_sha256(resolved_quotation),
        "replay_context_sha256": value_sha256(replay_context),
        "production_context_at_candidate_sha256": value_sha256(production_context),
    }
    audit = {
        "candidate_id": candidate.get("candidate_id"),
        "target_id": target_id,
        "candidate_timestamp": candidate_timestamp,
        "first_consideration_timestamp": candidate["first_consideration_timestamp"],
        "lane": lane,
        "thread_id": thread_id,
        "incoming_exact_source_match": incoming_exact,
        "replay_context": replay_context,
        "production_context_at_candidate": production_context,
        "current_date_convention": (
            "one fixed audit-pack date derived from audit created_at[:10], matching "
            "the frozen replay convention; production historical date is recorded separately"
        ),
        "parent_context_bindings": parent_binding,
        "quoted_post_binding": quote_binding,
        "clarification_binding": clarification_binding,
        "recent_account_replies": [
            {
                "post_id": str(record.get("id") or ""),
                "text": str(record.get("text") or ""),
                "created_at": record.get("created_at"),
                "cached_epoch": record.get("cached_epoch"),
                "source_record_sha256": value_sha256(record),
            }
            for record in recent_rows
        ],
        "recent_account_reply_order": "newest_first_by_cached_epoch_then_id",
        "recent_account_reply_limit": 20,
        "resolved_quotation": resolved_quotation,
        "context_dependency_flags": flags,
        "persisted_context_verification": persisted_checks,
        "component_sha256": component_hashes,
        "temporal_violations": temporal_violations,
        "historical_reply_self_leak_post_ids": self_reply_leaks,
        "historical_reply_self_leak_identity_evidence": self_reply_leak_evidence,
        "automatic_context_clearance": not flags and not problems,
        "manual_context_review_required": bool(flags) and not problems,
        "context_unrecoverable": bool(problems),
        "problems": problems,
    }
    audit["context_audit_record_sha256"] = value_sha256(audit)
    return audit, problems


def candidate_contamination(
    candidate: dict[str, Any],
    development_cases: Sequence[dict[str, Any]],
    development_threads: set[str],
    development_contexts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic development-ID, thread, text, and context leakage matches."""
    reasons: list[dict[str, Any]] = []
    candidate_id = str(candidate.get("candidate_id") or "")
    target_id = str(candidate.get("target_id") or "")
    thread_id = str(candidate.get("thread_id") or "")
    incoming = str(candidate.get("incoming_contribution") or "")
    context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
    flags = candidate.get("context_dependency_flags") or context_dependency_flags(context)
    for development in development_cases:
        development_id = str(development.get("candidate_id") or "")
        if candidate_id and candidate_id == development_id:
            reasons.append(
                {"reason": "development_candidate_id", "development_candidate_id": development_id}
            )
        if target_id and target_id == str(development.get("target_id") or ""):
            reasons.append(
                {"reason": "development_target_id", "development_candidate_id": development_id}
            )
        development_text = str(
            development.get("incoming_contribution")
            or development.get("_incoming_contribution")
            or ""
        )
        if not incoming or not development_text:
            continue
        left = normalise_incoming(incoming)
        right = normalise_incoming(development_text)
        if left == right:
            reasons.append(
                {
                    "reason": "exact_normalised_incoming_duplicate",
                    "development_candidate_id": development_id,
                    "candidate_normalised_sha256": text_sha256(left),
                    "development_normalised_sha256": text_sha256(right),
                    "similarity": 1.0,
                }
            )
        else:
            ratio = near_duplicate_ratio(incoming, development_text)
            if ratio >= NEAR_DUPLICATE_THRESHOLD:
                reasons.append(
                    {
                        "reason": "near_duplicate_incoming",
                        "development_candidate_id": development_id,
                        "candidate_normalised_sha256": text_sha256(left),
                        "development_normalised_sha256": text_sha256(right),
                        "similarity": round(ratio, 6),
                        "threshold": NEAR_DUPLICATE_THRESHOLD,
                        "algorithm": NEAR_DUPLICATE_VERSION,
                    }
                )
    material_thread_dependency = bool(
        flags or context.get("parent_thread") or context.get("quoted_post")
    )
    if thread_id and thread_id in development_threads and material_thread_dependency:
        reasons.append(
            {"reason": "development_thread_context_leakage", "thread_id": thread_id}
        )
    candidate_context_payload = context_leakage_payload(context) if context else {}
    candidate_context_text = (
        context_leakage_text(context, candidate_dependent_only=True)
        if context
        else ""
    )
    for development in development_contexts:
        development_context = development.get("context", development)
        if not isinstance(development_context, dict) or not candidate_context_text:
            continue
        development_payload = context_leakage_payload(development_context)
        development_text = context_leakage_text(
            development_context, candidate_dependent_only=True
        )
        if candidate_context_payload == development_payload:
            reasons.append(
                {
                    "reason": "exact_context_payload_duplicate",
                    "development_candidate_id": development.get("candidate_id"),
                    "candidate_context_sha256": value_sha256(
                        candidate_context_payload
                    ),
                    "development_context_sha256": value_sha256(
                        development_payload
                    ),
                    "similarity": 1.0,
                }
            )
        else:
            ratio = near_duplicate_ratio(candidate_context_text, development_text)
            if ratio >= NEAR_DUPLICATE_THRESHOLD:
                reasons.append(
                    {
                        "reason": "near_duplicate_context_payload",
                        "development_candidate_id": development.get("candidate_id"),
                        "candidate_context_sha256": value_sha256(
                            candidate_context_payload
                        ),
                        "development_context_sha256": value_sha256(
                            development_payload
                        ),
                        "candidate_dependent_context_sha256": text_sha256(
                            normalise_incoming(candidate_context_text)
                        ),
                        "development_candidate_dependent_context_sha256": text_sha256(
                            normalise_incoming(development_text)
                        ),
                        "near_similarity_scope": (
                            "candidate-dependent incoming/quote/parent/clarification; "
                            "shared recent-reply history cannot independently trigger exclusion"
                        ),
                        "similarity": round(ratio, 6),
                        "threshold": NEAR_DUPLICATE_THRESHOLD,
                        "algorithm": NEAR_DUPLICATE_VERSION,
                    }
                )
    unique: dict[str, dict[str, Any]] = {}
    for reason in reasons:
        unique[value_sha256(reason)] = reason
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("reason")), str(item.get("development_candidate_id"))
        ),
    )


def deduplicate_fresh_candidates(
    candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep one time-first representative per exact fresh normalised contribution."""
    ordered = sorted(
        candidates,
        key=lambda row: (str(row["candidate_timestamp"]), str(row["target_id"])),
    )
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in ordered:
        normalised = normalise_incoming(str(candidate["incoming_contribution"]))
        groups[normalised].append(candidate)
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    group_receipts: list[dict[str, Any]] = []
    for normalised, members in sorted(
        groups.items(), key=lambda item: (text_sha256(item[0]), item[0])
    ):
        representative = members[0]
        kept.append(representative)
        if len(members) == 1:
            continue
        member_ids = [str(row["candidate_id"]) for row in members]
        group_receipts.append(
            {
                "normalised_incoming_sha256": text_sha256(normalised),
                "representative_candidate_id": representative["candidate_id"],
                "representative_target_id": representative["target_id"],
                "member_candidate_ids": member_ids,
                "member_count": len(members),
                "pairwise_exact_match_count": len(members) * (len(members) - 1) // 2,
                "selection_rule": "earliest_candidate_timestamp_then_target_id",
            }
        )
        for duplicate in members[1:]:
            excluded.append(
                {
                    "candidate_id": duplicate["candidate_id"],
                    "target_id": duplicate["target_id"],
                    "lane": duplicate["lane"],
                    "candidate_timestamp": duplicate["candidate_timestamp"],
                    "historical_outcome": duplicate.get("historical_outcome"),
                    "source_record_fingerprint": duplicate.get(
                        "source_record_fingerprint"
                    ),
                    "context_audit_record_sha256": duplicate.get(
                        "context_audit_record_sha256"
                    ),
                    "exclusion_reasons": [
                        {
                            "reason": "fresh_exact_normalised_incoming_duplicate",
                            "matched_fresh_candidate_id": representative["candidate_id"],
                            "matched_fresh_target_id": representative["target_id"],
                            "normalised_incoming_sha256": text_sha256(normalised),
                            "similarity": 1.0,
                            "selection_rule": (
                                "earliest_candidate_timestamp_then_target_id"
                            ),
                        }
                    ],
                }
            )
    return (
        sorted(kept, key=lambda row: (row["candidate_timestamp"], row["target_id"])),
        sorted(
            excluded,
            key=lambda row: (row["candidate_timestamp"], row["target_id"]),
        ),
        {
            "algorithm": "fresh-exact-normalised-dedup-v1",
            "normalisation_version": NORMALISATION_VERSION,
            "representative_rule": "earliest_candidate_timestamp_then_target_id",
            "duplicate_group_count": len(group_receipts),
            "excluded_candidate_count": len(excluded),
            "pairwise_exact_match_count": sum(
                row["pairwise_exact_match_count"] for row in group_receipts
            ),
            "groups": group_receipts,
        },
    )


def semantic_inventory(incoming: str, context: dict[str, Any]) -> dict[str, Any]:
    """Assign transparent provisional contribution-only coverage tags and one stratum."""
    folded = clean_context_text(incoming).casefold()
    tags: list[str] = []
    courtesy = bool(
        re.search(
            r"\b(?:thank(?:s| you)?|grateful|appreciate|well done|congratulations|"
            r"best wishes|good luck|lovely|welcome)\b",
            folded,
        )
    ) and not bool(re.search(r"\b(?:because|since|therefore|however|but)\b", folded))
    if courtesy:
        tags.append("genuine_social_courtesy")
    if re.search(r"\b(?:agree|right|indeed|exactly|true)\b", folded) and re.search(
        r"\b(?:because|since|principle|reason|therefore|liberty|responsibility)\b",
        folded,
    ):
        tags.append("substantive_agreement_with_reason_or_principle")
    if re.search(
        r"\b(?:disagree|wrong|however|but|nonsense|challenge|critic|why should|"
        r"not true|false)\b",
        folded,
    ):
        tags.append("civil_challenge_criticism_or_disagreement")
    if re.search(
        r"\b(?:like|unlike|analogy|distinction|distinguish|recommend|should|ought|"
        r"instead|rather than)\b",
        folded,
    ):
        tags.append("analogy_distinction_or_recommendation")
    factual_question = "?" in folded and bool(
        re.search(
            r"\b(?:who|what|when|where|which|how many|how much|did|was|were|"
            r"source|history|historical|date|quote)\b",
            folded,
        )
    )
    if factual_question:
        tags.append("direct_factual_or_historical_question")
    sensitive = bool(
        re.search(
            r"\b(?:killed|murder|criminal|traitor|terrorist|rape|abuse|corrupt|"
            r"stole|fraud|disease|diagnosis|race|ethnic|religion|suicide|weapon)\b",
            folded,
        )
    )
    allegation = bool(
        re.search(r"\b(?:is|are|was|were|did|has|have)\b", folded)
        and re.search(r"\b(?:criminal|corrupt|killed|stole|fraud|traitor|terrorist)\b", folded)
    )
    if sensitive or allegation:
        tags.append("unsupported_allegation_or_sensitive_factual_correction_context")
    wit = bool(re.search(r"\b(?:joke|funny|irony|ironic|pun|haha|lol|wit)\b", folded))
    if wit and not sensitive:
        tags.append("safe_contribution_specific_wit_opportunity")
    if sensitive and allegation:
        tags.append("justified_safety_no_reply")
    if not tags:
        tags.append("other_safe_conversational_contribution")
    if "justified_safety_no_reply" in tags:
        primary = "justified_safety_no_reply"
    elif "genuine_social_courtesy" in tags:
        primary = "genuine_social_courtesy"
    elif "direct_factual_or_historical_question" in tags:
        primary = "factual_or_historical_question"
    elif "civil_challenge_criticism_or_disagreement" in tags:
        primary = "civil_challenge_or_disagreement"
    elif "safe_contribution_specific_wit_opportunity" in tags:
        primary = "safe_wit_opportunity"
    elif "other_safe_conversational_contribution" in tags:
        primary = "other_safe_conversational_contribution"
    else:
        primary = "formulaic_substantive_posted"
    return {
        "semantic_tags": [tag for tag in SEMANTIC_TAGS if tag in tags],
        "proposed_primary_stratum": primary,
        "classification_version": SEMANTIC_INVENTORY_VERSION,
        "classification_inputs": "incoming contribution only; bounded context is retained for review",
        "historical_outcome_used": False,
        "profile_outputs_used": False,
    }


def _markdown_quote(text: str) -> str:
    """Render exact text as a Markdown block quote."""
    return "\n".join(f"> {line}" for line in (str(text).splitlines() or [""]))


def render_manual_context_review(rows: Sequence[dict[str, Any]]) -> str:
    """Render a blinded context-only manual review document."""
    lines = [
        "# Fresh reply candidate context review",
        "",
        "This pack contains context only. It contains no profile labels, generated output, "
        "scores, quality judgements, costs, retries, execution order, or reliability outcomes.",
        "",
    ]
    forbidden_keys = {
        "historical_outcome", "historical_public_reply", "actual_reply_text",
        "profile", "response", "score", "cost", "retry",
    }
    for row in sorted(rows, key=lambda item: str(item["candidate_id"])):
        if forbidden_keys & set(row):
            raise AuditError("manual context row contains a blinded forbidden field")
        context = row["replay_context"]
        lines.extend(
            [
                f"## {row['candidate_id']}",
                "",
                f"Proposed primary stratum: {row['proposed_primary_stratum']}",
                "",
                f"Lane: {context['lane']}",
                "",
                "Incoming contribution:",
                "",
                _markdown_quote(context["incoming_contribution"]),
                "",
                "Quoted-post context:",
                "",
            ]
        )
        quoted = context.get("quoted_post")
        if quoted:
            lines.extend(
                [
                    f"Post ID: {quoted['post_id']}",
                    "",
                    f"Author role: {quoted['author_role']}",
                    "",
                    _markdown_quote(str(quoted.get("text") or "")),
                    "",
                ]
            )
        else:
            lines.extend(["(none supplied)", ""])
        lines.extend(["Bounded parent thread (oldest to newest):", ""])
        parents = context.get("parent_thread") or []
        if not parents:
            lines.extend(["(none supplied)", ""])
        for parent in parents:
            lines.extend(
                [
                    f"- {parent['post_id']} ({parent['author_role']})",
                    "",
                    _markdown_quote(str(parent.get("text") or "")),
                    "",
                ]
            )
        clarification = context.get("clarification_request")
        lines.extend(["Clarification context:", ""])
        if clarification:
            lines.extend(
                [
                    "Original question:",
                    "",
                    _markdown_quote(str(clarification.get("original_question") or "")),
                    "",
                    "Correction:",
                    "",
                    _markdown_quote(str(clarification.get("correction") or "")),
                    "",
                ]
            )
        else:
            lines.extend(["(none supplied)", ""])
        lines.extend(["Recent account replies (production newest-first bound):", ""])
        recent_replies = row.get("recent_account_replies") or []
        if not recent_replies:
            lines.extend(["(none supplied)", ""])
        for recent_reply in recent_replies:
            lines.extend(
                [
                    f"- {recent_reply['post_id']} ({recent_reply['created_at']})",
                    "",
                    _markdown_quote(str(recent_reply.get("text") or "")),
                    "",
                ]
            )
        lines.extend(["Resolved quotation metadata:", ""])
        resolved_quotation = row.get("resolved_quotation")
        if resolved_quotation:
            lines.extend(
                [
                    "```json",
                    json.dumps(
                        resolved_quotation,
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    ),
                    "```",
                    "",
                ]
            )
        else:
            lines.extend(["(none resolved)", ""])
        flags = row.get("context_dependency_flags") or []
        lines.extend(
            [
                "Context-dependency flags: " + (", ".join(flags) if flags else "none"),
                "",
                "Manual decision: ____________________",
                "",
                "Reviewer note: ____________________",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_manual_clearance_csv(
    rows: Sequence[dict[str, Any]], context_audit_sha256: str
) -> str:
    """Render a clearance template whose manual decision and note are blank."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "candidate_id",
            "context_audit_sha256",
            "candidate_context_audit_record_sha256",
            "manual_decision",
            "reviewer_note",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    for row in sorted(rows, key=lambda item: str(item["candidate_id"])):
        writer.writerow(
            {
                "candidate_id": row["candidate_id"],
                "context_audit_sha256": context_audit_sha256,
                "candidate_context_audit_record_sha256": row[
                    "context_audit_record_sha256"
                ],
                "manual_decision": "",
                "reviewer_note": "",
            }
        )
    return output.getvalue()


def audit_manifest_control_fields() -> dict[str, Any]:
    """Return immutable zero-action counters for the no-cost manifest."""
    return {
        "model_calls": 0,
        "provider_http_requests": 0,
        "posting_actions": 0,
        "production_state_changes": 0,
        "live_project_included": False,
    }


def _write_json(path: Path, value: Any) -> None:
    """Write one deterministic JSON document to a new output file."""
    if path.exists():
        raise AuditError(f"refusing to overwrite output: {path}")
    path.write_bytes(json_document_bytes(value))
    path.chmod(0o600)


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """Write deterministic compact JSONL records to a new output file."""
    if path.exists():
        raise AuditError(f"refusing to overwrite output: {path}")
    path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))
    path.chmod(0o600)


def _write_text(path: Path, value: str) -> None:
    """Write exact UTF-8 text to a new output file."""
    if path.exists():
        raise AuditError(f"refusing to overwrite output: {path}")
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def _pool_source_receipt(dataset: str) -> dict[str, Any]:
    """Return a read-only receipt for the ZFS pool and its physical devices."""
    pool = dataset.split("/", 1)[0]
    try:
        completed = _run_read_only_command("zpool", ["status", "-P", pool])
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"could not inspect ZFS pool {pool}") from exc
    device_paths = sorted(
        set(re.findall(r"(?m)^\s+(/dev/\S+)\s+", completed.stdout))
    )
    return {
        "pool": pool,
        "dataset": dataset,
        "device_paths": device_paths,
        "zpool_status_sha256": text_sha256(completed.stdout),
        "inspection_command": f"zpool status -P {pool}",
    }


def _snapshot_source_inventory(
    repository: Path,
    snapshot_root: Path,
    dataset: str,
    project_relative_path: Path,
    snapshot_names: Sequence[str],
) -> tuple[list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    """Validate explicitly selected immutable snapshots and source-file identities."""
    projects: list[tuple[dict[str, Any], Path]] = []
    inventory: list[dict[str, Any]] = []
    previous_creation = -1
    for snapshot_name in snapshot_names:
        metadata = _snapshot_properties(dataset, snapshot_name)
        if metadata["creation_epoch"] <= previous_creation:
            raise AuditError("selected snapshots are not in strictly increasing creation order")
        previous_creation = metadata["creation_epoch"]
        project = ensure_safe_source_path(
            snapshot_root / snapshot_name / project_relative_path,
            snapshot_root,
        )
        state_path = project / "bot_state.json"
        source_paths = {
            "bot_state.json": file_sha256(state_path),
            "mrsMThatcher2.py": file_sha256(project / "mrsMThatcher2.py"),
            "reply_strategy.py": file_sha256(project / "reply_strategy.py"),
        }
        snapshot_head = str(_git(project, "rev-parse", "HEAD")).strip()
        item = {
            **metadata,
            "source_project_path": str(project),
            "source_file_sha256": source_paths,
            "source_project_git_head": snapshot_head,
            "read_only_snapshot": True,
        }
        projects.append((item, project))
        inventory.append(item)
    hybrid_main_bytes = _git(
        repository, "show", f"{HYBRID_PROFILE_COMMIT}:mrsMThatcher2.py", text=False
    )
    assert isinstance(hybrid_main_bytes, bytes)
    inventory_semantics_sha = hashlib.sha256(hybrid_main_bytes).hexdigest()
    for item in inventory:
        observed_semantics_sha = item["source_file_sha256"]["mrsMThatcher2.py"]
        if observed_semantics_sha != inventory_semantics_sha:
            raise AuditError(
                "snapshot context semantics source differs from the hybrid reference: "
                + item["snapshot_name"]
            )
        item["context_semantics_reference_commit"] = HYBRID_PROFILE_COMMIT
        item["context_semantics_reference_source_sha256"] = inventory_semantics_sha
        item["context_semantics_source_matches_reference"] = True
    return projects, inventory


def _ordered_conflict_resolution(history: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve retry outcomes only when immutable attempt chronology is complete."""
    conflicts = history.get("conflict_evidence") or []
    if not conflicts:
        return None
    attempts = history.get("terminal_attempt_history") or []
    if not attempts or any(
        not isinstance(item, dict)
        or item.get("chronology_status") != "placed"
        or not item.get("timestamp")
        for item in attempts
    ):
        raise AuditError("historical candidate conflict lacks ordered immutable provenance")
    timestamps = [parse_utc(str(item["timestamp"])) for item in attempts]
    if timestamps != sorted(timestamps):
        raise AuditError("historical candidate conflict chronology is not ordered")
    return {
        "status": "resolved_by_ordered_immutable_attempt_provenance",
        "conflicting_fields": sorted(
            str(item.get("field") or "") for item in conflicts if isinstance(item, dict)
        ),
        "attempt_count": len(attempts),
        "first_attempt_timestamp": str(attempts[0]["timestamp"]),
        "final_attempt_timestamp": str(attempts[-1]["timestamp"]),
        "final_outcome": history.get("outcome"),
        "evidence_ids": sorted(
            {
                str(evidence_id)
                for conflict in conflicts
                if isinstance(conflict, dict)
                for evidence_id in conflict.get("evidence_ids", [])
            }
        ),
    }


def _reason_counts(excluded: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count every exclusion reason while keeping unique candidate count separate."""
    counts: Counter[str] = Counter()
    for row in excluded:
        for reason in row.get("exclusion_reasons", []):
            counts[str(reason["reason"])] += 1
    return dict(sorted(counts.items()))


def _refresh_context_audit_hash(record: dict[str, Any]) -> None:
    """Bind a context-audit record to all fields except its digest field."""
    record.pop("context_audit_record_sha256", None)
    record["context_audit_record_sha256"] = value_sha256(record)


def _build_report(
    summary: dict[str, Any], boundary: dict[str, Any], source_inventory: dict[str, Any]
) -> str:
    """Render a concise no-cost audit report from deterministic aggregate facts."""
    counts = summary["candidate_counts"]
    exclusions = summary["exclusion_counts_by_reason"]
    lines = [
        "# Fresh reply-hybrid no-cost audit report",
        "",
        "## Result",
        "",
        (
            f"The immutable later-period reconstruction found {counts['logged_post_cutoff_targets']} "
            f"logged targets, {counts['valid_external_candidates']} valid external candidates before "
            f"contamination/context exclusions, and {counts['eligible_candidates']} clean eligible "
            "candidates. No final paid sample was selected."
        ),
        "",
        f"Development cutoff: `{boundary['development_cutoff']}`.",
        "",
        "Admission rule: `candidate_timestamp > development_cutoff`.",
        "",
        "## Exclusions",
        "",
    ]
    if exclusions:
        lines.extend(f"- `{reason}`: {count}" for reason, count in exclusions.items())
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Context clearance",
            "",
            f"- Automatic clearance: {summary['automatic_context_clearance_count']}",
            f"- Manual review pending: {summary['manual_context_review_count']}",
            f"- Unrecoverable context: {summary['unrecoverable_context_count']}",
            "",
            "Manual decisions remain blank. Context-dependent candidates were recovered where "
            "possible and queued; they were not automatically rejected.",
            "",
            "## Source limitations",
            "",
            (
                "The operator reports a 30-day rolling snapshot window. This audit pins the exact "
                "snapshots observed at build time and does not assume older snapshots remain present."
            ),
            "",
            (
                "ZFS creation times come from numeric `zfs get` properties. Snapshot-name time "
                "inference during British Summer Time was explicitly rejected."
            ),
            "",
            f"First fresh source snapshot: `{source_inventory['first_snapshot_containing_fresh']}`.",
            "",
            f"Latest complete snapshot: `{source_inventory['latest_complete_snapshot']}`.",
            "",
            "## Historical score discrepancy",
            "",
            (
                "Both retained blind keys verify, but all retained human-score and reviewer-note "
                "fields are blank. No old score, winner, or quality label was inferred."
            ),
            "",
            "## No-cost controls",
            "",
            "- Model calls: 0",
            "- Provider HTTP requests: 0",
            "- Posting actions: 0",
            "- Production state changes: 0",
            "- Mutable live project included: false",
            "",
            "The tool has no response-generation, paid-execution, provider, search, or posting mode.",
            "",
        ]
    )
    return "\n".join(lines)


def build_audit(
    args: argparse.Namespace,
    command_line: str,
    canonical_rebuild_command: str | None = None,
) -> Path:
    """Build the complete deterministic real-data audit under a new private directory."""
    observed_start_utc = utc_text(datetime.now(timezone.utc))
    if not provider_credentials_are_absent():
        raise AuditError("relevant provider credential variables must be absent")
    created_at = utc_text(parse_utc(args.created_at))
    frozen_current_date = created_at[:10]
    repository = Path(args.repository).resolve(strict=True)
    snapshot_root = Path(args.snapshot_root).resolve(strict=True)
    project_relative = Path(args.project_relative_path)
    if project_relative.is_absolute() or ".." in project_relative.parts:
        raise AuditError("snapshot project-relative path is unsafe")
    immutable_research_root = ensure_safe_source_path(
        Path(args.immutable_research_root), snapshot_root
    )
    history_corpus = ensure_safe_source_path(
        Path(args.fresh_history), Path(args.research_root)
    )
    requested_output = Path(args.output)
    research_root = Path(args.research_root).resolve(strict=True)
    requested_parent = requested_output.parent.resolve(strict=True)
    if requested_parent != research_root and research_root not in requested_parent.parents:
        raise AuditError(f"output must be below {research_root}")
    resolved_output_candidate = requested_parent / requested_output.name
    if (
        resolved_output_candidate == repository
        or repository in resolved_output_candidate.parents
        or resolved_output_candidate in repository.parents
    ):
        raise AuditError("real-data audit output must remain outside the Git worktree")
    if len(args.snapshot) < 2 or len(args.snapshot) != len(set(args.snapshot)):
        raise AuditError("two or more unique explicit snapshots are required")

    profile_verification = verify_profile_identities(repository)
    historical_tool_verification = verify_historical_tool_identities(repository)
    registry, boundary, development_runtime = build_development_registry(
        immutable_research_root
    )
    fresh_history_by_target = _history_by_target(history_corpus)
    reply_candidate_by_post_id: dict[str, str] = {}
    for row in fresh_history_by_target.values():
        if not row.get("reply_post_id") or not row.get("candidate_id"):
            continue
        reply_post_id = str(row["reply_post_id"])
        candidate_id = str(row["candidate_id"])
        existing = reply_candidate_by_post_id.get(reply_post_id)
        if existing is not None and existing != candidate_id:
            raise AuditError(
                f"reply post identity maps to multiple history candidates: {reply_post_id}"
            )
        reply_candidate_by_post_id[reply_post_id] = candidate_id
    reply_candidate_ids_by_text_sha256: dict[str, set[str]] = defaultdict(set)
    for row in fresh_history_by_target.values():
        if row.get("candidate_id") and str(row.get("actual_reply_text") or "").strip():
            reply_candidate_ids_by_text_sha256[
                text_sha256(clean_context_text(str(row["actual_reply_text"])))
            ].add(str(row["candidate_id"]))
    snapshot_projects, snapshots = _snapshot_source_inventory(
        repository,
        snapshot_root,
        args.dataset,
        project_relative,
        args.snapshot,
    )
    snapshot_window_verification = _verify_snapshot_window_completeness(
        args.dataset,
        snapshot_root,
        project_relative,
        snapshots,
        boundary["development_cutoff"],
        created_at,
    )
    output = prepare_output_directory(requested_output, research_root)
    (
        considerations,
        reconstruction_counts,
        log_sources,
        malformed_consideration_exclusions,
    ) = reconstruct_considerations(snapshot_projects, boundary["development_cutoff"])
    (
        cache,
        provenance,
        own_auto_reply_ids,
        account_author_id,
        state_receipts,
        cache_issues,
    ) = (
        load_snapshot_cache(snapshot_projects)
    )
    states = [
        json.loads((project / "bot_state.json").read_text(encoding="utf-8"))
        for _snapshot, project in snapshot_projects
    ]
    quote_resolver = _QuoteResolver(
        repository / "semantic_alignment_research" / "quote_research_full_001"
    )

    development_cases = list(development_runtime.values())
    development_threads = {
        str(row["thread_id"])
        for row in development_cases
        if row.get("thread_id")
    }
    development_contexts = [
        {"candidate_id": row["candidate_id"], "context": row["context"]}
        for row in development_cases
    ]

    excluded: list[dict[str, Any]] = list(malformed_consideration_exclusions)
    eligible: list[dict[str, Any]] = []
    context_audits: list[dict[str, Any]] = []
    valid_external_count = 0
    automatic_count = 0
    manual_rows: list[dict[str, Any]] = []
    historical_counts: Counter[str] = Counter()
    lane_counts: Counter[str] = Counter()
    semantic_tag_counts: Counter[str] = Counter()
    stratum_counts: Counter[str] = Counter()

    for raw_candidate in sorted(
        considerations,
        key=lambda row: (
            parse_utc(row["first_consideration_timestamp"]), row["target_id"]
        ),
    ):
        target_id = str(raw_candidate["target_id"])
        history = fresh_history_by_target.get(target_id, {})
        candidate_id = str(history.get("candidate_id") or "candidate-" + value_sha256(
            {"lane": raw_candidate["lane"], "target_id": target_id}
        ))
        raw_candidate["candidate_id"] = candidate_id
        preliminary: list[dict[str, Any]] = []
        target = cache.get(target_id)
        preliminary.extend(cache_issues.get(target_id, []))
        if raw_candidate.get("source_conflict"):
            preliminary.append({"reason": "conflicting_candidate_source_records"})
        if target is None:
            if target_id not in cache_issues:
                if raw_candidate["lane"] == "quote_tweet" and re.match(
                    r"^RT\s+@\w+:", clean_context_text(raw_candidate["incoming_text"])
                ):
                    preliminary.append({"reason": "retweet_not_direct_quote_tweet"})
                else:
                    preliminary.append({"reason": "missing_target_source_record"})
        elif str(target.get("author_id") or "") == account_author_id:
            preliminary.append({"reason": "own_account_not_external_candidate"})
        elif str(target.get("author_id") or "") != str(raw_candidate["author_id"]):
            preliminary.append({"reason": "candidate_author_source_mismatch"})
        if preliminary:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": raw_candidate["lane"],
                    "first_consideration_timestamp": raw_candidate[
                        "first_consideration_timestamp"
                    ],
                    "source_record_ids": [
                        item["record_id"] for item in raw_candidate["consideration_records"]
                    ],
                    "exclusion_reasons": preliminary,
                }
            )
            continue
        assert target is not None
        valid_external_count += 1
        history_identity_issues, history_identity_receipt = _history_identity_issues(
            history, raw_candidate, target
        )
        if history_identity_issues:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": raw_candidate["lane"],
                    "first_consideration_timestamp": raw_candidate[
                        "first_consideration_timestamp"
                    ],
                    "fresh_history_identity_receipt": history_identity_receipt,
                    "exclusion_reasons": history_identity_issues,
                }
            )
            continue
        candidate_timestamp_raw = str(target.get("created_at") or "")
        try:
            candidate_timestamp = (
                utc_text(_parse_source_timestamp(candidate_timestamp_raw))
                if candidate_timestamp_raw
                else ""
            )
        except (AuditError, TypeError, ValueError, OverflowError):
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": raw_candidate["lane"],
                    "candidate_timestamp": None,
                    "source_record_fingerprint": value_sha256(target),
                    "exclusion_reasons": [
                        {
                            "reason": "invalid_candidate_timestamp",
                            "candidate_timestamp_sha256": text_sha256(
                                candidate_timestamp_raw
                            ),
                        }
                    ],
                }
            )
            continue
        if not candidate_timestamp or not strictly_after(
            candidate_timestamp, boundary["development_cutoff"]
        ):
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": raw_candidate["lane"],
                    "candidate_timestamp": candidate_timestamp or None,
                    "exclusion_reasons": [
                        {
                            "reason": "timestamp_at_or_before_development_cutoff",
                            "development_cutoff": boundary["development_cutoff"],
                        }
                    ],
                }
            )
            continue
        try:
            conflict_resolution = (
                _ordered_conflict_resolution(history) if history else None
            )
            context_audit, context_problems = build_replay_context_audit(
                raw_candidate,
                target,
                cache,
                provenance,
                own_auto_reply_ids,
                account_author_id,
                states,
                frozen_current_date,
                quote_resolver,
                reply_candidate_by_post_id,
                reply_candidate_ids_by_text_sha256,
            )
        except (AuditError, TypeError, ValueError, OverflowError) as exc:
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": raw_candidate["lane"],
                    "candidate_timestamp": candidate_timestamp,
                    "source_record_fingerprint": value_sha256(target),
                    "exclusion_reasons": [
                        {
                            "reason": "malformed_or_ambiguous_context_source",
                            "error_type": type(exc).__name__,
                            "error_message_sha256": text_sha256(str(exc)),
                        }
                    ],
                }
            )
            continue
        context_audit["candidate_id"] = candidate_id
        context_audit["historical_outcome"] = history.get("outcome")
        context_audit["historical_public_reply"] = history.get("actual_reply_text")
        context_audit["source_conflict_resolution"] = conflict_resolution
        context_audit["fresh_history_identity_receipt"] = history_identity_receipt
        _refresh_context_audit_hash(context_audit)
        context = context_audit["replay_context"]
        leakage_context = {
            **context,
            "recent_account_replies": context_audit["recent_account_replies"],
            "resolved_quotation": context_audit["resolved_quotation"],
        }
        semantic = semantic_inventory(
            context["incoming_contribution"], context
        )
        contamination_candidate = {
            "candidate_id": candidate_id,
            "target_id": target_id,
            "thread_id": context["thread_id"],
            "incoming_contribution": context["incoming_contribution"],
            "context": leakage_context,
            "context_dependency_flags": context_audit["context_dependency_flags"],
        }
        contamination = candidate_contamination(
            contamination_candidate,
            development_cases,
            development_threads,
            development_contexts,
        )
        exclusion_reasons = [
            {"reason": str(item.get("reason") or "context_audit_failure"), **{
                key: value for key, value in item.items() if key != "reason"
            }}
            for item in context_problems
        ] + contamination
        if exclusion_reasons:
            context_audits.append(context_audit)
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "target_id": target_id,
                    "lane": context["lane"],
                    "candidate_timestamp": candidate_timestamp,
                    "historical_outcome": history.get("outcome"),
                    "source_record_fingerprint": value_sha256(target),
                    "context_audit_record_sha256": context_audit[
                        "context_audit_record_sha256"
                    ],
                    "exclusion_reasons": exclusion_reasons,
                }
            )
            continue

        manual_required = bool(context_audit["context_dependency_flags"]) or (
            "direct_factual_or_historical_question" in semantic["semantic_tags"]
        )
        context_audit["manual_context_review_required"] = manual_required
        context_audit["automatic_context_clearance"] = not manual_required
        _refresh_context_audit_hash(context_audit)
        context_audits.append(context_audit)
        source_record_fingerprint = value_sha256(target)
        prospective_identity = value_sha256(
            {
                "target_id": target_id,
                "lane": context["lane"],
                "incoming_contribution": context["incoming_contribution"],
                "candidate_timestamp": candidate_timestamp,
                "thread_id": context["thread_id"],
                "quoted_post_id": (
                    context["quoted_post"].get("post_id")
                    if isinstance(context.get("quoted_post"), dict)
                    else None
                ),
                "source_record_fingerprint": source_record_fingerprint,
            }
        )
        item = {
            "candidate_id": candidate_id,
            "prospective_candidate_identity_sha256": prospective_identity,
            "target_id": target_id,
            "lane": context["lane"],
            "incoming_contribution": context["incoming_contribution"],
            "candidate_timestamp": candidate_timestamp,
            "first_consideration_timestamp": raw_candidate[
                "first_consideration_timestamp"
            ],
            "final_consideration_timestamp": raw_candidate[
                "final_consideration_timestamp"
            ],
            "thread_id": context["thread_id"],
            "quoted_post_id": (
                context["quoted_post"].get("post_id")
                if isinstance(context.get("quoted_post"), dict)
                else None
            ),
            "source_record_fingerprint": source_record_fingerprint,
            "target_source_provenance": provenance.get(target_id, []),
            "consideration_source_records": [
                {
                    key: record[key]
                    for key in (
                        "record_id",
                        "record_fingerprint",
                        "timestamp",
                        "original_timestamp_text",
                        "timezone_interpretation",
                        "raw_record_sha256",
                        "source_locations",
                    )
                }
                for record in raw_candidate["consideration_records"]
            ],
            "replay_context": context,
            "component_sha256": context_audit["component_sha256"],
            "context_dependency_flags": context_audit["context_dependency_flags"],
            "context_clearance_status": (
                "pending_manual_review" if manual_required else "automatic_clearance"
            ),
            "semantic_inventory": semantic,
            "historical_outcome": history.get("outcome"),
            "historical_public_reply": history.get("actual_reply_text"),
            "historical_evidence_only_not_quality_label": True,
            "source_conflict_resolution": conflict_resolution,
            "fresh_history_identity_receipt": history_identity_receipt,
            "context_audit_record_sha256": context_audit[
                "context_audit_record_sha256"
            ],
        }
        eligible.append(item)
        lane_counts[context["lane"]] += 1
        historical_counts[str(history.get("outcome") or "unknown")] += 1
        stratum_counts[semantic["proposed_primary_stratum"]] += 1
        semantic_tag_counts.update(semantic["semantic_tags"])
        if manual_required:
            manual_rows.append(
                {
                    "candidate_id": candidate_id,
                    "proposed_primary_stratum": semantic[
                        "proposed_primary_stratum"
                    ],
                    "replay_context": context,
                    "recent_account_replies": context_audit[
                        "recent_account_replies"
                    ],
                    "resolved_quotation": context_audit["resolved_quotation"],
                    "context_dependency_flags": context_audit[
                        "context_dependency_flags"
                    ],
                    "context_audit_record_sha256": context_audit[
                        "context_audit_record_sha256"
                    ],
                }
            )
        else:
            automatic_count += 1

    eligible, fresh_duplicate_exclusions, fresh_deduplication = (
        deduplicate_fresh_candidates(eligible)
    )
    excluded.extend(fresh_duplicate_exclusions)
    eligible_ids = {str(row["candidate_id"]) for row in eligible}
    manual_rows = [
        row for row in manual_rows if str(row["candidate_id"]) in eligible_ids
    ]
    automatic_count = sum(
        row["context_clearance_status"] == "automatic_clearance" for row in eligible
    )
    lane_counts = Counter(str(row["lane"]) for row in eligible)
    historical_counts = Counter(
        str(row.get("historical_outcome") or "unknown") for row in eligible
    )
    stratum_counts = Counter(
        str(row["semantic_inventory"]["proposed_primary_stratum"])
        for row in eligible
    )
    semantic_tag_counts = Counter(
        tag
        for row in eligible
        for tag in row["semantic_inventory"]["semantic_tags"]
    )

    considered_target_ids = {str(row["target_id"]) for row in considerations}
    for target_id, issues in cache_issues.items():
        if target_id in considered_target_ids:
            continue
        excluded.append(
            {
                "candidate_id": None,
                "target_id": target_id,
                "lane": None,
                "source_record_only": True,
                "exclusion_reasons": issues,
            }
        )

    eligible.sort(key=lambda row: (row["candidate_timestamp"], row["target_id"]))
    excluded.sort(
        key=lambda row: (
            str(row.get("candidate_timestamp") or row.get("first_consideration_timestamp") or ""),
            str(row.get("target_id") or ""),
        )
    )
    context_audits.sort(
        key=lambda row: (row["candidate_timestamp"], row["target_id"])
    )
    valid_context_audits = [row for row in context_audits if not row["context_unrecoverable"]]
    unrecoverable_count = sum(row["context_unrecoverable"] for row in context_audits)
    reason_counts = _reason_counts(excluded)
    exact_duplicate_count = sum(
        reason_counts.get(reason, 0)
        for reason in (
            "exact_normalised_incoming_duplicate",
            "exact_context_payload_duplicate",
            "fresh_exact_normalised_incoming_duplicate",
        )
    )
    near_duplicate_count = sum(
        reason_counts.get(reason, 0)
        for reason in ("near_duplicate_incoming", "near_duplicate_context_payload")
    )
    source_snapshots_with_fresh = {
        location["snapshot_name"]
        for candidate in considerations
        for record in candidate["consideration_records"]
        for location in record["source_locations"]
    }
    first_fresh_snapshot = next(
        item["snapshot_name"]
        for item in snapshots
        if item["snapshot_name"] in source_snapshots_with_fresh
    )

    fresh_history_manifest = json.loads(
        (history_corpus / "run_manifest.json").read_text(encoding="utf-8")
    )
    expected_history_arguments = {
        "snapshot": list(args.snapshot),
        "snapshot_root": str(snapshot_root),
        "project_relative_path": str(project_relative),
    }
    history_arguments = fresh_history_manifest.get("arguments")
    if not isinstance(history_arguments, dict) or any(
        history_arguments.get(key) != value
        for key, value in expected_history_arguments.items()
    ):
        raise AuditError(
            "fresh-history reconstruction arguments do not match the selected source window"
        )
    if fresh_history_manifest.get("selected_snapshots") != list(args.snapshot):
        raise AuditError("fresh-history selected snapshot receipt differs")
    source_inventory = {
        "schema_version": SCHEMA_VERSION,
        "inventory_version": "reply-hybrid-source-inventory-v1",
        "pool_and_device": _pool_source_receipt(args.dataset),
        "snapshot_root": str(snapshot_root),
        "snapshot_retention_note": (
            "operator reports a rolling 30-day window; availability is time-bound and "
            "this audit assumes no unlisted older snapshot remains available"
        ),
        "snapshot_creation_attempted": False,
        "snapshot_mutation_attempted": False,
        "selected_snapshots": snapshots,
        "snapshot_window_completeness": snapshot_window_verification,
        "reconstruction_boundary_snapshot": snapshots[0]["snapshot_name"],
        "first_snapshot_containing_fresh": first_fresh_snapshot,
        "latest_complete_snapshot": snapshots[-1]["snapshot_name"],
        "snapshot_name_timestamp_discrepancy": (
            "snapshot names were not parsed for UTC time; authoritative numeric ZFS creation "
            "properties avoid the old Europe/London BST one-hour inference error"
        ),
        "snapshot_state_files": state_receipts,
        "snapshot_log_files": log_sources,
        "cache_union": {
            "record_count": len(cache),
            "conflicting_shared_record_count": 0,
            "account_author_id_sha256": text_sha256(account_author_id),
        },
        "fresh_candidate_deduplication": fresh_deduplication,
        "fresh_history_reconstruction": {
            "path": str(history_corpus),
            "sha256sums_sha256": file_sha256(history_corpus / "SHA256SUMS"),
            "manifest_sha256": file_sha256(history_corpus / "run_manifest.json"),
            "extractor_commit": fresh_history_manifest["extractor_git_commit"],
            "extractor_source_sha256": fresh_history_manifest[
                "extractor_source_sha256"
            ],
            "exact_reconstruction_arguments": fresh_history_manifest["arguments"],
            "live_project_included": fresh_history_manifest["live_project_included"],
        },
        "historical_artifacts": registry["artifact_receipts"],
        "quotation_corpus": {
            "path": str(quote_resolver.research_dir),
            "source_file_sha256": quote_resolver.source_hashes,
            "provider_or_network_lookup_used": False,
        },
        "raw_reconstruction_counts": reconstruction_counts,
        "live_mutable_data_excluded": True,
    }
    candidate_counts = {
        "logged_post_cutoff_targets": reconstruction_counts[
            "unique_post_cutoff_targets"
        ],
        "valid_external_candidates": valid_external_count,
        "eligible_candidates": len(eligible),
        "excluded_unique_candidates": len(excluded),
        "context_audited_external_candidates": len(context_audits),
    }
    context_summary = {
        "schema_version": SCHEMA_VERSION,
        "context_audit_rule_version": CONTEXT_AUDIT_RULE_VERSION,
        "candidate_counts": candidate_counts,
        "automatic_context_clearance_count": automatic_count,
        "manual_context_review_count": len(manual_rows),
        "unrecoverable_context_count": unrecoverable_count,
        "dependency_flag_counts": dict(
            sorted(
                Counter(
                    flag
                    for row in valid_context_audits
                    for flag in row["context_dependency_flags"]
                ).items()
            )
        ),
        "future_parent_or_quote_violation_count": sum(
            len(row["temporal_violations"]) for row in context_audits
        ),
        "historical_reply_self_leak_count": sum(
            len(row["historical_reply_self_leak_post_ids"]) for row in context_audits
        ),
        "persisted_production_context_hash_checks": sum(
            bool(row["persisted_context_verification"]["production_context_hash_checked"])
            for row in context_audits
        ),
        "persisted_production_context_hash_matches": sum(
            bool(row["persisted_context_verification"].get("production_context_hash_match"))
            for row in context_audits
        ),
        "persisted_resolved_quote_context_hash_checks": sum(
            "resolved_quote_context_hash_match" in row["persisted_context_verification"]
            for row in context_audits
        ),
        "persisted_resolved_quote_context_hash_matches": sum(
            bool(
                row["persisted_context_verification"].get(
                    "resolved_quote_context_hash_match"
                )
            )
            for row in context_audits
        ),
        "exclusion_counts_by_reason": reason_counts,
        "exact_duplicate_exclusion_count": exact_duplicate_count,
        "near_duplicate_exclusion_count": near_duplicate_count,
        "fresh_candidate_deduplication": fresh_deduplication,
    }
    strata_inventory = {
        "schema_version": SCHEMA_VERSION,
        "classification_version": SEMANTIC_INVENTORY_VERSION,
        "normalisation_version": NORMALISATION_VERSION,
        "near_duplicate_algorithm": NEAR_DUPLICATE_VERSION,
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        "threshold_frozen_before_inventory": True,
        "historical_outcome_used_for_classification": False,
        "profile_outputs_generated_or_inspected": False,
        "eligible_count": len(eligible),
        "counts_by_lane": dict(sorted(lane_counts.items())),
        "counts_by_historical_status": dict(sorted(historical_counts.items())),
        "counts_by_semantic_tag": dict(sorted(semantic_tag_counts.items())),
        "counts_by_proposed_primary_stratum": dict(sorted(stratum_counts.items())),
        "fresh_candidate_deduplication": fresh_deduplication,
        "continuity_strata": [
            "formulaic_substantive_posted",
            "civil_challenge_or_disagreement",
            "genuine_social_courtesy",
            "factual_or_historical_question",
            "safe_wit_opportunity",
            "justified_safety_no_reply",
        ],
    }
    thin_strata = [
        stratum
        for stratum in strata_inventory["continuity_strata"]
        if stratum_counts.get(stratum, 0) == 0
    ]
    sampling_readiness = {
        "schema_version": SCHEMA_VERSION,
        "sampling_readiness_version": "fresh-reply-sampling-readiness-v1",
        "final_paid_sample_selected": False,
        "final_replay_pack_built": False,
        "eligible_candidate_count": len(eligible),
        "manual_context_decisions_pending": len(manual_rows),
        "unrecoverable_context_candidates": unrecoverable_count,
        "thin_or_empty_important_strata": thin_strata,
        "ready_to_freeze_sample": not manual_rows and not thin_strata,
        "blockers": [
            *(
                [f"{len(manual_rows)} manual context-clearance decisions remain blank"]
                if manual_rows
                else []
            ),
            *(
                ["one or more continuity strata contain no clean fresh candidate: " + ", ".join(thin_strata)]
                if thin_strata
                else []
            ),
        ],
        "instruction": (
            "counts and blockers only; a later reviewed phase must freeze any sample"
        ),
    }

    _write_json(output / "source_inventory.json", source_inventory)
    _write_json(output / "profile_identity_verification.json", profile_verification)
    _write_json(output / "development_exclusion_registry.json", registry)
    _write_json(output / "freshness_boundary.json", boundary)
    _write_jsonl(output / "fresh_candidate_inventory.jsonl", eligible)
    _write_jsonl(output / "excluded_candidates.jsonl", excluded)
    _write_jsonl(output / "context_audit.jsonl", context_audits)
    context_audit_sha256 = file_sha256(output / "context_audit.jsonl")
    _write_json(output / "context_audit_summary.json", context_summary)
    _write_text(
        output / "manual_context_review.md", render_manual_context_review(manual_rows)
    )
    _write_text(
        output / "manual_context_clearance.csv",
        render_manual_clearance_csv(manual_rows, context_audit_sha256),
    )
    _write_json(output / "strata_inventory.json", strata_inventory)
    _write_json(output / "sampling_readiness.json", sampling_readiness)
    _write_text(
        output / "no_cost_audit_report.md",
        _build_report(context_summary, boundary, source_inventory),
    )

    git_status = str(_git(repository, "status", "--short"))
    branch = str(_git(repository, "branch", "--show-current")).strip()
    commit = str(_git(repository, "rev-parse", "HEAD")).strip()
    source_hashes = {
        "audit_tool": file_sha256(Path(__file__).resolve()),
        "audit_tests": file_sha256(
            repository / "tests" / "test_reply_hybrid_evaluation_audit.py"
        ),
        "preregistration": file_sha256(
            repository / "reply_hybrid_evaluation_preregistration.md"
        ),
        "fresh_history_manifest": file_sha256(history_corpus / "run_manifest.json"),
        "fresh_history_sha256sums": file_sha256(history_corpus / "SHA256SUMS"),
        "profile_reply_strategy_sources": {
            name: profile["reply_strategy_source_sha256"]
            for name, profile in profile_verification["profiles"].items()
        },
        "historical_artifact_checksum_manifests": {
            name: receipt["sha256sums_sha256"]
            for name, receipt in registry["artifact_receipts"].items()
            if "sha256sums_sha256" in receipt
        },
        "snapshot_bot_state_files": {
            receipt["snapshot_name"]: receipt["sha256"]
            for receipt in state_receipts
        },
        "quotation_corpus_files": quote_resolver.source_hashes,
    }
    output_hashes = {
        path.name: file_sha256(path)
        for path in sorted(output.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name not in {"run_manifest.json", "SHA256SUMS"}
    }
    observed_finish_utc = utc_text(datetime.now(timezone.utc))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "branch": branch,
        "commit": commit,
        "base_hybrid_commit": HYBRID_PROFILE_COMMIT,
        "working_tree_clean": not bool(git_status),
        "working_tree_status_short": git_status.splitlines(),
        "exact_source_commits": {
            "profiles": {
                "hardened_current": CURRENT_PROFILE_COMMIT,
                "conservative_hybrid": HYBRID_PROFILE_COMMIT,
            },
            "historical_tools": HISTORICAL_SOURCE_COMMITS,
        },
        "historical_tool_identity_verification": historical_tool_verification,
        "exact_command_line": command_line,
        "canonical_rebuild_command_line": canonical_rebuild_command,
        "canonical_rebuild_command_line_note": (
            "this secondary command normalizes only --output to <OUTPUT_DIRECTORY>; the "
            "exact runtime invocation remains recorded separately"
        ),
        "start_utc": observed_start_utc,
        "finish_utc": observed_finish_utc,
        "audit_created_at": created_at,
        "timestamp_note": (
            "start_utc and finish_utc are observed runtime timestamps; audit_created_at is the "
            "explicit deterministic freeze timestamp used for replay current_date and payloads"
        ),
        "source_hashes": source_hashes,
        "output_hashes_before_manifest_and_sha256sums": output_hashes,
        "provider_credential_variables_absent": True,
        **audit_manifest_control_fields(),
        "candidate_counts": candidate_counts,
        "automatic_context_clearance_count": automatic_count,
        "manual_context_review_count": len(manual_rows),
        "unrecoverable_context_count": unrecoverable_count,
        "exclusion_counts_by_reason": reason_counts,
        "exact_duplicate_exclusion_count": exact_duplicate_count,
        "near_duplicate_exclusion_count": near_duplicate_count,
        "output_directory": str(output),
        "runtime_output_directory_recorded_in_content": True,
        "byte_identical_rebuild_scope": (
            "all deterministic audit payload files; run_manifest.json and SHA256SUMS are "
            "runtime receipts and therefore intentionally differ across distinct output paths"
        ),
        "output_directory_preexisting_nonempty": False,
        "final_sample_selected": False,
        "response_generation_authorised": False,
        "execution_seed_created": False,
        "blind_key_created": False,
    }
    _write_json(output / "run_manifest.json", manifest)
    sums = write_sha256sums(output)
    if set(sums) != set(OUTPUT_FILENAMES):
        raise AuditError(
            f"output inventory mismatch: expected {OUTPUT_FILENAMES!r}, observed {sorted(sums)!r}"
        )
    return output


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the validate-only command-line parser for this audit."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a no-cost fresh candidate and replay-context audit from explicit "
            "immutable snapshots. This tool cannot generate or post replies."
        )
    )
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--immutable-research-root", type=Path, required=True)
    parser.add_argument("--fresh-history", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, default=Path("/disks/disk1/research"))
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--dataset", default="disks/disk1")
    parser.add_argument("--project-relative-path", default="etc/mrsMThatcher")
    parser.add_argument("--snapshot", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--created-at", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fail-closed audit and return a conventional process status."""
    parser = build_argument_parser()
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(parsed_argv)
    try:
        process_argv = (
            list(sys.orig_argv)
            if argv is None and getattr(sys, "orig_argv", None)
            else [sys.executable, str(Path(__file__)), *parsed_argv]
        )
        command_line = shlex.join(process_argv)
        canonical_rebuild_command = canonical_rebuild_command_line(parsed_argv)
        output = build_audit(args, command_line, canonical_rebuild_command)
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"audit refused: {exc}\n")
    print(f"No-cost audit complete: {output}")
    print("model_calls=0 provider_http_requests=0 posting_actions=0 production_state_changes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
