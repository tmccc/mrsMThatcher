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


FIXTURES = Path(__file__).parent / "fixtures" / "digest_markdown"


@pytest.mark.parametrize("name", ["populated", "missing", "optional"])
def test_markdown_matches_baseline_bytes_without_mutating_report(name):
    report = json.loads((FIXTURES / f"{name}.json").read_text())
    original = copy.deepcopy(report)
    expected = (FIXTURES / f"{name}.md").read_bytes()
    receipt_events = (report.get("main_post_recovery") or {}).get("receipt_events") or []
    lifecycle = digest.summarise_main_post_receipt_lifecycle(receipt_events)
    original_lifecycle = copy.deepcopy(lifecycle)

    assert digest.render_markdown(report).encode("utf-8") == expected
    assert markdown.render_markdown(
        report, main_post_receipt_lifecycle=lifecycle
    ).encode("utf-8") == expected
    assert report == original
    assert lifecycle == original_lifecycle


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

text = markdown.render_markdown({"summary": {}}, main_post_receipt_lifecycle={})
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
