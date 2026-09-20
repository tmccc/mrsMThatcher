from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call
from types import SimpleNamespace

import pytest

import mrs_bot_mention_authority as authority
from tests.helpers.mention_fixtures import mention, mention_backlog, queue_active_mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401



METHODS = {'normalise_mention_pagination': 'normalise_pagination', 'normalise_mention_backlog_reset_guard': 'normalise_reset_guard', 'normalise_mention_backlog': 'normalise_backlog', 'canonical_mention_pending_candidates': 'canonical_candidates', '_emit_mention_authority_recovery': 'emit_recovery', 'validate_pending_mention_candidate_authority': 'validate_pending', 'mention_pagination_has_canonical_page_ownership': 'owns_page'}


def authority_operation(name):
    if name in METHODS:
        return getattr(bot._mention_authority_owner(), METHODS[name])
    return getattr(authority, name)


def patch_authority_method(monkeypatch, name, callback):
    if name in METHODS:
        monkeypatch.setattr(authority.MentionAuthority, METHODS[name], lambda self, *args, **kwargs: callback(*args, **kwargs))
    else:
        monkeypatch.setattr(authority, name, callback)


def test_composition_binds_current_authority_policy_without_runtime_access(monkeypatch):
    fields = {'STATE_FILE': 'state_file', 'MAX_REASONABLE_STATE_EPOCH': 'maximum_epoch', 'MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT': 'token_limit', 'log': 'log', 'log_event': 'log_event'}
    previous = None
    for _ in range(2):
        current = {name: object() for name in fields}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        owner = bot._mention_authority_owner()
        assert owner is not previous
        assert all(getattr(owner, field) is current[name] for name, field in fields.items())
        previous = owner


def test_pending_validation_calls_owned_normalizers_and_keeps_queue_children(monkeypatch):
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    candidate = state["mention_pending_candidates"]["105"]
    def forbidden(*args, **kwargs):
        raise AssertionError("mention authority bounced through root")
    for name in ("normalise_mention_backlog", "normalise_mention_pagination", "normalise_mention_backlog_reset_guard", "canonical_mention_pending_candidates"):
        monkeypatch.setattr(bot, name, forbidden)
    usable, changed = bot.validate_pending_mention_candidate_authority(
        state, path=bot.STATE_FILE, recover_pending_identity=True,
    )
    assert usable and not changed
    assert state["mention_pending_candidates"]["105"] is candidate

def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('mention authority import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_mention_authority', 'mrs_bot_state_value_normalisation', 'mrs_bot_reply_evaluation_state', 'mrs_bot_author_quarantines'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_mention_authority
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_public_arguments_defaults_references_and_native_errors(monkeypatch):
    for name, method in METHODS.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(authority.MentionAuthority, method)).parameters
        assert tuple(owned)[1:] == tuple(public)
        for key, parameter in public.items():
            assert owned[key].kind == parameter.kind
            assert owned[key].default == parameter.default
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        target = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(bot, "_mention_authority_owner", Mock(return_value=SimpleNamespace(**{method: target})))
            for use_defaults in (True, False):
                options = {
                    key: object() for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY
                    and (not use_defaults or param.default is param.empty)
                }
                expected = {
                    key: param.default for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
                } | options
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = target.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            target.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure
    assert bot.active_mention_backlog_reset_guard is authority.active_mention_backlog_reset_guard
    assert bot._reset_mention_candidate_authority is authority._reset_mention_candidate_authority
    assert bot.mention_pagination_provenance_is_valid is authority.mention_pagination_provenance_is_valid


def test_normalizers_copy_containers_and_use_current_caps_before_overflow_reset(monkeypatch):
    backlog = mention_backlog(since_id="99")
    backlog["seen_tokens"] = ["A"]
    normalised = bot.normalise_mention_backlog(backlog, path=bot.STATE_FILE)
    assert normalised == backlog and normalised is not backlog
    assert normalised["seen_tokens"] is not backlog["seen_tokens"]
    guard = {"base_since_id": "99", "head_traversal_started": False}
    normalised_guard = bot.normalise_mention_backlog_reset_guard(guard, path=bot.STATE_FILE)
    assert normalised_guard == guard and normalised_guard is not guard
    pagination = {"base_since_id": "99", "next_token": "A"}
    normalised_page = bot.normalise_mention_pagination(pagination, path=bot.STATE_FILE)
    assert normalised_page == pagination and normalised_page is not pagination

    monkeypatch.setattr(bot, "MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT", 0)
    assert bot.normalise_mention_backlog(backlog, path=bot.STATE_FILE) is None
    assert bot.normalise_mention_backlog(backlog, path=bot.STATE_FILE, reset_token_overflow=True) == {}
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", backlog["started_epoch"] - 1)
    assert bot.normalise_mention_backlog(backlog, path=bot.STATE_FILE, reset_token_overflow=True) is None


def test_active_guard_keeps_original_reference_and_reset_only_replaces_authority_fields():
    guard = {"base_since_id": "unbounded", "head_traversal_started": False}
    state = {"last_seen_mention_id": "unbounded", "mention_backlog_reset_guard": guard}
    assert bot.active_mention_backlog_reset_guard(state) is guard
    guard["head_traversal_started"] = 0
    assert bot.active_mention_backlog_reset_guard(state) is None
    guard["head_traversal_started"] = True
    state["last_seen_mention_id"] = "99"
    assert bot.active_mention_backlog_reset_guard(state) is None
    writes = []

    class ObservedState(dict):
        def __setitem__(self, key, value):
            writes.append(key)
            super().__setitem__(key, value)

    state = ObservedState(state, mention_backlog={"old": True}, mention_pagination={"old": True},
                          mention_pending_candidates={"105": mention(105, 205)}, unrelated={})
    before = dict(state)
    bot._reset_mention_candidate_authority(state, watermark="99")
    assert writes == ["mention_backlog", "mention_pagination", "mention_pending_candidates", "mention_backlog_reset_guard"]
    assert state["last_seen_mention_id"] is before["last_seen_mention_id"]
    assert state["unrelated"] is before["unrelated"]
    for key in writes:
        assert state[key] is not before[key]
    assert state["mention_backlog"] == state["mention_pagination"] == state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {"base_since_id": "99", "head_traversal_started": False}


@pytest.mark.parametrize("history_key", ["replied_to_ids", "replied_to_quote_post_ids"])
def test_pending_copies_are_shallow_and_validation_assigns_only_on_inequality(history_key):
    candidate = mention(105, 205)
    raw = {"105": candidate}
    canonical = bot.canonical_mention_pending_candidates(raw, path=bot.STATE_FILE)
    assert canonical == raw and canonical is not raw
    assert canonical["105"] is not candidate
    assert canonical["105"]["entities"] is candidate["entities"]
    state = bot.default_state()
    queue_active_mention(state, candidate, base_since_id="99")
    pending = state["mention_pending_candidates"]
    pending.update({"103": mention(103, 203), "104": mention(104, 204)})
    before = dict(state)
    assert bot.validate_pending_mention_candidate_authority(
        state, path=bot.STATE_FILE, recover_pending_identity=True,
    ) == (True, False)
    assert all(state[key] is value for key, value in before.items())

    state[history_key] = ["103"]
    bot.record_terminal_reply_evaluation(state, target_id="104", lane="mention", reason="confirmed_no_reply")
    assert bot.validate_pending_mention_candidate_authority(
        state, path=bot.STATE_FILE, recover_pending_identity=True,
    ) == (True, True)
    kept = state["mention_pending_candidates"]
    assert set(kept) == {"105"} and kept is not pending
    assert kept["105"] is not pending["105"]
    assert kept["105"]["entities"] is pending["105"]["entities"]
    for key in ("mention_backlog", "mention_pagination", "mention_backlog_reset_guard"):
        assert state[key] is before[key]


@pytest.mark.parametrize("failure_at", ["warning", "event"])
def test_recovery_capture_keeps_original_event_and_immediate_logging_error_order(monkeypatch, failure_at):
    trace = Mock()
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "log_event", trace.event)
    recovery = {"reason": "test-recovery", "discarded_candidates": 2, "detail": {}}
    events = []
    bot._emit_mention_authority_recovery(recovery, path=bot.STATE_FILE, recovery_events=events)
    assert len(events) == 1 and events[0] is recovery
    assert trace.mock_calls == []
    failure = OSError("current recovery logger failed")
    (trace.log.warning if failure_at == "warning" else trace.event).side_effect = failure
    with pytest.raises(OSError) as caught:
        bot._emit_mention_authority_recovery(recovery, path=bot.STATE_FILE, recovery_events=None)
    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == (
        ["log.warning"] if failure_at == "warning" else ["log.warning", "event"]
    )
    assert trace.log.warning.call_args.args[1:] == ("test-recovery", bot.STATE_FILE, 2)
    if failure_at == "event":
        trace.event.assert_called_once_with("mention_backlog_reset", **recovery)
        assert trace.event.call_args.kwargs["detail"] is recovery["detail"]


def test_normalizer_error_leaves_prior_deduplication_and_skips_guard_reset_and_event(monkeypatch):
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    pending = state["mention_pending_candidates"]
    pending["103"] = mention(103, 203)
    state["replied_to_ids"] = ["103"]
    before = dict(state)
    trace = Mock()
    for label, name in (
        ("backlog", "normalise_mention_backlog"), ("pagination", "normalise_mention_pagination"),
        ("guard", "normalise_mention_backlog_reset_guard"), ("reset", "_reset_mention_candidate_authority"),
        ("emit", "_emit_mention_authority_recovery"),
    ):
        callback = Mock(wraps=authority_operation(name))
        trace.attach_mock(callback, label)
        patch_authority_method(monkeypatch, name, callback)
    failure = TypeError("current pagination normalizer failed")
    trace.pagination.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.validate_pending_mention_candidate_authority(state, path=bot.STATE_FILE, recover_pending_identity=True)
    assert caught.value is failure
    assert trace.mock_calls == [call.backlog(before["mention_backlog"], path=bot.STATE_FILE),
                                call.pagination(before["mention_pagination"], path=bot.STATE_FILE)]
    assert set(state["mention_pending_candidates"]) == {"105"}
    assert state["mention_pending_candidates"] is not pending
    assert state["mention_pending_candidates"]["105"]["entities"] is pending["105"]["entities"]
    for key in ("mention_backlog", "mention_pagination", "mention_backlog_reset_guard", "last_seen_mention_id"):
        assert state[key] is before[key]


def test_invalid_maps_keep_failure_precedence_and_reset_before_current_emitter_error(monkeypatch):
    state = bot.default_state()
    state.update(last_seen_mention_id="99", mention_backlog={"invalid": True},
                 mention_pagination={"invalid": True}, mention_backlog_reset_guard={"invalid": True},
                 mention_pending_candidates={"105": mention(105, 205)})
    trace = Mock()
    for label, name in (
        ("backlog", "normalise_mention_backlog"), ("pagination", "normalise_mention_pagination"),
        ("guard", "normalise_mention_backlog_reset_guard"), ("reset", "_reset_mention_candidate_authority"),
    ):
        callback = Mock(wraps=authority_operation(name))
        trace.attach_mock(callback, label)
        patch_authority_method(monkeypatch, name, callback)
    failure = RuntimeError("current emitter failed after reset")
    trace.emit.side_effect = failure
    patch_authority_method(monkeypatch, "_emit_mention_authority_recovery", trace.emit)
    events = []
    with pytest.raises(RuntimeError) as caught:
        bot.validate_pending_mention_candidate_authority(
            state, path=bot.STATE_FILE, recover_pending_identity=True, recovery_events=events,
        )
    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == ["backlog", "pagination", "guard", "reset", "emit"]
    assert trace.reset.call_args.args[0] is state
    trace.emit.assert_called_once_with({
        "reason": "stale_pending_candidate_authority", "authority_failure": "invalid_backlog",
        "since_id": "99", "discarded_candidates": 1,
    }, path=bot.STATE_FILE, recovery_events=events)
    assert trace.emit.call_args.kwargs["recovery_events"] is events
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_backlog"] == state["mention_pagination"] == state["mention_pending_candidates"] == {}
    assert state["mention_backlog_reset_guard"] == {"base_since_id": "99", "head_traversal_started": False}


def test_page_ownership_uses_current_path_and_exact_backlog_equality_before_id_checks(monkeypatch, tmp_path):
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    pagination = dict(state["mention_pagination"])
    before = dict(state)
    path = tmp_path / "current-state.json"
    monkeypatch.setattr(bot, "STATE_FILE", path)
    normalizer = Mock(wraps=authority_operation("normalise_mention_backlog"))
    patch_authority_method(monkeypatch, "normalise_mention_backlog", normalizer)
    assert bot.mention_pagination_has_canonical_page_ownership(state, pagination, target_id="105") is True
    normalizer.assert_called_once_with(state["mention_backlog"], path=path)
    assert normalizer.call_args.args[0] is state["mention_backlog"]
    assert normalizer.call_args.kwargs["path"] is path
    assert all(state[key] is value for key, value in before.items())
    assert bot.mention_pagination_has_canonical_page_ownership(state, pagination, target_id="99") is False
    assert bot.mention_pagination_has_canonical_page_ownership(state, pagination, target_id="106") is False
    normalizer.reset_mock()
    normalizer.return_value = {**state["mention_backlog"], "announced": False}
    bounded = Mock(wraps=bot.bounded_tweet_id_value)
    monkeypatch.setattr(authority, "bounded_tweet_id_value", bounded)
    assert bot.mention_pagination_has_canonical_page_ownership(state, pagination, target_id="105") is False
    # Provenance checks the base; unequal canonical backlog stops the later range checks.
    assert bounded.mock_calls == [call("99", allow_empty=True)]
    normalizer.side_effect = TypeError("current backlog normalizer failed")
    with pytest.raises(TypeError) as caught:
        bot.mention_pagination_has_canonical_page_ownership(state, pagination, target_id="105")
    assert caught.value is normalizer.side_effect
