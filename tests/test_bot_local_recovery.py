"""Contract for the finite recovery pass shared by startup and runtime.

Decision table (the final barrier is reassessed on every row):

Evidence                         Local order              Forbidden                 Handoff
-------------------------------  -----------------------  ------------------------  -------------------------
No receipt/guard/barrier          media, source, reconcile remote/model, state write  next lifecycle stage
Maintenance pause                barrier durability       all transaction recovery  wait or runtime pause tick
Incident/durability uncertainty  barrier durability       retirement, reconcile     controlled recovery, wait
Confirmed exact lineage          media, source, reconcile resend, duplicate account  next stage if clear
Prepared source guard            source/journal, then     reconcile before guard    next stage after proof
                                 confirmed reconcile
Persistent retirement failure    barrier durability       reconcile, remote work    retry local or controlled
Conflicting receipt namespaces   barrier durability       select/delete/promote     controlled repair
Unproved sending receipt         reconcile no-op, barrier promote/resend             runtime blocked tick
Progress with another blocker    local work, barrier      remote work               hand remaining blocker off
Failed state persistence         barrier durability       ordinary save of memory   reload committed state
Unrelated exception              propagate                scheduling                caller retains boundary

The owner supplies current observations, not remote-write permission. The
existing lane and send-time checks still govern every later remote operation.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mrs_bot_local_recovery import (
    BarrierState, LocalRecovery,
    RecoveryErrors,
    RecoveryStage,
    RecoveryStatus,
)


class RecognisedReplyFailure(Exception):
    """Model one recognised confirmed-reply persistence failure."""


class RecognisedRetirementFailure(Exception):
    """Model one recognised exact-retirement failure."""


class RecognisedMainFailure(Exception):
    """Model one recognised main-post local failure."""


def test_import_has_no_runtime_authority():
    """The decision owner can be imported without touching process state."""
    code = """
import builtins, io, logging, os, random, socket, sys, time
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('local recovery import used runtime authority')
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'}:
        forbidden()
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_local_recovery
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def make_recovery(*, paused=False, latched=False, blocked=False, media=False,
                  source=False, reconciled=None, stage_failure=None, events=None):
    """Build an inert owner whose reads and operations leave a trace."""
    events = [] if events is None else events
    flags = SimpleNamespace(paused=paused, latched=latched, blocked=blocked)

    def pause():
        events.append("pause")
        return flags.paused

    def latch():
        events.append("latch")
        return flags.latched

    def resume_media():
        events.append("media")
        if stage_failure == "media":
            raise RecognisedRetirementFailure("media")
        return media

    def resume_source(*, maintenance_paused):
        assert maintenance_paused is False
        events.append("source")
        if stage_failure == "source":
            raise RecognisedRetirementFailure("source")
        return source

    def reconcile(*_args):
        events.append("reconcile")
        if stage_failure == "reply":
            raise RecognisedReplyFailure("reply")
        if stage_failure == "main":
            raise RecognisedMainFailure("main")
        return reconciled or {"regular": False, "meme": False,
                              "conversational_reply": False}

    def maintain():
        events.append("barrier")
        return flags.blocked or flags.latched

    owner = LocalRecovery(
        global_paused=pause,
        incident_latched=latch,
        resume_media_retirement=resume_media,
        resume_source_retirement=resume_source,
        reconcile_confirmed_transactions=reconcile,
        maintain_barrier=maintain,
        errors=RecoveryErrors(
            media=(RecognisedRetirementFailure,),
            source=(RecognisedRetirementFailure,),
            reconcile=(RecognisedReplyFailure, RecognisedMainFailure),
        ),
    )
    return owner, flags, events


@pytest.mark.parametrize(
    "scenario,expected_status,expected_operations",
    [
        ("clear", RecoveryStatus.CLEAR,
         ["media", "source", "reconcile", "barrier"]),
        ("paused", RecoveryStatus.PAUSED, ["barrier"]),
        ("incident", RecoveryStatus.INCIDENT, ["barrier"]),
        ("unproved_sending", RecoveryStatus.BLOCKED,
         ["media", "source", "reconcile", "barrier"]),
        ("confirmed", RecoveryStatus.RECOVERED,
         ["media", "source", "reconcile", "barrier"]),
        ("guard", RecoveryStatus.RECOVERED,
         ["media", "source", "reconcile", "barrier"]),
        ("progress_blocked", RecoveryStatus.PROGRESSED_BLOCKED,
         ["media", "source", "reconcile", "barrier"]),
        ("source_failure", RecoveryStatus.FAILED,
         ["media", "source", "barrier"]),
        ("media_failure", RecoveryStatus.FAILED,
         ["media", "barrier"]),
        ("reply_failure", RecoveryStatus.FAILED,
         ["media", "source", "reconcile", "barrier"]),
        ("main_failure", RecoveryStatus.FAILED,
         ["media", "source", "reconcile", "barrier"]),
    ],
)
def test_shared_decision_trace(scenario, expected_status, expected_operations):
    """One policy orders local work, barriers and recognised failures."""
    owner, _flags, events = make_recovery(
        paused=scenario == "paused",
        latched=scenario == "incident",
        blocked=scenario in {"unproved_sending", "progress_blocked"},
        source=scenario in {"guard", "progress_blocked"},
        reconciled={"conversational_reply": True} if scenario == "confirmed" else None,
        stage_failure=(scenario[:-8] if scenario.endswith("_failure") else None),
    )
    result = owner.run_once(set(), set(), {}, current=123)
    assert result.status is expected_status
    assert result.barrier is (
        BarrierState.BLOCKED if scenario in {
            "incident", "unproved_sending", "progress_blocked",
        } else BarrierState.CLEAR
    )
    assert [event for event in events if event in {
        "media", "source", "reconcile", "barrier",
    }] == expected_operations
    assert result.reload_committed_state is (
        scenario in {"reply_failure", "main_failure"}
    )
    assert result.failure_stage is (
        RecoveryStage.SOURCE if scenario == "source_failure" else
        RecoveryStage.MEDIA if scenario == "media_failure" else
        RecoveryStage.RECONCILE if scenario in {"reply_failure", "main_failure"}
        else None
    )


@pytest.mark.parametrize("transition,expected_status,expected_operations", [
    ("pause_after_media", RecoveryStatus.PAUSED, ["media", "barrier"]),
    ("pause_after_source", RecoveryStatus.PAUSED,
     ["media", "source", "barrier"]),
    ("incident_after_source", RecoveryStatus.INCIDENT,
     ["media", "source", "barrier"]),
])
def test_control_transition_rechecked_before_next_local_stage(
    transition, expected_status, expected_operations,
):
    """A stale clear snapshot cannot authorise the next recovery stage."""
    events = []
    owner, flags, events = make_recovery(events=events)
    original_media = owner.resume_media_retirement
    original_source = owner.resume_source_retirement

    def media():
        result = original_media()
        if transition == "pause_after_media":
            flags.paused = True
        return result

    def source(*, maintenance_paused):
        result = original_source(maintenance_paused=maintenance_paused)
        if transition == "pause_after_source":
            flags.paused = True
        if transition == "incident_after_source":
            flags.latched = True
        return result

    owner.resume_media_retirement = media
    owner.resume_source_retirement = source
    outcome = owner.run_once(set(), set(), {})
    assert outcome.status is expected_status
    assert [event for event in events if event in {
        "media", "source", "reconcile", "barrier",
    }] == expected_operations


def test_unrelated_reconciliation_error_propagates():
    """Unknown failures keep their identity instead of becoming a normal block."""
    owner, _flags, events = make_recovery()
    failure = ValueError("unexpected")

    def unexpected(*_args):
        events.append("reconcile")
        raise failure

    owner.reconcile_confirmed_transactions = unexpected
    with pytest.raises(ValueError) as caught:
        owner.run_once(set(), set(), {})
    assert caught.value is failure
    assert "barrier" not in events


def test_retirement_uncertainty_keeps_incident_distinct_from_local_retry():
    """A process latch changes the hand-off even when an error was recognised."""
    owner, flags, events = make_recovery(stage_failure="source", blocked=True)
    original_source = owner.resume_source_retirement

    def uncertain_source(*, maintenance_paused):
        try:
            return original_source(maintenance_paused=maintenance_paused)
        finally:
            flags.latched = True

    owner.resume_source_retirement = uncertain_source
    result = owner.run_once(set(), set(), {})
    assert result.status is RecoveryStatus.INCIDENT
    assert result.failure_stage is RecoveryStage.SOURCE
    assert result.barrier is BarrierState.BLOCKED
    assert "reconcile" not in events
