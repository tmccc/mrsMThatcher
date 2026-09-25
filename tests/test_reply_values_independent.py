"""Keep synthetic reply values independent and calendar rules distinct."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from tests.helpers.mention_values import mention
from tests.helpers.reply_values import (
    UnitReplyEvidenceRepository, unit_approved_reply, unit_confirmed_v4_reply_receipt,
    unit_reply_context,
)


def test_builder_adds_declared_facts_only_to_its_explicit_repository():
    first = UnitReplyEvidenceRepository()
    second = UnitReplyEvidenceRepository()
    context = unit_reply_context()
    reply = unit_approved_reply(
        context, text="People moved west in 1989.", factual=True, repository=first,
    )
    assert reply.draft_record["factual_claims"]
    assert len(first.passages) == 2
    assert len(second.passages) == 1
    assert unit_approved_reply(context).draft_record
    assert len(first.passages) == 2
    assert mention(1, 2, account_id="67890")["entities"]["mentions"] == [
        {"id": "67890", "username": "MrsMThatcher"}
    ]


def test_legacy_ambient_and_current_london_receipt_dates_diverge_at_boundary():
    script = """
import builtins, sys, time
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'tests.helpers.bot_runtime'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from datetime import datetime, timezone
from tests.helpers.reply_values import (
    ambient_receipt_date, reply_cap_receipt_date, unit_confirmed_reply_receipt,
    unit_confirmed_v4_reply_receipt, unit_reply_context, unit_approved_reply,
)
from tests.helpers.mention_values import mention
import calendar
epoch = calendar.timegm((2026, 7, 1, 23, 30, 0))
assert ambient_receipt_date(epoch) == '2026-07-01'
assert reply_cap_receipt_date(epoch) == '2026-07-02'
assert unit_confirmed_reply_receipt(epoch=epoch)['daily_reply_date'] == '2026-07-01'
assert unit_confirmed_v4_reply_receipt(confirmation_epoch=epoch)['daily_reply_date'] == '2026-07-02'
assert unit_approved_reply(unit_reply_context()).draft_record
assert mention(1, 2)['entities']['mentions'][0]['id'] == '12345'
assert 'mrsMThatcher2' not in sys.modules
assert 'tests.helpers.bot_runtime' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "TZ": "America/Los_Angeles"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
