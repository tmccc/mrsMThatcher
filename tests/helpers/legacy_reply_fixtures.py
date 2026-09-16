"""Share historical conversational-reply receipts and recovery isolation."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile

import pytest

from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.helpers.protocol_activation import create_test_protocol_activation

_IMPORT_TEMPORARY = tempfile.TemporaryDirectory(
    prefix="mrsMThatcher-legacy-reply-recovery-import-"
)
_IMPORT_BASE = Path(_IMPORT_TEMPORARY.name)
_IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(_IMPORT_BASE),
    "MRS_LOG_FILE": str(_IMPORT_BASE / "test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "OPENAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "OPENAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
_ORIGINAL_ENV = {name: os.environ.get(name) for name in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

import mrsMThatcher2 as bot  # noqa: E402
import remote_write_transport_journal as transport_module  # noqa: E402

for _name, _value in _ORIGINAL_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "legacy_conversational_reply_receipts.json"
)
LEGACY_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]
CASE_IDS = [str(case["case_id"]) for case in LEGACY_CASES]


def _forbid(operation: str):
    """Reject unintended network or posting operations during local recovery."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"legacy receipt recovery attempted forbidden {operation}")

    return forbidden


@pytest.fixture(autouse=True)
def isolated_recovery_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate legacy receipt paths and prevent outbound recovery writes."""

    transport_module.reset_consumed_authorities_for_tests()
    paths = {
        "BASE_DIR": tmp_path,
        "STATE_FILE": tmp_path / "bot_state.json",
        "REGULAR_POST_RECEIPT_FILE": tmp_path / "regular_post_receipt.json",
        "MEME_POST_RECEIPT_FILE": tmp_path / "meme_post_receipt.json",
        "CONFIRMED_REPLY_RECEIPT_FILE": tmp_path / "confirmed_reply_receipt.json",
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE": (
            tmp_path / "historical_context_reply_receipt.json"
        ),
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE": tmp_path / "context_history.json",
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE": tmp_path / "context_outbox.json",
        "AMBIGUOUS_POST_OUTCOME_FILE": tmp_path / "ambiguous_post_outcome.json",
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE": (
            tmp_path / "ambiguous_post_outcome.restart_barrier.json"
        ),
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE": (
            tmp_path / REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME
        ),
        "CONTROL_FILE": tmp_path / "control.json",
    }
    for name, value in paths.items():
        monkeypatch.setattr(bot, name, value)
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "x_request", _forbid("X request"))
    monkeypatch.setattr(bot.requests, "request", _forbid("network request"))
    monkeypatch.setattr(bot, "create_post", _forbid("X post"))


def _legacy_case(case_id: str) -> dict:
    """Return a fresh copy of a named historical receipt case."""

    return copy.deepcopy(
        next(case for case in LEGACY_CASES if case["case_id"] == case_id)
    )


def _confirmed_receipt(case: dict, receipt_schema_version: int = 4) -> dict:
    """Build the requested historical confirmed-receipt schema."""

    sending = copy.deepcopy(case["sending_receipt"])
    confirmation_epoch = int(case["confirmation_epoch"])
    sending["schema_version"] = receipt_schema_version
    if receipt_schema_version == 4:
        confirmed = {
            **sending,
            "lifecycle_state": "confirmed",
            "reply_post_id": str(case["reply_post_id"]),
            "confirmation_epoch": confirmation_epoch,
            "reply_epoch": confirmation_epoch,
            "daily_reply_date": bot.epoch_date_str(confirmation_epoch),
            "source_receipt_sha256": str(case["source_receipt_sha256"]),
        }
        return confirmed
    sending.pop("attempt_epoch", None)
    sending.pop("source_receipt_sha256", None)
    sending["reply_epoch"] = confirmation_epoch
    sending["daily_reply_date"] = bot.epoch_date_str(confirmation_epoch)
    sending["reply_post_id"] = str(case["reply_post_id"])
    if receipt_schema_version == 3:
        sending["lifecycle_state"] = "confirmed"
    else:
        assert receipt_schema_version == 2
        sending.pop("lifecycle_state", None)
    return sending
