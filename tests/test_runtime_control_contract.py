"""Bot and digest controls agree on validity and pause expiry."""

from datetime import datetime, timezone

import pytest

import mrs_log_digest as digest
import mrs_log_digest_runtime as digest_runtime
import runtime_control_contract as contract
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


NOW = 1_700_000_000


@pytest.mark.parametrize(
    "raw,valid,active_keys",
    [
        (b'{}', True, []),
        (b'{"generation":0}', True, []),
        (b'{"generation":null}', False, []),
        (b'{"generation":true}', False, []),
        (b'{"generation":-1}', False, []),
        (b'{"generation":1.0}', False, []),
        (b'{"generation":"1"}', False, []),
        (b'{"pause_all":true}', True, ["pause_all"]),
        (b'{"pause_all":false}', True, []),
        (b'{"pause_all":" YES "}', True, ["pause_all"]),
        (b'{"pause_all":" off "}', True, []),
        (b'{"pause_all":1}', False, []),
        (b'{"pause_all":null}', False, []),
        (b'{"pause_replies":"on","disable_quote_posts":true}', True,
         ["disable_quote_posts", "pause_replies"]),
        (b'{"pause_all_until":0}', True, []),
        (b'{"pause_all_until":1699999999}', True, []),
        (b'{"pause_all_until":1700000000}', True, []),
        (b'{"pause_all_until":1700000001}', True, ["pause_all_until"]),
        (b'{"pause_all_until":1700000001.0}', True, ["pause_all_until"]),
        (b'{"pause_all_until":4102444801}', True, ["pause_all_until"]),
        (b'{"pause_all_until":4102531200}', True, ["pause_all_until"]),
        (b'{"pause_all_until":4102531200.0}', True, ["pause_all_until"]),
        (b'{"pause_all_until":4102531201}', False, []),
        (b'{"pause_all_until":-1}', False, []),
        (b'{"pause_all_until":1.0000000000000000000000000001}', False, []),
        (b'{"pause_all_until":1e100000}', False, []),
        (b'{"pause_all_until":true}', False, []),
        (b'{"pause_all_until":null}', False, []),
        (b'{"pause_all_until":"1700000001"}', False, []),
        (b'{"pause_all_until":"2030-01-02T03:04:05+00:00"}', True,
         ["pause_all_until"]),
        (b'{"pause_all_until":"2030-01-02 03:04"}', True, ["pause_all_until"]),
        (b'{"pause_all_until":"not a date"}', False, []),
        (b'{"unexpected":false}', False, []),
        (b'{"pause_all":true,"pause_all":false}', False, []),
        (b'{"pause_all_until":NaN}', False, []),
        (b'{"pause_all_until":Infinity}', False, []),
        (b'[]', False, []),
        (b'null', False, []),
        (b'{', False, []),
    ],
)
def test_bot_loader_and_digest_snapshot_agree(tmp_path, monkeypatch, raw, valid, active_keys):
    path = tmp_path / "mrsMThatcher.control.json"
    path.write_bytes(raw)
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {
        "signature": None, "data": {}, "has_valid": False, "failure_signature": None,
    })
    monkeypatch.setattr(bot, "now_epoch", lambda: NOW)

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(NOW, tz=timezone.utc)

    monkeypatch.setattr(digest, "datetime", FixedTime)
    loaded = bot.load_control()
    snapshot = digest.runtime_control_snapshot(tmp_path)

    assert snapshot["valid"] is valid
    assert bool(loaded.get("_control_fail_closed")) is not valid
    if valid:
        assert snapshot["active_keys"] == active_keys
        expected_global = bool(set(active_keys) & {
            "pause_all", "disable_all", "pause_all_until", "disable_all_until",
        })
        for key in contract.CONTROL_BOOLEAN_KEYS:
            paused, _reason, _until = bot.control_pause_active(loaded, key)
            assert paused is bool({key, f"{key}_until"} & set(active_keys))
    else:
        assert snapshot["active_keys"] == ["fail_closed_invalid_control"]
        expected_global = True
    paused, _reason, _until = bot.control_pause_active(loaded, "disable_all", "pause_all")
    assert paused is expected_global
    assert snapshot["global_pause_active"] is expected_global


def test_consumers_share_control_key_sets_and_epoch_bound():
    assert bot.MAX_REASONABLE_STATE_EPOCH == contract.MAX_CONTROL_EPOCH
    assert digest_runtime.MAX_CONTROL_EPOCH == contract.MAX_CONTROL_EPOCH
    for suffix in ("ALLOWED_KEYS", "BOOLEAN_KEYS", "TIME_KEYS"):
        shared = getattr(contract, "CONTROL_" + suffix)
        assert getattr(bot, "CONTROL_" + suffix) is shared
        assert getattr(digest_runtime, "REMOTE_WRITE_CONTROL_" + suffix) is shared
