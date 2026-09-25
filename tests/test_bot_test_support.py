"""Check the import and isolation contracts of shared bot test support."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers import bot_fixtures, bot_runtime, reply_fixtures
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


@pytest.mark.parametrize("preload_bot", [False, True])
def test_support_import_preserves_environment_and_one_bot(preload_bot: bool) -> None:
    code = """
import os
from pathlib import Path
import sys

if sys.argv[1] == 'True':
    import mrsMThatcher2 as preloaded_bot

os.environ['X_CONSUMER_KEY'] = 'original-test-value'
os.environ.pop('OPENAI_API_KEY', None)
before = dict(os.environ)

from tests.helpers import bot_runtime, bot_fixtures, reply_fixtures
import mrsMThatcher2 as bot

assert dict(os.environ) == before
assert bot_runtime.bot is bot_fixtures.bot is reply_fixtures.bot is bot
assert 'tests.test_unit_helpers' not in sys.modules
assert bot_runtime.SOURCE_GET_TWEET_BY_ID is bot.get_tweet_by_id
assert bot_runtime.SOURCE_DEFAULT_SINGLE_CALL_REPLY['enabled'] is False
assert bot.single_call_reply['enabled'] is True
assert bot_runtime.UNIT_BASE.is_dir()
assert bot_runtime.SCENARIOS == Path.cwd() / 'tests' / 'fixtures' / 'scenarios'
assert bot_runtime.SCENARIOS.is_dir()
if sys.argv[1] == 'True':
    assert bot is preloaded_bot
else:
    assert bot.BASE_DIR == bot_runtime.UNIT_BASE
    assert bot.LOG_FILE == bot_runtime.UNIT_BASE / 'unit-test.log'
    assert bot.X_BASE == bot.X_UPLOAD_BASE == 'http://127.0.0.1:9'
    assert bot.OPENAI_BASE == 'http://127.0.0.1:9/v1'

bot.single_call_reply['enabled'] = False
from tests.helpers.bot_runtime import bot as imported_again
assert imported_again.single_call_reply['enabled'] is False
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(preload_bot)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("failure", ["RuntimeError", "KeyboardInterrupt"])
def test_failed_bot_import_restores_environment(failure: str) -> None:
    code = """
import builtins
import os
import sys

os.environ['X_CONSUMER_KEY'] = 'original-test-value'
os.environ.pop('OPENAI_API_KEY', None)
before = dict(os.environ)
failure_type = getattr(builtins, sys.argv[1])
original_import = builtins.__import__

def fail_bot_import(name, *args, **kwargs):
    if name == 'mrsMThatcher2':
        assert os.environ['X_CONSUMER_KEY'] == 'dummy'
        assert os.environ['OPENAI_API_KEY'] == 'dummy'
        raise failure_type('injected bot import failure')
    return original_import(name, *args, **kwargs)

builtins.__import__ = fail_bot_import
try:
    import tests.helpers.bot_runtime
except failure_type as error:
    assert str(error) == 'injected bot import failure'
else:
    raise AssertionError('bot import unexpectedly succeeded')
finally:
    builtins.__import__ = original_import

assert dict(os.environ) == before
assert 'tests.helpers.bot_runtime' not in sys.modules
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code, failure],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_integration_helpers_use_isolated_bot_and_explicit_receipt_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = bot_runtime.bot
    assert bot.STATE_FILE == tmp_path / "bot_state.json"
    assert bot.reply_evidence_repository() is reply_fixtures.UNIT_REPLY_REPOSITORY

    receipt = reply_fixtures.unit_confirmed_v4_reply_receipt(
        lane="quote_tweet", date_for_epoch=lambda _epoch: "2026-08-01",
    )
    assert receipt["daily_reply_date"] == "2026-08-01"
    assert receipt["daily_quote_reply_date"] == "2026-08-01"

    import mrs_bot_main_post_receipts as receipts

    hash_text = lambda _text: "f" * 64
    monkeypatch.setattr(bot, "quote_text_hash", hash_text)
    monkeypatch.setattr(receipts, "quote_text_hash", hash_text)
    attempt = bot_fixtures.schema_current_main_attempt("quote_image")
    assert attempt["selected_identity"]["quote_hash"] == "f" * 64
