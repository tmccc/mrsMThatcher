"""Contracts for engagement runtime extraction, exact inputs and local delivery."""
from __future__ import annotations

import copy
import inspect
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_engagement_runtime as owner
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.test_production_consistency_incident import isolated_incident_paths  # noqa: F401

DEPENDENCIES = {'engagement_experiment_envelope_from_receipt': [],
 'configured_engagement_question_path': ['BASE_DIR', 'Path'],
 'current_exact_quote_text_by_sha256': ['LINES_FILE', 'engagement_question_trial'],
 'load_engagement_question_runtime_plan': ['BASE_DIR',
                                           '_get_engagement_question_last_loaded_plan_sha256',
                                           '_set_engagement_question_last_loaded_plan_sha256',
                                           'configured_engagement_question_path',
                                           'current_exact_quote_text_by_sha256',
                                           'engagement_question_experiment_plan_path',
                                           'engagement_question_trial',
                                           'load_receipt_json_no_follow',
                                           'log_event'],
 'publish_pending_engagement_question_notification': ['ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS',
                                                      '_get_engagement_question_last_notification_failure_post_id',
                                                      '_set_engagement_question_last_notification_failure_post_id',
                                                      'atomic_write_json',
                                                      'configured_engagement_question_path',
                                                      'copy',
                                                      'engagement_question_notification_output_path',
                                                      'engagement_question_trial',
                                                      'json_file_matches',
                                                      'log',
                                                      'log_event',
                                                      'now_epoch',
                                                      'os',
                                                      'save_state',
                                                      'stat']}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Engagement-runtime import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'engagement_question_experiment', 'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_engagement_runtime':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
datetime.datetime = type("ForbiddenDatetime", (), dict(now=forbidden, fromtimestamp=forbidden, today=forbidden))
zoneinfo.ZoneInfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_engagement_runtime
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_engagement_runtime.configured_engagement_question_path.__annotations__['return'] == 'Path'
assert mrs_bot_engagement_runtime.load_engagement_question_runtime_plan.__annotations__['return'] == 'tuple[dict, dict, dict[str, str]]'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_alias_and_current_adapter_references(monkeypatch, name):
    signatures = {
        "engagement_experiment_envelope_from_receipt": "(receipt: 'object') -> 'dict | None'",
        "configured_engagement_question_path": "(raw_path: 'str') -> 'Path'",
        "current_exact_quote_text_by_sha256": "() -> 'dict[str, str]'",
        "load_engagement_question_runtime_plan": "() -> 'tuple[dict, dict, dict[str, str]]'",
        "publish_pending_engagement_question_notification": "(state: 'dict') -> 'bool'",
    }
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert str(inspect.signature(public)) == signatures[name]
    assert public.__doc__ == extracted.__doc__
    if not DEPENDENCIES[name]:
        assert public is extracted
        return
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    args = (object(),) if name in {"configured_engagement_question_path", "publish_pending_engagement_question_notification"} else ()
    for _ in range(2):
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        assert public(*args) is result
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == dependencies
        assert all(callback.call_args.kwargs[key] is value for key, value in dependencies.items())


@pytest.mark.parametrize("suffix", ["plan_sha256", "notification_failure_post_id"])
def test_shared_access_observes_and_assigns_current_root_reference(monkeypatch, suffix):
    stem = "engagement_question_last_" + ("loaded_" if suffix == "plan_sha256" else "") + suffix
    current, replacement = object(), object()
    monkeypatch.setattr(bot, "_" + stem.upper(), current)
    getter, setter = getattr(bot, "_get_" + stem), getattr(bot, "_set_" + stem)
    assert getter() is current
    assert setter(replacement) is None
    assert getter() is replacement
    assert getattr(bot, "_" + stem.upper()) is replacement


def test_envelope_preserves_lazy_schema_comparison_and_dictionary_reference():
    events, envelope = [], {}

    class Schema:
        def __ne__(self, other):
            events.append(("compare", other))
            return False

    class Receipt(dict):
        def get(self, key):
            events.append(key)
            return super().get(key)

    receipt = Receipt(schema_version=Schema(), engagement_question_experiment=envelope)
    assert bot.engagement_experiment_envelope_from_receipt(receipt) is envelope
    assert events == ["schema_version", ("compare", 4), "engagement_question_experiment"]
    events.clear()
    receipt["schema_version"] = 3
    assert bot.engagement_experiment_envelope_from_receipt(receipt) is None
    assert events == ["schema_version"]
    assert bot.engagement_experiment_envelope_from_receipt([]) is None
    receipt.update(schema_version=4, engagement_question_experiment=[])
    assert bot.engagement_experiment_envelope_from_receipt(receipt) is None


@pytest.mark.parametrize("absolute", [True, False])
def test_configured_path_constructs_then_checks_and_lazily_joins(monkeypatch, absolute):
    events = Mock()
    raw, result = object(), object()
    path = SimpleNamespace(is_absolute=events.is_absolute)
    events.Path.return_value = path
    events.is_absolute.return_value = absolute

    class Base:
        def __truediv__(self, other):
            assert other is path
            events.join(other)
            return result

    monkeypatch.setattr(bot, "Path", events.Path)
    monkeypatch.setattr(bot, "BASE_DIR", Base())
    assert bot.configured_engagement_question_path(raw) is (path if absolute else result)
    assert events.mock_calls == [call.Path(raw), call.is_absolute(), *([] if absolute else [call.join(path)])]


def test_quote_source_keeps_exact_utf8_lf_whitespace_duplicates_and_order(monkeypatch, tmp_path):
    path = tmp_path / "quotes.txt"
    path.write_bytes("  café  \n\n\tSecond\n  café  \n \n".encode("utf-8"))
    sha = Mock(side_effect=lambda text: "hash:" + text)
    monkeypatch.setattr(bot, "LINES_FILE", path)
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(sha256_text=sha))
    result = bot.current_exact_quote_text_by_sha256()
    assert list(result.items()) == [("hash:" + text, text) for text in ["  café  ", "\tSecond", " "]]
    assert sha.call_args_list == [call(text) for text in ["  café  ", "\tSecond", "  café  ", " "]]


@pytest.mark.parametrize("source,error", [(b"one\r\n", RuntimeError), (b"\xff", UnicodeDecodeError), (b"one\ntwo", RuntimeError), (None, FileNotFoundError)])
def test_quote_source_native_refusals(monkeypatch, tmp_path, source, error):
    path = tmp_path / "quotes.txt"
    if source is not None:
        path.write_bytes(source)
    sha = Mock(return_value="collision")
    monkeypatch.setattr(bot, "LINES_FILE", path)
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(sha256_text=sha))
    with pytest.raises(error):
        bot.current_exact_quote_text_by_sha256()
    assert sha.call_count == (2 if source == b"one\ntwo" else 0)


def _callbacks(monkeypatch, *names):
    events = Mock()
    for name in names:
        monkeypatch.setattr(bot, name, getattr(events, name))
    return events


def _plan_inputs(monkeypatch, tmp_path):
    events = _callbacks(monkeypatch, "configured_engagement_question_path", "load_receipt_json_no_follow", "current_exact_quote_text_by_sha256", "engagement_question_trial", "log_event")

    class Plan(dict):
        def __getitem__(self, key):
            events.plan_key(key)
            return super().__getitem__(key)

    plan = Plan(plan_sha256="plan", experiment_id="synthetic", pair_count=2)
    document, catalogue, quotes = {}, {}, {}
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "engagement_question_experiment_plan_path", "synthetic-plan.json")
    monkeypatch.setattr(bot, "_ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256", None)
    for prefix in ("get", "set"):
        name = f"_{prefix}_engagement_question_last_loaded_plan_sha256"
        callback = Mock(wraps=getattr(bot, name))
        events.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    events.configured_engagement_question_path.return_value = tmp_path / "plan.json"
    events.load_receipt_json_no_follow.return_value = (True, document)
    events.current_exact_quote_text_by_sha256.return_value = quotes
    events.engagement_question_trial.load_approved_catalogue.return_value = (catalogue, "catalogue-hash")
    events.engagement_question_trial.validate_plan_document.return_value = plan
    return events, plan, document, catalogue, quotes


def test_plan_validation_references_event_order_repeated_reads_and_shared_cache(monkeypatch, tmp_path):
    events, plan, document, catalogue, quotes = _plan_inputs(monkeypatch, tmp_path)
    events.log_event.side_effect = lambda *args, **kwargs: plan.update(plan_sha256="after-event")
    result = bot.load_engagement_question_runtime_plan()
    assert all(a is b for a, b in zip(result, (plan, catalogue, quotes)))
    assert events.mock_calls == [
        call.configured_engagement_question_path("synthetic-plan.json"),
        call.load_receipt_json_no_follow(tmp_path / "plan.json"),
        call.current_exact_quote_text_by_sha256(),
        call.engagement_question_trial.load_approved_catalogue(tmp_path / "engagement_question_experiment" / "approved_question_catalogue.json", quotes),
        call.engagement_question_trial.validate_plan_document(document, catalogue=catalogue, catalogue_sha256="catalogue-hash", quote_text_by_id=quotes, require_plan_kind="live"),
        call._get_engagement_question_last_loaded_plan_sha256(), call.plan_key("plan_sha256"),
        call.plan_key("experiment_id"), call.plan_key("plan_sha256"), call.plan_key("pair_count"),
        call.log_event("engagement_question_experiment_plan_loaded", experiment_id="synthetic", plan_sha256="plan", pair_count=2),
        call.plan_key("plan_sha256"), call._set_engagement_question_last_loaded_plan_sha256("after-event"),
    ]
    validation = events.engagement_question_trial.validate_plan_document.call_args
    assert validation.args[0] is document and validation.kwargs["catalogue"] is catalogue
    assert validation.kwargs["quote_text_by_id"] is quotes
    assert list(events.log_event.call_args.kwargs) == ["experiment_id", "plan_sha256", "pair_count"]
    events.reset_mock()
    assert all(a is b for a, b in zip(bot.load_engagement_question_runtime_plan(), result))
    events.log_event.assert_not_called()
    events._set_engagement_question_last_loaded_plan_sha256.assert_not_called()
    events.engagement_question_trial.validate_plan_document.assert_called_once()
    assert events.plan_key.call_args_list == [call("plan_sha256")]


@pytest.mark.parametrize("failure", ["absent", "nondict", "quotes", "event"])
def test_plan_refusals_and_event_failure_leave_cache_unchanged(monkeypatch, tmp_path, failure):
    events, *_ = _plan_inputs(monkeypatch, tmp_path)
    if failure in {"absent", "nondict"}:
        events.load_receipt_json_no_follow.return_value = (failure != "absent", [])
        expected = RuntimeError
    else:
        expected = LookupError
        getattr(events, "current_exact_quote_text_by_sha256" if failure == "quotes" else "log_event").side_effect = expected("synthetic failure")
    with pytest.raises(expected):
        bot.load_engagement_question_runtime_plan()
    assert bot._ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256 is None
    events._set_engagement_question_last_loaded_plan_sha256.assert_not_called()
    if failure != "event":
        events.engagement_question_trial.load_approved_catalogue.assert_not_called()
    if failure in {"absent", "nondict"}:
        events.current_exact_quote_text_by_sha256.assert_not_called()


def _notification_inputs(monkeypatch, tmp_path):
    events = _callbacks(monkeypatch, "engagement_question_trial", "configured_engagement_question_path", "json_file_matches", "atomic_write_json", "save_state", "now_epoch", "log", "log_event", "copy", "os")
    original = {"nested": {"pending": True}}
    state = {"engagement_question_experiment": original}
    identity = {"post_id": 123, "document": {"question": ["Synthetic?"]}, "document_sha256": "hash"}
    monkeypatch.setattr(bot, "engagement_question_notification_output_path", "synthetic-notification.json")
    monkeypatch.setattr(bot, "_ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID", "old")
    for prefix in ("get", "set"):
        name = f"_{prefix}_engagement_question_last_notification_failure_post_id"
        callback = Mock(wraps=getattr(bot, name))
        events.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    events.copy.deepcopy.side_effect = copy.deepcopy
    events.engagement_question_trial.EXPERIMENT_ID = "synthetic"
    events.engagement_question_trial.pending_treatment_notification.return_value = identity
    events.engagement_question_trial.validate_notification_document.side_effect = lambda document: document
    events.engagement_question_trial.canonical_sha256.return_value = "hash"
    events.configured_engagement_question_path.return_value = tmp_path / "notification.json"
    events.json_file_matches.side_effect = [False, True]
    events.os.lstat.side_effect = FileNotFoundError("synthetic missing output")

    def mark(observed, post_id):
        assert observed is original and post_id == "123"
        observed["nested"]["pending"] = False
        return True

    events.engagement_question_trial.mark_notification_delivered.side_effect = mark
    return events, state, original, identity


def test_notification_success_orders_output_snapshot_save_and_cache(monkeypatch, tmp_path):
    events, state, original, identity = _notification_inputs(monkeypatch, tmp_path)
    assert bot.publish_pending_engagement_question_notification(state) is True
    assert [entry[0] for entry in events.mock_calls] == [
        "engagement_question_trial.pending_treatment_notification", "copy.deepcopy",
        "engagement_question_trial.validate_notification_document", "engagement_question_trial.canonical_sha256",
        "configured_engagement_question_path", "json_file_matches", "os.lstat", "atomic_write_json",
        "json_file_matches", "copy.deepcopy", "engagement_question_trial.mark_notification_delivered",
        "save_state", "_set_engagement_question_last_notification_failure_post_id",
    ]
    validated = events.engagement_question_trial.canonical_sha256.call_args.args[0]
    assert validated == identity["document"] and validated is not identity["document"]
    assert validated["question"] is not identity["document"]["question"]
    assert all(c.args[1] is validated for c in events.json_file_matches.call_args_list)
    events.atomic_write_json.assert_called_once_with(tmp_path / "notification.json", validated, durable=True)
    assert events.atomic_write_json.call_args.args[1] is validated
    assert events.copy.deepcopy.call_args_list[1].args[0] is original
    events.save_state.assert_called_once_with(state, durable=True)
    assert events.save_state.call_args.args[0] is state
    assert state["engagement_question_experiment"] is original
    events._set_engagement_question_last_notification_failure_post_id.assert_called_once_with(None)
    assert bot._ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID is None


@pytest.mark.parametrize("branch", ["no_state", "disabled", "no_pending", "matching", "young", "threshold", "nonregular"])
def test_notification_truth_gates_and_local_output_pacing(monkeypatch, tmp_path, branch):
    events, state, original, _ = _notification_inputs(monkeypatch, tmp_path)
    if branch == "no_state":
        state["engagement_question_experiment"] = []
    elif branch == "disabled":
        monkeypatch.setattr(bot, "engagement_question_notification_output_path", "")
    elif branch == "no_pending":
        events.engagement_question_trial.pending_treatment_notification.return_value = None
    elif branch == "matching":
        events.json_file_matches.side_effect = [True, True]
    else:
        events.os.lstat.side_effect = None
        events.os.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFLNK if branch == "nonregular" else stat.S_IFREG, st_mtime=10)
        events.now_epoch.return_value = 10 + bot.ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS - (branch == "young")
    assert bot.publish_pending_engagement_question_notification(state) is (branch in {"matching", "threshold"})
    if branch in {"no_state", "disabled"}:
        assert events.mock_calls == []
    if branch in {"no_pending", "matching"}:
        events.os.lstat.assert_not_called()
    if branch != "threshold":
        events.atomic_write_json.assert_not_called()
    if branch in {"young", "nonregular"}:
        events.engagement_question_trial.mark_notification_delivered.assert_not_called()
        assert original == {"nested": {"pending": True}}
    if branch == "nonregular":
        events.now_epoch.assert_not_called()
        events.log.error.assert_called_once()


@pytest.mark.parametrize("failure", ["hash", "verification", "lstat"])
def test_notification_refusals_precede_snapshot_and_delivery(monkeypatch, tmp_path, failure):
    events, state, _, identity = _notification_inputs(monkeypatch, tmp_path)
    if failure == "hash":
        identity.update(post_id=None, document_sha256="changed")
    elif failure == "verification":
        events.json_file_matches.side_effect = [False, False]
    else:
        events.os.lstat.side_effect = PermissionError("synthetic denied output")
    assert bot.publish_pending_engagement_question_notification(state) is False
    assert events.copy.deepcopy.call_count == 1
    events.engagement_question_trial.mark_notification_delivered.assert_not_called()
    events.save_state.assert_not_called()
    events.log_event.assert_called_once_with("engagement_question_treatment_notification_write_failed", experiment_id="synthetic", post_id=None if failure == "hash" else "123")
    assert bot._ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID == ("" if failure == "hash" else "123")


@pytest.mark.parametrize("failure", ["mark_false", "save"])
def test_notification_rollback_restores_original_object_and_preserves_callback_mapping(monkeypatch, tmp_path, failure):
    events, state, original, _ = _notification_inputs(monkeypatch, tmp_path)
    nested, replacement = original["nested"], {"callback": True}

    def fail(*args, **kwargs):
        original["nested"]["pending"] = False
        state["engagement_question_experiment"] = replacement
        if failure == "save":
            raise OSError("synthetic disk full")
        return False

    target = events.save_state if failure == "save" else events.engagement_question_trial.mark_notification_delivered
    target.side_effect = fail
    assert bot.publish_pending_engagement_question_notification(state) is False
    assert original == {"nested": {"pending": True}}
    assert original["nested"] is not nested and nested == {"pending": False}
    assert state["engagement_question_experiment"] is replacement
    events.atomic_write_json.assert_called_once()
    assert [c[0] for c in events.mock_calls][-4:] == ["log.error", "_get_engagement_question_last_notification_failure_post_id", "log_event", "_set_engagement_question_last_notification_failure_post_id"]


def test_notification_failure_observes_cache_after_log_and_deduplicates_events(monkeypatch, tmp_path):
    events, state, _, _ = _notification_inputs(monkeypatch, tmp_path)
    events.engagement_question_trial.canonical_sha256.return_value = "changed"
    events.log.error.side_effect = lambda *a, **k: setattr(bot, "_ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID", "123")
    assert bot.publish_pending_engagement_question_notification(state) is False
    events.log_event.assert_not_called()
    events._set_engagement_question_last_notification_failure_post_id.assert_not_called()
    events.log.error.side_effect = None
    monkeypatch.setattr(bot, "_ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID", "other")
    for _ in range(2):
        assert bot.publish_pending_engagement_question_notification(state) is False
    assert events.log.error.call_count == 3
    events.log_event.assert_called_once()
    events._set_engagement_question_last_notification_failure_post_id.assert_called_once_with("123")


@pytest.mark.parametrize("callback", ["log.error", "_get_engagement_question_last_notification_failure_post_id", "log_event", "_set_engagement_question_last_notification_failure_post_id"])
def test_notification_error_callbacks_escape_natively(monkeypatch, tmp_path, callback):
    events, state, _, _ = _notification_inputs(monkeypatch, tmp_path)
    events.engagement_question_trial.canonical_sha256.return_value = "changed"
    target = events
    for part in callback.split("."):
        target = getattr(target, part)
    error = LookupError("synthetic callback failure")
    target.side_effect = error
    with pytest.raises(LookupError) as caught:
        bot.publish_pending_engagement_question_notification(state)
    assert caught.value is error
    assert bot._ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID == "old"


@pytest.mark.parametrize("boundary", ["state_get", "output_truth", "pending", "save"])
def test_notification_pretry_errors_and_baseexception_escape(monkeypatch, tmp_path, boundary):
    events, state, original, _ = _notification_inputs(monkeypatch, tmp_path)
    error = KeyboardInterrupt("synthetic interruption") if boundary in {"pending", "save"} else ValueError("synthetic pretry failure")

    class BrokenTruth:
        def __bool__(self):
            raise error

    if boundary == "state_get":
        state = SimpleNamespace(get=Mock(side_effect=error))
    elif boundary == "output_truth":
        monkeypatch.setattr(bot, "engagement_question_notification_output_path", BrokenTruth())
    else:
        target = events.save_state if boundary == "save" else events.engagement_question_trial.pending_treatment_notification
        target.side_effect = error
    with pytest.raises(type(error)) as caught:
        bot.publish_pending_engagement_question_notification(state)
    assert caught.value is error
    events.log.error.assert_not_called()
    events._set_engagement_question_last_notification_failure_post_id.assert_not_called()
    assert original["nested"]["pending"] is (boundary != "save")
