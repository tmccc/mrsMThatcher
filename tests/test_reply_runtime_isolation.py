"""Regression coverage for the production/research dependency boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
import reply_evidence
from historical_context_formatter import packet_is_attributed_to_margaret_thatcher


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"


def blocked_google_path(tmp_path: Path) -> Path:
    """Create a package that proves any attempt to import Google fails."""
    package = tmp_path / "blocked" / "google"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        'raise ImportError("google SDK deliberately unavailable")\n',
        encoding="utf-8",
    )
    return package.parent


def sdk_free_environment(blocked: Path) -> dict[str, str]:
    """Return a subprocess environment with the blocker before site packages."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(blocked), str(ROOT)))
    return env


def test_saved_corpus_validation_does_not_import_google_sdk(tmp_path: Path) -> None:
    blocked = blocked_google_path(tmp_path)
    code = (
        "import json; from pathlib import Path; "
        "from historical_context_formatter import load_and_validate_corpus; "
        "from reply_evidence import EvidenceRepository; "
        f"p,u=load_and_validate_corpus(Path({str(RESEARCH)!r})); "
        f"r=EvidenceRepository(Path({str(RESEARCH)!r})); "
        "print(json.dumps({'completed': sorted(p), 'unresolved': sorted(u), 'eligible': sorted(r.packets)}))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=sdk_free_environment(blocked),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    packets = json.loads((RESEARCH / "research_packets.json").read_text())["items"]
    unresolved = json.loads(
        (RESEARCH / "final_unresolved/final_research_status.json").read_text()
    )["unresolved_quote_ids"]
    expected_eligible = {
        quote_id for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    assert packets and expected_eligible
    assert json.loads(result.stdout) == {
        "completed": sorted(packets),
        "unresolved": sorted(unresolved),
        "eligible": sorted(expected_eligible),
    }


def test_normal_bootstrap_does_not_require_google_sdk_or_research_tree(
    tmp_path: Path,
) -> None:
    blocked = blocked_google_path(tmp_path)
    base = tmp_path / "normal-bootstrap"
    base.mkdir()
    fake = "http://127.0.0.1:1"
    env = sdk_free_environment(blocked)
    env.update({
        "MRS_TEST_MODE": "1",
        "MRS_BASE_DIR": str(base),
        "MRS_LOG_FILE": str(base / "bootstrap.log"),
        "X_API_BASE_URL": fake,
        "X_UPLOAD_BASE_URL": fake,
        "XAI_API_BASE_URL": f"{fake}/v1",
        "X_CONSUMER_KEY": "dummy",
        "X_CONSUMER_SECRET": "dummy",
        "X_ACCESS_TOKEN": "dummy",
        "X_ACCESS_SECRET": "dummy",
        "X_MY_USER_ID": "1",
        "XAI_API_KEY": "dummy",
        "LOG_LEVEL": "INFO",
    })
    code = (
        "import mrsMThatcher2 as bot; "
        "bot.production_bootstrap(configure_file_logging=False); "
        "print(bot._PRODUCTION_BOOTSTRAPPED, bot._REPLY_EVIDENCE_REPOSITORY)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip().endswith("True None")


def test_self_test_does_not_require_google_sdk_or_research_tree(tmp_path: Path) -> None:
    blocked = blocked_google_path(tmp_path)
    base = tmp_path / "installation"
    (base / "images").mkdir(parents=True)
    (base / "mrsMThatcher.txt").write_text("A test quotation.\n", encoding="utf-8")
    (base / "images" / "t01.jpg").write_bytes(b"image")
    fake = "http://127.0.0.1:1"
    env = sdk_free_environment(blocked)
    env.update({
        "MRS_TEST_MODE": "1",
        "MRS_BASE_DIR": str(base),
        "MRS_LOG_FILE": str(base / "selftest.log"),
        "X_API_BASE_URL": fake,
        "X_UPLOAD_BASE_URL": fake,
        "XAI_API_BASE_URL": f"{fake}/v1",
        "X_CONSUMER_KEY": "dummy",
        "X_CONSUMER_SECRET": "dummy",
        "X_ACCESS_TOKEN": "dummy",
        "X_ACCESS_SECRET": "dummy",
        "X_MY_USER_ID": "1",
        "XAI_API_KEY": "dummy",
        "X_BEARER_TOKEN": "dummy",
        "LOG_LEVEL": "INFO",
    })

    result = subprocess.run(
        [sys.executable, str(ROOT / "mrsMThatcher2.py"), "--self-test"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Deferred optional runtime-resource loading" in result.stdout
    assert "Self-test finished successfully" in result.stdout


def test_reply_evidence_load_failure_is_cached_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    class FailingRepository:
        def __init__(self, path: Path, **_kwargs: object):
            calls.append(path)
            raise OSError("missing corpus")

    monkeypatch.setattr(reply_evidence, "EvidenceRepository", FailingRepository)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "_REPLY_EVIDENCE_REPOSITORY", None)
    monkeypatch.setattr(bot, "_REPLY_EVIDENCE_LOAD_ERROR", None)

    for _attempt in range(2):
        with pytest.raises(bot.ReplyEvidenceUnavailable, match="missing corpus"):
            bot.reply_evidence_repository()

    assert calls == [tmp_path / "semantic_alignment_research/quote_research_full_001"]
