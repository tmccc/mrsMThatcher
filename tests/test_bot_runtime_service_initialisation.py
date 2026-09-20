"""Focused contracts for lazy runtime-service initialisation and live root state."""
from __future__ import annotations

import builtins
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_runtime_service_initialisation as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'initialise_bot_health_reporting': ['BASE_DIR',
                                     'BotHealthReporter',
                                     'HealthLoggingObserver',
                                     'INITIALISE_REQUESTED',
                                     'SELF_TEST_REQUESTED',
                                     'TEST_MODE',
                                     '_get_bot_health_reporter',
                                     '_get_bot_logger',
                                     '_set_bot_health_logging_observer',
                                     '_set_bot_health_reporter',
                                     'health_file_path_from_environment'],
 'reply_evidence_repository': ['BASE_DIR',
                               'Path',
                               'ReplyEvidenceUnavailable',
                               'SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH',
                               '_get_bot_logger',
                               '_get_reply_evidence_load_error',
                               '_get_reply_evidence_repository_cache',
                               '_set_reply_evidence_load_error',
                               '_set_reply_evidence_repository_cache'],
 'initialise_historical_context_semantic_gate': ['BASE_DIR',
                                                 '_get_bot_logger',
                                                 '_get_historical_context_semantic_gate',
                                                 '_set_historical_context_semantic_gate',
                                                 'historical_context_reply',
                                                 'log_event']}
SHARED = {'_BOT_HEALTH_REPORTER': {'getter': '_get_bot_health_reporter',
                          'setter': '_set_bot_health_reporter'},
 '_BOT_HEALTH_LOGGING_OBSERVER': {'setter': '_set_bot_health_logging_observer'},
 '_REPLY_EVIDENCE_REPOSITORY': {'getter': '_get_reply_evidence_repository_cache',
                                'setter': '_set_reply_evidence_repository_cache'},
 '_REPLY_EVIDENCE_LOAD_ERROR': {'getter': '_get_reply_evidence_load_error',
                                'setter': '_set_reply_evidence_load_error'},
 '_HISTORICAL_CONTEXT_SEMANTIC_GATE': {'getter': '_get_historical_context_semantic_gate',
                                       'setter': '_set_historical_context_semantic_gate'},
 'log': {'getter': '_get_bot_logger'}}
PUBLIC = {name: getattr(bot, name) for name in DEPENDENCIES}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Runtime-service-initialisation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'engagement_question_experiment', 'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt', 'reply_evidence', 'historical_context_reply_semantic_gate', 'bot_health'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_runtime_service_initialisation', 'mrs_bot_historical_context_delivery'}:
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
import mrs_bot_runtime_service_initialisation
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_runtime_service_initialisation.initialise_bot_health_reporting.__annotations__['return'] == 'None'
assert mrs_bot_runtime_service_initialisation.initialise_historical_context_semantic_gate.__annotations__['packets'] == 'dict[str, dict]'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.fixture(autouse=True)
def isolated_runtime_services(monkeypatch, isolate_bot_runtime):
    # Keep the receipt/incident fixture, restoring its intentional evidence stub.
    monkeypatch.setattr(bot, "reply_evidence_repository", PUBLIC["reply_evidence_repository"])
    for name in SHARED:
        monkeypatch.setattr(bot, name, Mock() if name == "log" else None)
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    monkeypatch.setattr(bot, "INITIALISE_REQUESTED", False)
    monkeypatch.setattr(bot, "TEST_MODE", True)


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_and_current_adapter_references(monkeypatch, name):
    signatures = {
        "initialise_bot_health_reporting": "() -> 'None'",
        "reply_evidence_repository": "()",
        "initialise_historical_context_semantic_gate": "(packets: 'dict[str, dict]') -> 'object'",
    }
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert bot._runtime_service_initialisation is owner
    assert str(inspect.signature(public)) == signatures[name]
    assert public.__doc__ == extracted.__doc__
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    args = (object(),) if name == "initialise_historical_context_semantic_gate" else ()
    for _round in range(2):
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        assert public(*args) is result
        assert callback.call_args.args == args
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == dependencies
        assert all(callback.call_args.kwargs[key] is value for key, value in dependencies.items())


@pytest.mark.parametrize("name", list(SHARED))
def test_accessors_keep_live_root_storage_and_exact_references(monkeypatch, name):
    for value in (object(), None, False):
        monkeypatch.setattr(bot, name, value)
        if "getter" in SHARED[name]:
            assert getattr(bot, SHARED[name]["getter"])() is value
        if "setter" in SHARED[name]:
            replacement = object()
            assert getattr(bot, SHARED[name]["setter"])(replacement) is None
            assert getattr(bot, name) is replacement
            if "getter" in SHARED[name]:
                assert getattr(bot, SHARED[name]["getter"])() is replacement


def _health(monkeypatch, tmp_path):
    trace = Mock()
    path, reporter, observer = tmp_path / "synthetic-health.json", object(), object()
    trace.path.return_value = path
    trace.reporter.return_value = reporter
    trace.observer.return_value = observer
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "health_file_path_from_environment", trace.path)
    monkeypatch.setattr(bot, "BotHealthReporter", trace.reporter)
    monkeypatch.setattr(bot, "HealthLoggingObserver", trace.observer)
    monkeypatch.setattr(bot, "log", trace.log)
    trace.set_reporter.side_effect = bot._set_bot_health_reporter
    trace.set_observer.side_effect = bot._set_bot_health_logging_observer
    monkeypatch.setattr(bot, "_set_bot_health_reporter", trace.set_reporter)
    monkeypatch.setattr(bot, "_set_bot_health_logging_observer", trace.set_observer)
    return SimpleNamespace(trace=trace, path=path, reporter=reporter, observer=observer)


@pytest.mark.parametrize("gate", ["cached", "self_test", "initialise", "no_path"])
def test_health_lazy_gates_do_not_construct_or_write(monkeypatch, tmp_path, gate):
    h = _health(monkeypatch, tmp_path)
    forbidden_truth = Mock()
    forbidden_truth.__bool__ = Mock(side_effect=AssertionError("late flag read"))
    if gate == "cached":
        monkeypatch.setattr(bot, "_BOT_HEALTH_REPORTER", False)
        monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", forbidden_truth)
    elif gate == "self_test":
        monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", True)
        monkeypatch.setattr(bot, "INITIALISE_REQUESTED", forbidden_truth)
    elif gate == "initialise":
        monkeypatch.setattr(bot, "INITIALISE_REQUESTED", True)
    else:
        h.trace.path.return_value = None
    assert bot.initialise_bot_health_reporting() is None
    assert h.trace.mock_calls == ([call.path(test_mode=True, test_base_dir=tmp_path)] if gate == "no_path" else [])
    assert not h.path.exists()


def test_health_assigns_before_handler_and_delayed_warning_uses_replaced_root_logger(monkeypatch, tmp_path):
    h = _health(monkeypatch, tmp_path)
    after_constructor, after_observer, delayed = Mock(), Mock(), Mock()

    def construct(path, *, write_failure_callback):
        assert path is h.path
        assert bot._BOT_HEALTH_REPORTER is None
        monkeypatch.setattr(bot, "log", after_constructor)
        return h.reporter

    def observe(reporter):
        assert reporter is h.reporter
        assert bot._BOT_HEALTH_REPORTER is None
        monkeypatch.setattr(bot, "log", after_observer)
        return h.observer

    def add_handler(observer):
        assert observer is h.observer
        assert bot._BOT_HEALTH_REPORTER is h.reporter
        assert bot._BOT_HEALTH_LOGGING_OBSERVER is h.observer

    h.trace.reporter.side_effect = construct
    h.trace.observer.side_effect = observe
    after_observer.addHandler.side_effect = add_handler
    assert bot.initialise_bot_health_reporting() is None
    callback = h.trace.reporter.call_args.kwargs["write_failure_callback"]
    assert h.trace.mock_calls == [
        call.path(test_mode=True, test_base_dir=tmp_path),
        call.reporter(h.path, write_failure_callback=callback),
        call.observer(h.reporter), call.set_reporter(h.reporter), call.set_observer(h.observer),
    ]
    after_observer.addHandler.assert_called_once_with(h.observer)
    monkeypatch.setattr(bot, "log", delayed)
    message = object()
    callback(message)
    delayed.warning.assert_called_once_with("%s", message)
    after_constructor.assert_not_called()
    after_constructor.warning.assert_not_called()
    after_observer.warning.assert_not_called()
    assert not h.path.exists()


@pytest.mark.parametrize("boundary", ["path", "reporter", "observer", "handler"])
@pytest.mark.parametrize("test_mode,error_type", [(False, ValueError), (True, ValueError), (False, KeyboardInterrupt)])
def test_health_native_failure_scope_and_partial_assignments(monkeypatch, tmp_path, boundary, test_mode, error_type):
    h = _health(monkeypatch, tmp_path)
    error = error_type("health boundary")
    monkeypatch.setattr(bot, "TEST_MODE", test_mode)
    callback = h.trace.log.addHandler if boundary == "handler" else getattr(h.trace, boundary)
    callback.side_effect = error
    if test_mode or not isinstance(error, Exception):
        with pytest.raises(error_type) as caught:
            bot.initialise_bot_health_reporting()
        assert caught.value is error
        h.trace.log.warning.assert_not_called()
    else:
        assert bot.initialise_bot_health_reporting() is None
        h.trace.log.warning.assert_called_once_with(
            "Bot health telemetry could not be initialised; bot operation continues", exc_info=True,
        )
    h.trace.path.assert_called_once_with(test_mode=test_mode, test_base_dir=tmp_path if test_mode else None)
    assert bot._BOT_HEALTH_REPORTER is (h.reporter if boundary == "handler" else None)
    assert bot._BOT_HEALTH_LOGGING_OBSERVER is (h.observer if boundary == "handler" else None)
    assert not h.path.exists()


@pytest.mark.parametrize("initial_mode", [False, True])
def test_health_rereads_test_mode_and_diagnostic_errors_escape(monkeypatch, tmp_path, initial_mode):
    h = _health(monkeypatch, tmp_path)
    failure, diagnostic = ValueError("path"), RuntimeError("warning")
    class Mode:
        value = initial_mode

        def __bool__(self):
            return self.value

    mode = Mode()
    monkeypatch.setattr(bot, "TEST_MODE", mode)

    def fail(**kwargs):
        assert kwargs["test_mode"] is mode
        mode.value = not initial_mode
        raise failure

    h.trace.path.side_effect = fail
    h.trace.log.warning.side_effect = diagnostic
    with pytest.raises(type(diagnostic if initial_mode else failure)) as caught:
        bot.initialise_bot_health_reporting()
    assert caught.value is (diagnostic if initial_mode else failure)
    assert h.trace.log.warning.call_count == int(initial_mode)


def _imports(monkeypatch, trace, modules):
    original = builtins.__import__

    def current_import(name, *args, **kwargs):
        if name in modules:
            trace.append(("import", name))
            return modules[name]
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", current_import)


@pytest.mark.parametrize("service", ["repository", "error", "gate"])
def test_cache_gates_make_separate_current_observations_before_imports(monkeypatch, service):
    trace, result = [], object()
    _imports(monkeypatch, trace, {name: None for name in (
        "reply_evidence", "historical_context_formatter", "historical_context_reply_semantic_gate",
    )})
    if service == "gate":
        getter = Mock(side_effect=[False, result])
        monkeypatch.setattr(bot, "_get_historical_context_semantic_gate", getter)
        assert bot.initialise_historical_context_semantic_gate(object()) is result
    elif service == "repository":
        getter = Mock(side_effect=[False, result])
        monkeypatch.setattr(bot, "_get_reply_evidence_repository_cache", getter)
        assert bot.reply_evidence_repository() is result
    else:
        getter = Mock(side_effect=["first reason", "current reason"])
        monkeypatch.setattr(bot, "_get_reply_evidence_load_error", getter)
        with pytest.raises(bot.ReplyEvidenceUnavailable, match="^current reason$"):
            bot.reply_evidence_repository()
    assert getter.call_count == 2
    assert trace == []


@pytest.mark.parametrize("absolute", [False, True])
def test_evidence_path_order_and_live_success_properties(monkeypatch, absolute):
    trace, input_path, joined, factual, final = [], object(), object(), object(), object()
    fields = ["completed_packet_count", "unresolved_packet_count", "attribution_eligible_packet_count", "factual_evidence_count", "passages"]

    class ResearchPath:
        def is_absolute(self):
            trace.append("absolute")
            return absolute

    research = ResearchPath()

    class Base:
        def __truediv__(self, value):
            trace.append(("join", value))
            return joined if value is research else factual

    class Repository:
        def __init__(self, index):
            self.index = index

        def __getattr__(self, name):
            assert name == fields[self.index]
            trace.append(name)
            monkeypatch.setattr(bot, "_REPLY_EVIDENCE_REPOSITORY", Repository(self.index + 1))
            monkeypatch.setattr(bot, "log", Mock())
            return [0] * 5 if name == "passages" else self.index + 1

    class Logger:
        @property
        def info(self):
            trace.append("info lookup")
            return emit

    def emit(message, *args):
        trace.append("info")
        assert args == (1, 2, 3, 4, 5)
        monkeypatch.setattr(bot, "_REPLY_EVIDENCE_REPOSITORY", final)

    def path(value):
        assert value is input_path
        trace.append("path")
        return research

    def construct(value, *, factual_evidence_path):
        trace.append("construct")
        assert value is (research if absolute else joined)
        assert factual_evidence_path is factual
        assert bot._REPLY_EVIDENCE_REPOSITORY is None
        monkeypatch.setattr(bot, "log", Logger())
        return Repository(0)

    _imports(monkeypatch, trace, {"reply_evidence": SimpleNamespace(EvidenceRepository=construct)})
    monkeypatch.setattr(bot, "SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH", input_path)
    monkeypatch.setattr(bot, "Path", path)
    monkeypatch.setattr(bot, "BASE_DIR", Base())
    assert bot.reply_evidence_repository() is final
    assert trace == [("import", "reply_evidence"), "path", "absolute"] + (
        [] if absolute else [("join", research)]
    ) + [("join", "reply_factual_evidence.json"), "construct", "info lookup", *fields, "info"]
    assert bot._REPLY_EVIDENCE_LOAD_ERROR is None


def test_evidence_failure_interpolates_before_caching_and_rereads_after_log(monkeypatch, tmp_path):
    trace = []

    class LoadError(OSError):
        def __str__(self):
            assert bot._REPLY_EVIDENCE_LOAD_ERROR is None
            trace.append("string")
            return "missing synthetic corpus"

    failure = LoadError()

    def critical(message, reason):
        trace.append("critical")
        assert reason == f"reply evidence unavailable at {tmp_path / 'synthetic'}: LoadError: missing synthetic corpus"
        assert bot._REPLY_EVIDENCE_LOAD_ERROR == reason
        monkeypatch.setattr(bot, "_REPLY_EVIDENCE_LOAD_ERROR", "updated by critical")

    factory = Mock(side_effect=failure)
    _imports(monkeypatch, trace, {"reply_evidence": SimpleNamespace(EvidenceRepository=factory)})
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH", "synthetic")
    bot.log.critical.side_effect = critical
    with pytest.raises(bot.ReplyEvidenceUnavailable, match="^updated by critical$") as caught:
        bot.reply_evidence_repository()
    assert caught.value.__cause__ is failure
    assert trace == [("import", "reply_evidence"), "string", "critical"]
    assert bot._REPLY_EVIDENCE_REPOSITORY is None
    factory.assert_called_once_with(tmp_path / "synthetic", factual_evidence_path=tmp_path / "reply_factual_evidence.json")


@pytest.mark.parametrize("boundary", ["import", "path", "absolute", "join", "factual", "constructor", "property", "log"])
def test_evidence_native_failures_keep_constructor_only_catch(monkeypatch, tmp_path, boundary):
    error = KeyboardInterrupt("constructor") if boundary == "constructor" else ValueError(boundary)
    trace, repository = [], SimpleNamespace(completed_packet_count=1)

    def fail():
        raise error

    class ResearchPath:
        def is_absolute(self):
            return fail() if boundary == "absolute" else False

    research = ResearchPath()

    class Base:
        def __truediv__(self, value):
            if boundary == ("join" if value is research else "factual"):
                fail()
            return tmp_path / "synthetic"

    class Repository:
        @property
        def completed_packet_count(self):
            return fail()

    if boundary == "property":
        repository = Repository()
    elif boundary == "log":
        repository = SimpleNamespace(completed_packet_count=1, unresolved_packet_count=2, attribution_eligible_packet_count=3, factual_evidence_count=4, passages=[])
        bot.log.info.side_effect = error
    factory = Mock(side_effect=error if boundary == "constructor" else None, return_value=repository)
    module = SimpleNamespace(EvidenceRepository=factory)
    if boundary == "import":
        class MissingModule:
            @property
            def EvidenceRepository(self):
                return fail()
        module = MissingModule()
    _imports(monkeypatch, trace, {"reply_evidence": module})
    monkeypatch.setattr(bot, "Path", lambda _value: fail() if boundary == "path" else research)
    monkeypatch.setattr(bot, "BASE_DIR", Base())
    with pytest.raises(type(error)) as caught:
        bot.reply_evidence_repository()
    assert caught.value is error
    assert bot._REPLY_EVIDENCE_LOAD_ERROR is None
    assert bot._REPLY_EVIDENCE_REPOSITORY is (repository if boundary in {"property", "log"} else None)
    bot.log.critical.assert_not_called()


@pytest.mark.parametrize("boundary", ["string", "critical", "exception"])
def test_evidence_failure_diagnostic_errors_escape_natively(monkeypatch, tmp_path, boundary):
    error = RuntimeError(boundary)

    class LoadError(OSError):
        def __str__(self):
            if boundary == "string":
                raise error
            return "synthetic failure"

    failure = LoadError()
    _imports(monkeypatch, [], {"reply_evidence": SimpleNamespace(EvidenceRepository=Mock(side_effect=failure))})
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    if boundary == "critical":
        bot.log.critical.side_effect = error
    elif boundary == "exception":
        monkeypatch.setattr(bot, "ReplyEvidenceUnavailable", Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        bot.reply_evidence_repository()
    assert caught.value is error
    assert (bot._REPLY_EVIDENCE_LOAD_ERROR is None) == (boundary == "string")
    assert bot._REPLY_EVIDENCE_REPOSITORY is None


def _semantic_gate(monkeypatch, available=True, failure_at=None, error=None):
    trace, logged, events, counts = [], [], [], {}
    packets = {"first": {"eligible": True}, "second": {"eligible": False}}

    def record(name):
        trace.append(name)
        if name == failure_at:
            raise error

    class Option:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def __int__(self):
            record(self.name)
            return self.value

        def __bool__(self):
            record(self.name)
            return self.value

    class Gate:
        @property
        def available(self):
            record("available")
            assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE is self
            monkeypatch.setattr(bot, "log", Logger())
            return Option("truth", available)

        def __getattr__(self, name):
            record(name)
            count = counts.get(name, 0)
            counts[name] = count + 1
            return {"a": 1} if name == "blocked_dispositions" else f"{name}-{count}"

    gate = Gate()

    class Logger:
        def __getattr__(self, name):
            record(f"{name} lookup")

            def emit(*args):
                record(name)
                logged.append((name, args))
                # The return remains the loaded gate even if logging replaces cache.
                monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", "after log")
            return emit

    def attributed(packet):
        name = next(key for key, value in packets.items() if value is packet)
        record(name)
        return packet["eligible"]

    def load(**kwargs):
        record("load")
        assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE is None
        assert kwargs["root"] is root
        assert kwargs["eligible_quote_ids"] == {"first"}
        assert list(kwargs) == ["root", "eligible_quote_ids", "formatter_options"]
        assert list(kwargs["formatter_options"]) == ["maximum_length", "include_meaning", "include_source", "include_verification"]
        assert kwargs["formatter_options"] == {"maximum_length": 321, "include_meaning": False, "include_source": True, "include_verification": False}
        return gate

    def event(name, **fields):
        record("event")
        events.append((name, fields))

    modules = {
        "historical_context_formatter": SimpleNamespace(packet_is_attributed_to_margaret_thatcher=attributed),
        "historical_context_reply_semantic_gate": SimpleNamespace(POLICY_VERSION="synthetic-policy", load_historical_context_semantic_gate=load),
    }
    _imports(monkeypatch, trace, modules)
    root = object()
    monkeypatch.setattr(bot, "BASE_DIR", root)
    monkeypatch.setattr(bot, "log_event", event)
    monkeypatch.setattr(bot, "historical_context_reply", {
        "maximum_length": Option("maximum_length", 321),
        "include_meaning": Option("include_meaning", False),
        "include_source": Option("include_source", True),
        "include_verification": Option("include_verification", False),
    })
    return SimpleNamespace(trace=trace, packets=packets, gate=gate, logged=logged, events=events)


GATE_PREFIX = [
    ("import", "historical_context_formatter"),
    ("import", "historical_context_reply_semantic_gate"),
    "first", "second", "maximum_length", "include_meaning", "include_source", "include_verification",
    "load", "available", "truth",
]


@pytest.mark.parametrize("available", [False, True])
def test_gate_conversion_store_property_log_event_order_and_return_reference(monkeypatch, available):
    g = _semantic_gate(monkeypatch, available)
    assert bot.initialise_historical_context_semantic_gate(g.packets) is g.gate
    if available:
        assert g.trace == GATE_PREFIX + [
            "info lookup", "ledger_sha256", "projection_sha256", "blocked_dispositions", "info",
            "ledger_sha256", "projection_sha256", "blocked_dispositions", "event",
        ]
        assert g.logged[0][0] == "info"
        assert g.logged[0][1][1:] == ("synthetic-policy", "ledger_sha256-0", "projection_sha256-0", 1)
        expected = dict(status="loaded", policy_version="synthetic-policy", ledger_sha256="ledger_sha256-1", projection_sha256="projection_sha256-1", blocked_quote_count=1)
    else:
        assert g.trace == GATE_PREFIX + ["critical lookup", "reason", "critical", "ledger_sha256", "reason", "event"]
        assert g.logged[0][0] == "critical"
        assert g.logged[0][1][1:] == ("reason-0",)
        expected = dict(status="unavailable", policy_version="synthetic-policy", ledger_sha256="ledger_sha256-0", reason="reason-1")
    assert g.events == [("historical_context_semantic_gate", expected)]
    assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE == "after log"


@pytest.mark.parametrize("boundary", ["first", "maximum_length", "include_meaning", "load", "available", "truth", "ledger_sha256", "info", "event"])
def test_gate_native_errors_preserve_exact_progress_and_stored_cache(monkeypatch, boundary):
    error = ValueError(boundary)
    g = _semantic_gate(monkeypatch, failure_at=boundary, error=error)
    with pytest.raises(ValueError) as caught:
        bot.initialise_historical_context_semantic_gate(g.packets)
    assert caught.value is error
    order = GATE_PREFIX + [
        "info lookup", "ledger_sha256", "projection_sha256", "blocked_dispositions", "info",
        "ledger_sha256", "projection_sha256", "blocked_dispositions", "event",
    ]
    assert g.trace == order[:order.index(boundary) + 1]
    expected = None if boundary in {"first", "maximum_length", "include_meaning", "load"} else g.gate
    assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE is expected if boundary != "event" else bot._HISTORICAL_CONTEXT_SEMANTIC_GATE == "after log"
    assert g.events == []


@pytest.mark.parametrize("module", ["historical_context_formatter", "historical_context_reply_semantic_gate"])
def test_gate_import_failures_precede_packet_or_cache_work(monkeypatch, module):
    trace, error = [], ImportError("synthetic gate import")
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name in {"historical_context_formatter", "historical_context_reply_semantic_gate"}:
            trace.append(name)
            if name == module:
                raise error
            return SimpleNamespace(packet_is_attributed_to_margaret_thatcher=Mock())
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(ImportError) as caught:
        bot.initialise_historical_context_semantic_gate(object())
    assert caught.value is error
    assert trace == ["historical_context_formatter"] + ([module] if module != "historical_context_formatter" else [])
    assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE is None


def test_gate_baseexception_after_store_is_not_caught(monkeypatch):
    error = KeyboardInterrupt("available")
    g = _semantic_gate(monkeypatch, failure_at="available", error=error)
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.initialise_historical_context_semantic_gate(g.packets)
    assert caught.value is error
    assert bot._HISTORICAL_CONTEXT_SEMANTIC_GATE is g.gate
    assert g.trace == GATE_PREFIX[:-1]
