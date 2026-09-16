from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mrs_log_digest as digest
import mrs_log_digest_markdown as markdown
from mrs_log_digest_transactions import summarise_reply_receipt_lifecycle
from mrs_log_digest_consistency_events import prepare_context_transaction_outcomes


FIXTURES = Path(__file__).parent / "fixtures" / "digest_markdown"


@pytest.mark.parametrize("name", ["populated", "missing", "optional"])
def test_markdown_matches_baseline_bytes_without_mutating_report(name):
    report = json.loads((FIXTURES / f"{name}.json").read_text())
    original = copy.deepcopy(report)
    expected = (FIXTURES / f"{name}.md").read_bytes()
    receipt_events = (report.get("main_post_recovery") or {}).get("receipt_events") or []
    lifecycle = digest.summarise_main_post_receipt_lifecycle(receipt_events)
    original_lifecycle = copy.deepcopy(lifecycle)
    reply_recovery = report.get("confirmed_reply_recovery") or {}
    reply_lifecycle = summarise_reply_receipt_lifecycle(
        reply_recovery.get("receipt_events") or [],
        reconciled_ambiguity_receipts=reply_recovery.get("durably_reconciled_ambiguity_receipts") or [],
        unavailable_receipts=reply_recovery.get("status_unavailable_receipts") or [],
        normalise_lane=digest._normalise_lane,
    )
    original_reply_lifecycle = copy.deepcopy(reply_lifecycle)

    assert digest.render_markdown(report).encode("utf-8") == expected
    assert markdown.render_markdown(
        report, main_post_receipt_lifecycle=lifecycle,
        reply_receipt_lifecycle=reply_lifecycle,
    ).encode("utf-8") == expected
    assert report == original
    assert lifecycle == original_lifecycle
    assert reply_lifecycle == original_reply_lifecycle


def test_renderer_import_and_render_do_not_access_runtime_or_initialise_services(tmp_path):
    script = """
import logging
import os
import sys

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        # Interpreter imports may read code; report/state/config files may not be read.
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system"}, event

sys.addaudithook(audit)
import mrs_log_digest_markdown as markdown

text = markdown.render_markdown(
    {"summary": {}}, main_post_receipt_lifecycle={}, reply_receipt_lifecycle={},
)
assert text.startswith("# MrsMThatcher log digest\\n")
assert "mrs_log_digest" not in sys.modules
assert "mrsMThatcher2" not in sys.modules
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(digest.__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_reply_recovery_formats_prepared_lifecycle_without_scanning_receipt_rows():
    class UnreadableReceipt(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("presentation must not inspect receipt lifecycle events")

    report = {"summary": {}, "confirmed_reply_recovery": {"receipt_events": [UnreadableReceipt()]}}
    lifecycle = {
        "normal_reply_pairs": 7,
        "terminal_reply_removals_outside_window": 2,
        "definite_non_success_clears": 3,
        "confirmed_state_fallback_clears": 4,
        "reconciled_ambiguity_sending_receipts": 5,
        "unresolved_reply_receipts": [{"kind": "prepared-unresolved", "message": "Prepared pending receipt"}],
        "unavailable_reply_receipt_rows": [{"kind": "prepared-unavailable", "message": "Prepared unavailable receipt"}],
    }
    before = copy.deepcopy(lifecycle)
    rendered = markdown.render_markdown(
        report, main_post_receipt_lifecycle={}, reply_receipt_lifecycle=lifecycle,
    )
    assert "Routine confirmed-reply receipt write/remove pairs completed: **7**." in rendered
    assert "Prepared pending receipt" in rendered
    assert "Prepared unavailable receipt" in rendered
    assert lifecycle == before


def test_context_transactions_render_prepared_outcomes_without_reclassifying(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("prepared context outcomes must not be recomputed")

    monkeypatch.setattr(markdown, "prepare_context_transaction_outcomes", forbidden)
    report = {"summary": {}, "events": [], "production_consistency": {
        "context_transaction_outcomes": {
            "resolved_pending_count": 3,
            "outstanding_count": 1,
            "outstanding_transactions": [{"parent_post_id": "202", "reason": "Prepared outstanding row"}],
        },
    }}
    before = copy.deepcopy(report)

    rendered = digest.render_markdown(report)

    assert "**3** intermediate `context_reply_pending` states subsequently reached" in rendered
    assert "Prepared outstanding row" in rendered
    assert report == before


@pytest.mark.parametrize("consistency", [None, {}, {"context_transaction_state_counts": {}}])
def test_context_transactions_fallback_for_older_reports_matches_prepared_output(consistency):
    events = [
        {"kind": "posting_transaction_state", "parent_post_id": "101",
         "context_reply_state": "context_reply_pending"},
        {"kind": "historical_context_obligation", "parent_post_id": "101",
         "context_reply_state": "context_reply_not_required"},
        {"kind": "posting_transaction_state", "parent_post_id": "202",
         "context_reply_state": "context_reply_failed_retryable"},
    ]
    report = {"summary": {}, "events": events}
    if consistency is not None:
        report["production_consistency"] = consistency
    before = copy.deepcopy(report)
    prepared = copy.deepcopy(report)
    prepared.setdefault("production_consistency", {})["context_transaction_outcomes"] = (
        prepare_context_transaction_outcomes(prepared["events"])
    )

    assert digest.render_markdown(report) == digest.render_markdown(prepared)
    assert report == before
