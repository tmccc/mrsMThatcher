from __future__ import annotations

import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'remove_managed_log_handlers': [],
 'mark_managed_log_handler': [],
 'setup_logging': ['LOG_FILE',
                   'PRODUCTION_BASE_DIR',
                   'PRODUCTION_LOG_BACKUP_COUNT',
                   'PRODUCTION_LOG_MAX_BYTES',
                   'Path',
                   'RotatingFileHandler',
                   'logging',
                   'os',
                   'path_is_same_or_child',
                   'sys'],
 'report_bot_health_progress': ['_BOT_HEALTH_REPORTER'],
 'redact_secret': [],
 'log_json_debug': ['log'],
 'state_debug_summary': [],
 'log_event': ['log'],
 '_log_descriptive_observability_failure': ['log'],
 'emit_account_root_posted': ['_log_descriptive_observability_failure',
                              'log_event'],
 'emit_historical_context_reply_posted': ['_log_descriptive_observability_failure',
                                          'log_event'],
 'emit_historical_context_history_observation': ['emit_historical_context_reply_posted'],
 'emit_historical_context_store_observation': ['_log_descriptive_observability_failure',
                                               'emit_historical_context_history_observation'],
 'print_rate_limit_headers': ['datetime', 'log'],
 '_log_validated_single_call_reply': ['log']}
SIGNATURES = {'remove_managed_log_handlers': "(logger: 'logging.Logger') -> 'None'",
 'mark_managed_log_handler': "(handler: 'logging.Handler', kind: 'str') -> "
                             "'logging.Handler'",
 'setup_logging': "(*, log_path: 'Path | None' = None, configure_file_logging: "
                  "'bool' = True) -> 'logging.Logger'",
 'report_bot_health_progress': "(phase: 'str', *, paused: 'bool | None' = "
                               "None, remote_write_blocked: 'bool | None' = "
                               "None, loop_started: 'bool' = False, "
                               "loop_completed: 'bool' = False) -> 'None'",
 'redact_secret': "(value: 'str', visible: 'int' = 4) -> 'str'",
 'log_json_debug': "(label: 'str', obj: 'object', max_chars: 'int' = 4000) -> "
                   "'None'",
 'state_debug_summary': "(state: 'object') -> 'dict[str, object]'",
 'log_event': "(event: 'str', **fields: 'object') -> 'None'",
 '_log_descriptive_observability_failure': "(message: 'str') -> 'None'",
 'emit_account_root_posted': "(*, lane: 'str', post_id: 'object', public_text: "
                             "'object' = None, quote_id: 'object' = None, "
                             "quote_text: 'object' = None, image_summary: "
                             "'object' = None, post_created_at: 'object' = "
                             "None) -> 'None'",
 'emit_historical_context_reply_posted': "(*, parent_post_id: 'object', "
                                         "reply_post_id: 'object', reply_text: "
                                         "'object', quote_id: 'object', "
                                         "reply_created_at: 'object' = None) "
                                         "-> 'None'",
 'emit_historical_context_history_observation': "(item: 'object') -> 'None'",
 'emit_historical_context_store_observation': "(store: 'object', "
                                              "parent_post_id: 'object') -> "
                                              "'None'",
 'print_rate_limit_headers': "(response: 'requests.Response') -> 'int | None'",
 '_log_validated_single_call_reply': "(*, target_description: 'str', "
                                     "target_id: 'str', reply: 'object') -> "
                                     "'None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Observability import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name != 'mrs_bot_observability':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_observability
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", [name for name, deps in DEPENDENCIES.items() if name not in {"log_event", "redact_secret", "state_debug_summary"}])
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            for dep, value in current.items():
                patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only} | current
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result

            patch.setattr(bot, "_observability", SimpleNamespace(**{name: capture}))
            for include_defaults in (True, False):
                provided = {key: object() for key, param in signature.parameters.items()
                            if include_defaults or param.default is inspect.Parameter.empty}
                bound = signature.bind(**provided)
                bound.apply_defaults()
                expected = bound.arguments
                assert adapter(**provided) is result
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(**provided, not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_observability", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_event_adapter_transfers_the_same_collected_fields_with_colliding_keys(monkeypatch):
    assert str(inspect.signature(bot.log_event)) == SIGNATURES["log_event"]
    owner_signature = inspect.signature(bot._observability.log_event)
    field = owner_signature.parameters["fields"]
    assert field.kind is inspect.Parameter.KEYWORD_ONLY
    assert field.default is inspect.Parameter.empty and field.annotation == "object"
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in owner_signature.parameters.values())
    event, result = object(), object()
    payload = {key: {"nested": []} for key in ("json", "log", "fields", "other")}
    for _ in range(2):
        current_log = object()

        def capture(value, *, fields, log):
            assert value is event
            assert fields is sys._getframe(1).f_locals["fields"]
            assert fields is not payload and list(fields) == list(payload)
            assert all(fields[key] is value for key, value in payload.items())
            assert log is current_log
            return result

        monkeypatch.setattr(bot, "log", current_log)
        monkeypatch.setattr(bot, "_observability", SimpleNamespace(log_event=capture))
        assert bot.log_event(event, **payload) is result
        with pytest.raises(TypeError, match="multiple values.*event"):
            bot.log_event(event, event="duplicate")
        with pytest.raises(TypeError, match="required positional argument"):
            bot.log_event()
        with pytest.raises(TypeError, match="positional"):
            bot.log_event(event, payload)
        failure = LookupError("owner failure")
        monkeypatch.setattr(bot, "_observability", SimpleNamespace(log_event=Mock(side_effect=failure)))
        with pytest.raises(LookupError) as caught:
            bot.log_event(event, **payload)
        assert caught.value is failure


def test_event_payload_update_serialization_fallback_and_final_log_scope(monkeypatch):
    fields = {"event": "override", "json": [], "log": {}, "fields": []}
    observed = Mock()

    def dumps(payload, **options):
        assert list(payload) == ["event", "json", "log", "fields"]
        assert all(payload[key] is value for key, value in fields.items())
        assert options == {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}
        return "serialized"

    observed.dumps.side_effect = dumps
    monkeypatch.setattr(bot._observability, "json", observed)
    bot._observability.log_event("original", fields=fields, log=observed)
    assert [c[0] for c in observed.mock_calls] == ["dumps", "info"]
    observed.info.assert_called_once_with("EVENT %s", "serialized")
    observed.reset_mock()
    observed.dumps.side_effect = ValueError("serialization")
    bot._observability.log_event("original", fields=fields, log=observed)
    observed.info.assert_called_once_with("EVENT %s", repr(fields))
    observed.info.side_effect = failure = RuntimeError("logger")
    with pytest.raises(RuntimeError) as caught:
        bot._observability.log_event("original", fields=fields, log=observed)
    assert caught.value is failure
    observed.reset_mock()
    with pytest.raises(TypeError):
        bot._observability.log_event("original", fields=42, log=observed)
    assert observed.mock_calls == []


@pytest.mark.parametrize("failure_at", [None, "remove", "close"])
def test_managed_handlers_use_owned_marker_snapshot_and_detach_before_close(monkeypatch, failure_at):
    assert bot._MANAGED_LOG_HANDLER_ATTR == bot._observability._MANAGED_LOG_HANDLER_ATTR
    monkeypatch.setattr(bot._observability, "_MANAGED_LOG_HANDLER_ATTR", "stage48_owned")
    order = []

    class Handler:
        def __setattr__(self, name, value):
            order.append((name, value))
            object.__setattr__(self, name, value)

        def close(self):
            order.append(("close", self))
            if failure_at == "close":
                raise failure

    first, second, untouched = Handler(), Handler(), SimpleNamespace()
    assert bot.mark_managed_log_handler(first, "file") is first
    assert order == [("stage48_owned", True), ("_mrs_mthatcher_handler_kind", "file")]
    bot.mark_managed_log_handler(second, "console")
    logger = SimpleNamespace(handlers=[untouched, first, second])
    failure = OSError("handler")

    def remove(handler):
        order.append(("remove", handler))
        if failure_at == "remove":
            raise failure
        logger.handlers.clear()

    logger.removeHandler = remove
    order.clear()
    if failure_at:
        with pytest.raises(OSError) as caught:
            bot.remove_managed_log_handlers(logger)
        assert caught.value is failure
        assert order == [("remove", first)] + ([("close", first)] if failure_at == "close" else [])
    else:
        bot.remove_managed_log_handlers(logger)
        assert order == [("remove", first), ("close", first), ("remove", second), ("close", second)]


def test_logging_setup_preserves_target_guard_and_console_before_file_failure(monkeypatch, tmp_path):
    target = tmp_path / "logs" / "test.log"
    observed = Mock()
    logger = observed.logger
    logging = SimpleNamespace(INFO=20, getLogger=observed.get_logger,
                              Formatter=observed.formatter, StreamHandler=observed.console)
    observed.get_logger.return_value = logger
    observed.mark.side_effect = lambda handler, kind: handler
    observed.guard.return_value = True
    monkeypatch.setattr(bot, "logging", logging)
    monkeypatch.setattr(bot, "path_is_same_or_child", observed.guard)
    monkeypatch.setattr(bot._observability, "remove_managed_log_handlers", observed.remove)
    monkeypatch.setattr(bot._observability, "mark_managed_log_handler", observed.mark)
    monkeypatch.setattr(bot, "RotatingFileHandler", observed.file)
    monkeypatch.setenv("LOG_LEVEL", "unknown-level")
    with pytest.raises(RuntimeError, match="Refusing to attach pytest"):
        bot.setup_logging(log_path=target)
    assert not target.parent.exists()
    assert [c[0] for c in observed.mock_calls] == ["guard"]
    observed.guard.return_value = False
    observed.reset_mock()
    observed.file.side_effect = failure = OSError("file constructor")
    with pytest.raises(OSError) as caught:
        bot.setup_logging(log_path=target)
    assert caught.value is failure and target.parent.is_dir()
    assert [c[0] for c in observed.mock_calls] == [
        "guard", "get_logger", "logger.setLevel", "remove", "formatter", "console", "mark",
        "console().setLevel", "console().setFormatter", "logger.addHandler", "file",
    ]
    assert logger.propagate is False
    logger.setLevel.assert_called_once_with(20)
    observed.formatter.assert_called_once_with(
        fmt="%(asctime)s %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    observed.file.assert_called_once_with(
        target, maxBytes=bot.PRODUCTION_LOG_MAX_BYTES, backupCount=bot.PRODUCTION_LOG_BACKUP_COUNT,
    )
    observed.reset_mock()
    assert bot.setup_logging(log_path=target, configure_file_logging=False) is logger
    observed.guard.assert_not_called()
    observed.file.assert_not_called()
    logger.debug.assert_called_once_with(
        "Logging initialised. LOG_LEVEL=%s LOG_FILE=%s", "UNKNOWN-LEVEL", "<disabled>",
    )
    monkeypatch.setattr(bot, "Path", Mock(side_effect=failure))
    with pytest.raises(OSError) as caught:
        bot.setup_logging(log_path=target, configure_file_logging=False)
    assert caught.value is failure


@pytest.mark.parametrize("failure", [None, RuntimeError("progress"), KeyboardInterrupt("progress")])
def test_health_progress_snapshots_current_reporter_and_only_suppresses_exception(monkeypatch, failure):
    current = Mock()
    progress = Mock(side_effect=failure)
    accesses = []

    class Reporter:
        @property
        def progress(self):
            accesses.append("progress")
            monkeypatch.setattr(bot, "_BOT_HEALTH_REPORTER", current)
            return progress

    monkeypatch.setattr(bot, "_BOT_HEALTH_REPORTER", None)
    assert bot.report_bot_health_progress("none") is None
    monkeypatch.setattr(bot, "_BOT_HEALTH_REPORTER", Reporter())
    phase, paused = object(), object()
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.report_bot_health_progress(phase, paused=paused, loop_completed=True)
        assert caught.value is failure
    else:
        assert bot.report_bot_health_progress(phase, paused=paused, loop_completed=True) is None
    assert accesses == ["progress"]
    progress.assert_called_once_with(
        phase, paused=paused, remote_write_blocked=None, loop_started=False, loop_completed=True,
    )
    current.assert_not_called()
    bot.report_bot_health_progress("next")
    current.progress.assert_called_once()


def test_pure_aliases_preserve_signatures_redaction_and_counts_only_native_errors():
    for name in ("redact_secret", "state_debug_summary"):
        assert getattr(bot, name) is getattr(bot._observability, name)
        assert str(inspect.signature(getattr(bot, name))) == SIGNATURES[name]
    assert bot.redact_secret("") == "<missing>"
    assert bot.redact_secret("12345678") == "<set-but-short>"
    assert bot.redact_secret("123456789") == "1234...6789"
    assert bot.redact_secret("abcdefg", visible=0) == "...abcdefg"
    assert bot.redact_secret("abcdefg", visible=-1) == "abcdef...bcdefg"
    with pytest.raises(TypeError):
        bot.redact_secret("abcdefg", visible="bad")
    assert bot.state_debug_summary(["private"]) == {"type": "list"}
    assert bot.state_debug_summary({"z": [object()], 2: {"private": object()}, "a": object()}) == {
        "key_count": 3, "keys": ["2", "a", "z"], "collection_counts": {"2": 1, "z": 1},
    }
    failure = ValueError("length")

    class BrokenList(list):
        def __len__(self):
            raise failure

    with pytest.raises(ValueError) as caught:
        bot.state_debug_summary({"a": BrokenList()})
    assert caught.value is failure


def test_json_diagnostics_preserve_shared_values_cycles_depth_and_truncation(monkeypatch):
    logger = Mock()
    dumps, sub = Mock(wraps=json.dumps), Mock(wraps=re.sub)
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot._observability, "json", SimpleNamespace(dumps=dumps))
    monkeypatch.setattr(bot._observability, "re", SimpleNamespace(sub=sub))
    shared = [{"safe": "é"}]
    cycle = {}
    cycle["cycle"] = cycle
    deep = "leaf"
    for _ in range(22):
        deep = [deep]
    bot.log_json_debug("label", {"left": shared, "right": shared, "cycle": cycle, "deep": deep, "API-Key": "secret"})
    cleaned = dumps.call_args.args[0]
    assert cleaned["left"] == cleaned["right"] == shared
    assert cleaned["cycle"] == {"cycle": "<circular-reference>"}
    assert cleaned["API-Key"] == "[REDACTED]"
    assert "<maximum-depth>" in repr(cleaned["deep"])
    assert dumps.call_args.kwargs == {"indent": 2, "sort_keys": True, "default": str}
    assert sub.call_count > 0
    bot.log_json_debug("short", "abcd", max_chars=-1)
    logger.debug.assert_called_with("%s: %s", "short", '"abcd...<truncated>')


@pytest.mark.parametrize("failure_at", ["serializer", "base_exception", "truncation", "logger"])
def test_json_diagnostic_error_scopes_remain_native(monkeypatch, failure_at):
    logger = Mock()
    failure = KeyboardInterrupt("serializer") if failure_at == "base_exception" else ValueError("failure")
    dumps = Mock(return_value="text")
    if failure_at in {"serializer", "base_exception"}:
        dumps.side_effect = failure
    if failure_at == "logger":
        logger.debug.side_effect = failure
    monkeypatch.setattr(bot._observability, "json", SimpleNamespace(dumps=dumps))
    monkeypatch.setattr(bot, "log", logger)
    if failure_at == "serializer":
        bot.log_json_debug("label", {})
        logger.debug.assert_called_once_with("%s: %s", "label", "<unserialisable-redacted-payload>")
    else:
        with pytest.raises(TypeError if failure_at == "truncation" else type(failure)) as caught:
            bot.log_json_debug("label", {}, max_chars=None if failure_at == "truncation" else 4000)
        if failure_at != "truncation":
            assert caught.value is failure
        if failure_at != "logger":
            logger.debug.assert_not_called()


@pytest.mark.parametrize("public,quote,summary,visible,source", [
    (" public ", " quote ", " summary ", "public", "public_text"),
    (" ", " quote ", " summary ", "quote", "image_quote_text"),
    (None, " ", " summary ", "summary", "image_summary"),
    (None, None, None, None, "unavailable"),
])
def test_account_event_preserves_visible_text_precedence_and_identity(monkeypatch, public, quote, summary, visible, source):
    event = Mock()
    monkeypatch.setattr(bot, "log_event", event)
    bot.emit_account_root_posted(lane="lane", post_id=123, public_text=public,
                                quote_text=quote, image_summary=summary, quote_id=0, post_created_at=0)
    event.assert_called_once_with(
        "account_root_posted", event_version=1, lane="lane", post_id="123", root_post_id="123",
        conversation_id="123", public_text=(public or "").strip() or None,
        visible_text=visible, visible_text_source=source, quote_id="0",
        quote_text=(quote or "").strip() or None, image_summary=(summary or "").strip() or None,
        post_created_at="0", publication_authority="confirmed_transport",
    )
    assert list(event.call_args.kwargs) == [
        "event_version", "lane", "post_id", "root_post_id", "conversation_id", "public_text",
        "visible_text", "visible_text_source", "quote_id", "quote_text", "image_summary",
        "post_created_at", "publication_authority",
    ]


def test_descriptive_failure_helper_has_no_docstring_and_keeps_narrow_catch(monkeypatch):
    assert bot._log_descriptive_observability_failure.__doc__ is None
    assert bot._observability._log_descriptive_observability_failure.__doc__ is None
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    logger.error.side_effect = RuntimeError("descriptive")
    assert bot._log_descriptive_observability_failure("message") is None
    logger.error.assert_called_once_with("message", exc_info=True)
    logger.error.side_effect = failure = KeyboardInterrupt("descriptive")
    with pytest.raises(KeyboardInterrupt) as caught:
        bot._log_descriptive_observability_failure("message")
    assert caught.value is failure


@pytest.mark.parametrize("name,arguments", [
    ("emit_account_root_posted", {"lane": "lane", "post_id": 123}),
    ("emit_historical_context_reply_posted", {"parent_post_id": 123, "reply_post_id": 456,
                                             "reply_text": None, "quote_id": 0}),
])
def test_descriptive_events_use_current_failure_callback_outside_original_catch(monkeypatch, name, arguments):
    event, failed = Mock(side_effect=ValueError("event")), Mock()
    monkeypatch.setattr(bot, "log_event", event)
    monkeypatch.setattr(bot, "_log_descriptive_observability_failure", failed)
    assert getattr(bot, name)(**arguments) is None
    failed.assert_called_once()
    failed.side_effect = failure = RuntimeError("failure callback")
    with pytest.raises(RuntimeError) as caught:
        getattr(bot, name)(**arguments)
    assert caught.value is failure
    failed.reset_mock()
    event.side_effect = failure = KeyboardInterrupt("event")
    with pytest.raises(KeyboardInterrupt) as caught:
        getattr(bot, name)(**arguments)
    assert caught.value is failure
    failed.assert_not_called()


def test_history_observation_preserves_dict_completed_non_none_gates_and_references(monkeypatch):
    emit = Mock()
    monkeypatch.setattr(bot, "emit_historical_context_reply_posted", emit)
    fields = {"parent_post_id": 0, "reply_post_id": False, "reply_text": [], "quote_id": ""}
    for invalid in (None, [], {"status": "completed"}, {"status": "pending", **fields},
                    {"status": "completed", **fields, "quote_id": None}):
        bot.emit_historical_context_history_observation(invalid)
    emit.assert_not_called()
    item = {"status": "completed", **fields}
    bot.emit_historical_context_history_observation(item)
    assert list(emit.call_args.kwargs) == list(fields)
    assert all(emit.call_args.kwargs[key] is value for key, value in fields.items())
    emit.side_effect = failure = ValueError("emit")
    with pytest.raises(ValueError) as caught:
        bot.emit_historical_context_history_observation(item)
    assert caught.value is failure


def test_store_observation_preserves_lookup_reference_and_failure_boundary(monkeypatch):
    item = {"status": "completed"}
    store = SimpleNamespace(history=Mock(return_value={"items": {"123": item}}))
    emit, failed = Mock(), Mock()
    monkeypatch.setattr(bot, "emit_historical_context_history_observation", emit)
    monkeypatch.setattr(bot, "_log_descriptive_observability_failure", failed)
    bot.emit_historical_context_store_observation(store, 123)
    emit.assert_called_once_with(item)
    assert emit.call_args.args[0] is item
    for invalid in ([], {"items": []}, {"items": {}}):
        store.history.return_value = invalid
        bot.emit_historical_context_store_observation(store, 123)
        emit.assert_called_with(None)
    emit.side_effect = ValueError("observation")
    bot.emit_historical_context_store_observation(store, 123)
    failed.assert_called_once_with("Could not emit recovered historical-context observability")
    failed.side_effect = failure = RuntimeError("failure callback")
    with pytest.raises(RuntimeError) as caught:
        bot.emit_historical_context_store_observation(store, 123)
    assert caught.value is failure


@pytest.mark.parametrize("reset", [None, "bad", "123"])
def test_rate_headers_preserve_access_log_conversion_and_datetime_order(monkeypatch, reset):
    observed = Mock()
    observed.headers.get.side_effect = ["limit", "remaining", reset]
    observed.datetime.fromtimestamp.return_value.strftime.return_value = "formatted"
    monkeypatch.setattr(bot, "log", observed.log)
    monkeypatch.setattr(bot, "datetime", observed.datetime)
    assert bot.print_rate_limit_headers(SimpleNamespace(headers=observed.headers)) == (123 if reset == "123" else None)
    assert observed.mock_calls[:5] == [
        call.headers.get("x-rate-limit-limit"), call.log.warning("Rate Limit: %s", "limit"),
        call.headers.get("x-rate-limit-remaining"), call.log.warning("Remaining: %s", "remaining"),
        call.headers.get("x-rate-limit-reset"),
    ]
    if reset == "123":
        assert observed.mock_calls[5:] == [
            call.datetime.fromtimestamp(123), call.datetime.fromtimestamp().strftime("%Y-%m-%d %H:%M:%S"),
            call.log.warning("Rate Limit Resets At: %s", "formatted"),
        ]
    else:
        assert observed.mock_calls[5:] == ([call.log.warning("Rate Limit Resets At: %s", "bad")] if reset else [])


def test_rate_header_datetime_log_catch_and_fallback_log_error_remain_native(monkeypatch):
    logger, date = Mock(), Mock()
    failure = ValueError("logger")
    logger.warning.side_effect = [None, None, failure, failure]
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "datetime", date)
    with pytest.raises(ValueError) as caught:
        bot.print_rate_limit_headers(SimpleNamespace(headers={"x-rate-limit-reset": "123"}))
    assert caught.value is failure
    assert logger.warning.call_args == call("Rate Limit Resets At invalid epoch: %s", "123")


def test_validated_reply_uses_owned_hash_strict_utf8_counts_and_native_errors(monkeypatch):
    logger, sha = Mock(), Mock()
    sha.return_value.hexdigest.return_value = "digest"
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot._observability, "hashlib", SimpleNamespace(sha256=sha))
    bot._log_validated_single_call_reply(target_description="target", target_id=123, reply="é🙂")
    sha.assert_called_once_with("é🙂".encode("utf-8"))
    logger.info.assert_called_once_with(
        "Generated validated reply to %s %s character_count=%d utf8_byte_count=%d sha256=%s",
        "target", "123", 2, 6, "digest",
    )
    sha.reset_mock()
    logger.reset_mock()
    with pytest.raises(UnicodeEncodeError):
        bot._log_validated_single_call_reply(target_description="target", target_id=123, reply="\ud800")
    sha.assert_not_called()
    logger.info.assert_not_called()
    sha.side_effect = failure = ValueError("hash")
    with pytest.raises(ValueError) as caught:
        bot._log_validated_single_call_reply(target_description="target", target_id=123, reply="text")
    assert caught.value is failure
    logger.info.assert_not_called()
