from __future__ import annotations

import inspect
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


DEPENDENCIES = {'run_test_cycle': ['AmbiguousRemotePostOutcome',
                    'BASE_DIR',
                    'LOG_FILE',
                    'OPENAI_BASE',
                    'STATE_FILE',
                    'UnrecoverableConfirmedReplyPersistenceError',
                    'X_BASE',
                    'X_UPLOAD_BASE',
                    'acquire_instance_lock',
                    'ambiguous_remote_post_is_blocking',
                    'block_if_ambiguous_remote_post',
                    'load_runtime_state',
                    'log',
                    'log_event',
                    'maybe_reply_to_mentions',
                    'maybe_reply_to_quote_tweets',
                    'reconcile_runtime_historical_context_state',
                    'require_established_installation_after_ledger_recovery',
                    'require_production_bootstrap',
                    'require_test_mode',
                    'save_state',
                    'wait_for_durable_barrier_before_one_shot_exit'],
 'run_test_main_tick': ['acquire_instance_lock',
                        'ambiguous_remote_post_is_blocking',
                        'block_if_ambiguous_remote_post',
                        'load_runtime_state',
                        'log',
                        'now_epoch',
                        'reconcile_runtime_historical_context_state',
                        'report_bot_health_progress',
                        'require_established_installation_after_ledger_recovery',
                        'require_production_bootstrap',
                        'require_test_mode',
                        'run_reply_lane_checks_for_tick',
                        'save_state',
                        'scheduler_epoch_from_state',
                        'wait_for_durable_barrier_before_one_shot_exit'],
 'require_test_mode': ['IMPORT_TIME_TEST_MODE', 'log'],
 'wait_for_durable_barrier_before_one_shot_exit': ['durable_remote_write_safety_barrier_exists',
                                                   'log',
                                                   'remote_write_safety_incident_is_latched',
                                                   'sleep'],
 'run_test_post_quote': ['AmbiguousRemotePostOutcome',
                         'ApiError',
                         'ConfirmedPostLocalPersistenceError',
                         'LINES_FILE',
                         'UnrecoverableConfirmedPostPersistenceError',
                         'acquire_instance_lock',
                         'block_if_ambiguous_remote_post',
                         'current_image_paths',
                         'lane_paused',
                         'load_image_used_basenames',
                         'load_quote_used_hashes',
                         'load_runtime_state',
                         'log',
                         'post_random_quote',
                         'prepare_test_main_post_state',
                         'reconcile_runtime_historical_context_state',
                         'record_api_error',
                         'require_established_installation_after_ledger_recovery',
                         'require_production_bootstrap',
                         'require_test_mode',
                         'save_state',
                         'wait_for_durable_barrier_before_one_shot_exit'],
 'run_test_post_meme': ['AmbiguousRemotePostOutcome',
                        'ApiError',
                        'ConfirmedPostLocalPersistenceError',
                        'UnrecoverableConfirmedPostPersistenceError',
                        'acquire_instance_lock',
                        'block_if_ambiguous_remote_post',
                        'lane_paused',
                        'load_runtime_state',
                        'log',
                        'post_next_meme',
                        'prepare_test_main_post_state',
                        'reconcile_runtime_historical_context_state',
                        'record_api_error',
                        'require_established_installation_after_ledger_recovery',
                        'require_production_bootstrap',
                        'require_test_mode',
                        'save_state',
                        'wait_for_durable_barrier_before_one_shot_exit'],
 'run_cli': ['CLI_USAGE',
             'CliUsageError',
             'IMPORT_TIME_CLI_ARGUMENTS',
             'IMPORT_TIME_TEST_MODE',
             'TEST_MODE_REQUIRED_CLI_FLAGS',
             'initialise_installation',
             'main',
             'parse_cli_mode',
             'production_bootstrap',
             'run_self_test',
             'run_test_cycle',
             'run_test_main_tick',
             'run_test_post_meme',
             'run_test_post_quote',
             'sys']}

SIGNATURES = {'run_test_cycle': "() -> 'int'",
 'run_test_main_tick': "() -> 'int'",
 'require_test_mode': "(command_name: 'str') -> 'bool'",
 'wait_for_durable_barrier_before_one_shot_exit': "(*, lane: 'str') -> 'None'",
 'run_test_post_quote': "() -> 'int'",
 'run_test_post_meme': "() -> 'int'",
 'run_cli': "(argv: 'list[str] | tuple[str, ...] | None' = None) -> 'int | None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('CLI execution import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_cli_execution':
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
import mrs_bot_cli_execution
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_cli_execution.run_cli.__annotations__['argv'] == 'list[str] | tuple[str, ...] | None'
assert mrs_bot_cli_execution.wait_for_durable_barrier_before_one_shot_exit.__annotations__['return'] == 'None'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", DEPENDENCIES)
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

            patch.setattr(bot, "_cli_execution", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_cli_execution", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


ENTRY_FLAGS = {
    "run_test_cycle": "--test-cycle",
    "run_test_main_tick": "--test-main-tick",
    "run_test_post_quote": "--test-post-quote",
    "run_test_post_meme": "--test-post-meme",
}


def _execution_trace(monkeypatch, state):
    trace = Mock()
    for name in (
        "require_test_mode", "require_production_bootstrap", "acquire_instance_lock",
        "require_established_installation_after_ledger_recovery",
        "reconcile_runtime_historical_context_state", "block_if_ambiguous_remote_post",
        "load_runtime_state", "save_state", "log",
        "wait_for_durable_barrier_before_one_shot_exit",
    ):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.require_test_mode.return_value = True
    trace.load_runtime_state.return_value = state
    return trace


def _preamble(flag, *, quote=False):
    return [
        call.require_test_mode(flag), call.require_production_bootstrap(),
        call.acquire_instance_lock(),
        call.require_established_installation_after_ledger_recovery(),
        call.reconcile_runtime_historical_context_state(),
        (call.block_if_ambiguous_remote_post(allow_confirmed_pending_schedule_reconciliation=True)
         if quote else call.block_if_ambiguous_remote_post()),
    ]


@pytest.mark.parametrize("name,flag", ENTRY_FLAGS.items())
def test_entry_points_reject_before_bootstrap(monkeypatch, name, flag):
    trace = _execution_trace(monkeypatch, {})
    trace.require_test_mode.return_value = False
    assert getattr(bot, name)() == 2
    assert trace.mock_calls == [call.require_test_mode(flag)]


@pytest.mark.parametrize("authorised", [False, True])
def test_test_mode_uses_current_frozen_authority_and_exact_error(monkeypatch, authorised):
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "IMPORT_TIME_TEST_MODE", authorised)
    monkeypatch.setattr(bot, "TEST_MODE", not authorised)
    monkeypatch.setenv("MRS_TEST_MODE", "0" if authorised else "1")
    command = object()
    assert bot.require_test_mode(command) is authorised
    assert logger.mock_calls == ([] if authorised else [
        call.error("%s requires MRS_TEST_MODE=1 before bot import", command),
    ])


@pytest.mark.parametrize("latched,proofs", [(False, []), (True, [True]), (True, [False, False, True])])
def test_durable_wait_checks_incident_first_and_repeats_proof_before_sleep(monkeypatch, latched, proofs):
    trace = Mock()
    for name in ("remote_write_safety_incident_is_latched", "durable_remote_write_safety_barrier_exists", "sleep", "log"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.remote_write_safety_incident_is_latched.return_value = latched
    trace.durable_remote_write_safety_barrier_exists.side_effect = proofs
    lane = object()
    assert bot.wait_for_durable_barrier_before_one_shot_exit(lane=lane) is None
    expected = [call.remote_write_safety_incident_is_latched()]
    if latched:
        expected += [call.durable_remote_write_safety_barrier_exists()]
    if len(proofs) > 1:
        expected += [
            call.log.critical(
                "The one-shot %s command cannot exit because its only remote-write safety "
                "barrier is process-local. Create and verify a durable reconciliation "
                "marker before terminating this process.", lane,
            ),
            call.durable_remote_write_safety_barrier_exists(), call.sleep(60),
            call.durable_remote_write_safety_barrier_exists(),
            call.log.critical(
                "A durable remote-write safety marker is now present for one-shot lane=%s; "
                "process exit is restart-safe", lane,
            ),
        ]
    assert trace.mock_calls == expected


@pytest.mark.parametrize("boundary", ["incident", "proof", "sleep", "log"])
def test_durable_wait_preserves_native_callback_failures(monkeypatch, boundary):
    trace = Mock()
    trace.incident.return_value = True
    trace.proof.return_value = False
    failure = KeyboardInterrupt("synthetic wait boundary")
    getattr(trace, boundary).side_effect = failure
    monkeypatch.setattr(bot, "remote_write_safety_incident_is_latched", trace.incident)
    monkeypatch.setattr(bot, "durable_remote_write_safety_barrier_exists", trace.proof)
    monkeypatch.setattr(bot, "sleep", trace.sleep)
    monkeypatch.setattr(bot, "log", SimpleNamespace(critical=trace.log))
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="synthetic")
    assert caught.value is failure
    assert trace.mock_calls[-1][0] == boundary


POST_MODES = {
    "quote": ("run_test_post_quote", "quote_image", "disable_quote_posts", "post_random_quote"),
    "meme": ("run_test_post_meme", "daily_meme", "disable_meme_posts", "post_next_meme"),
}


def _post_trace(monkeypatch, kind):
    state, lines, lines_used, paths, images_used = {}, ["quote\n"], set(), [], set()
    trace = _execution_trace(monkeypatch, state)
    for name in ("prepare_test_main_post_state", "lane_paused", "load_quote_used_hashes",
                 "current_image_paths", "load_image_used_basenames", "post_random_quote",
                 "post_next_meme", "record_api_error"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.lane_paused.return_value = False
    trace.load_quote_used_hashes.return_value = lines_used
    trace.current_image_paths.return_value = paths
    trace.load_image_used_basenames.return_value = images_used
    handle = MagicMock()
    trace.attach_mock(handle, "file")
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    handle.readlines.return_value = lines
    trace.open.return_value = handle
    monkeypatch.setattr(bot._cli_execution, "open", trace.open, raising=False)
    filename = object()
    monkeypatch.setattr(bot, "LINES_FILE", filename)
    name, lane, pause, post = POST_MODES[kind]
    label = "quote/image" if kind == "quote" else "daily meme"
    prefix = _preamble(ENTRY_FLAGS[name], quote=kind == "quote") + [
        call.log.info(f"Running one test {label} post cycle"),
        call.load_runtime_state(), call.prepare_test_main_post_state(state), call.lane_paused(pause),
    ]
    preflight = [] if kind == "meme" else [
        call.open(filename, encoding="utf-8"), call.file.__enter__(), call.file.readlines(),
        call.file.__exit__(None, None, None), call.load_quote_used_hashes(lines),
        call.current_image_paths(), call.load_image_used_basenames(paths),
    ]
    post_call = call.post_next_meme(state) if kind == "meme" else call.post_random_quote(lines_used, images_used, state)
    return SimpleNamespace(trace=trace, state=state, lines=lines, lines_used=lines_used,
                           images_used=images_used, paths=paths, name=name, lane=lane, label=label,
                           post=getattr(trace, post), prefix=prefix, preflight=preflight, post_call=post_call)


@pytest.mark.parametrize("kind,paused", [("quote", False), ("meme", False), ("quote", True), ("meme", True)])
def test_post_preamble_quote_context_history_references_and_pause_order(monkeypatch, kind, paused):
    case = _post_trace(monkeypatch, kind)
    case.trace.lane_paused.return_value = paused
    assert getattr(bot, case.name)() == 0
    expected = case.prefix
    if paused:
        expected += [
            call.log.warning(f"Skipping test {case.label} post due to runtime control file"),
            call.save_state(case.state),
        ]
    else:
        expected += case.preflight + [case.post_call, call.log.info(f"Test {case.label} post cycle finished")]
        assert case.post.call_args.args[-1] is case.state
        if kind == "quote":
            assert case.post.call_args.args[0] is case.lines_used
            assert case.post.call_args.args[1] is case.images_used
            assert case.trace.load_quote_used_hashes.call_args.args[0] is case.lines
            assert case.trace.load_image_used_basenames.call_args.args[0] is case.paths
    assert case.trace.mock_calls == expected
    assert case.trace.prepare_test_main_post_state.call_args.args[0] is case.state


def _post_error(name):
    cls = getattr(bot, name) if name != "RuntimeError" else RuntimeError
    return cls("posting failed", service="x") if issubclass(cls, bot.ApiError) else cls("posting failed")


@pytest.mark.parametrize("kind", POST_MODES)
@pytest.mark.parametrize("error_name,status,tail", [
    ("UnrecoverableConfirmedPostPersistenceError", 3, ["wait"]),
    ("ConfirmedPostLocalPersistenceError", 3, ["save"]),
    ("AmbiguousRemotePostOutcome", 1, ["wait", "record", "save"]),
    ("ApiError", 1, ["record", "save"]),
    ("RuntimeError", 1, ["save"]),
])
def test_post_ordered_catches_preserve_wait_error_identity_save_and_exit(monkeypatch, kind, error_name, status, tail):
    case = _post_trace(monkeypatch, kind)
    failure = _post_error(error_name)
    case.post.side_effect = failure
    assert getattr(bot, case.name)() == status
    prefix = case.prefix + case.preflight + [case.post_call]
    assert case.trace.mock_calls[:len(prefix)] == prefix
    caught = case.trace.mock_calls[len(prefix):]
    log_call = caught[0]
    assert log_call[0] == ("log.exception" if error_name in {"ApiError", "RuntimeError"} else "log.critical")
    if log_call[0] == "log.critical":
        assert log_call.kwargs == {"exc_info": True}
    expected = {
        "wait": call.wait_for_durable_barrier_before_one_shot_exit(lane=case.lane),
        "record": call.record_api_error(case.state, failure, "x", scope="write"),
        "save": call.save_state(case.state),
    }
    assert caught[1:] == [expected[name] for name in tail]
    if "record" in tail:
        assert case.trace.record_api_error.call_args.args[0] is case.state
        assert case.trace.record_api_error.call_args.args[1] is failure
    if "save" in tail:
        assert case.trace.save_state.call_args.args[0] is case.state


@pytest.mark.parametrize("boundary", ["open", "readlines", "close", "history"])
def test_quote_preflight_errors_escape_before_posting_catches(monkeypatch, boundary):
    case = _post_trace(monkeypatch, "quote")
    failure = _post_error("ApiError")
    callbacks = {"open": case.trace.open, "readlines": case.trace.file.readlines,
                 "close": case.trace.file.__exit__, "history": case.trace.load_quote_used_hashes}
    callbacks[boundary].side_effect = failure
    with pytest.raises(bot.ApiError) as caught:
        bot.run_test_post_quote()
    assert caught.value is failure
    case.post.assert_not_called()
    case.trace.save_state.assert_not_called()
    case.trace.record_api_error.assert_not_called()
    case.trace.log.exception.assert_not_called()
    if boundary == "readlines":
        assert case.trace.file.__exit__.call_args.args[1] is failure


@pytest.mark.parametrize("kind,error_name,boundary", [
    ("quote", "UnrecoverableConfirmedPostPersistenceError", "wait_for_durable_barrier_before_one_shot_exit"),
    ("meme", "AmbiguousRemotePostOutcome", "record_api_error"),
    ("quote", "ConfirmedPostLocalPersistenceError", "save_state"),
    ("meme", "UnrecoverableConfirmedPostPersistenceError", "log.critical"),
])
def test_post_handler_failures_escape_without_later_bookkeeping(monkeypatch, kind, error_name, boundary):
    case = _post_trace(monkeypatch, kind)
    case.post.side_effect = _post_error(error_name)
    callback = case.trace
    for part in boundary.split("."):
        callback = getattr(callback, part)
    failure = ValueError("handler boundary")
    callback.side_effect = failure
    with pytest.raises(ValueError) as caught:
        getattr(bot, case.name)()
    assert caught.value is failure
    assert case.trace.mock_calls[-1][0] == boundary


@pytest.mark.parametrize("kind,failure", [("quote", KeyboardInterrupt()), ("meme", SystemExit(9))])
def test_posting_does_not_catch_process_control_exceptions(monkeypatch, kind, failure):
    case = _post_trace(monkeypatch, kind)
    case.post.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        getattr(bot, case.name)()
    assert caught.value is failure
    assert case.trace.mock_calls == case.prefix + case.preflight + [case.post_call]


def _cycle_trace(monkeypatch, priority):
    state = {"next_reply_lane_priority": priority, "last_reply_epoch": 0}
    trace = _execution_trace(monkeypatch, state)
    for name in ("maybe_reply_to_mentions", "maybe_reply_to_quote_tweets", "ambiguous_remote_post_is_blocking", "log_event"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.ambiguous_remote_post_is_blocking.return_value = False
    normal_status, quote_status = object(), object()
    trace.maybe_reply_to_mentions.return_value = normal_status
    trace.maybe_reply_to_quote_tweets.return_value = quote_status
    prefix = _preamble("--test-cycle") + [call.log.info("Running one test cycle")]
    prefix += [call.log.info(message, getattr(bot, name)) for message, name in (
        ("Base dir=%s", "BASE_DIR"), ("State file=%s", "STATE_FILE"), ("Log file=%s", "LOG_FILE"),
        ("X base=%s", "X_BASE"), ("X upload base=%s", "X_UPLOAD_BASE"), ("OpenAI base=%s", "OPENAI_BASE"),
    )]
    prefix += [call.load_runtime_state()]
    return trace, state, quote_status, prefix


@pytest.mark.parametrize("priority,posted", [
    ("normal", None), ("normal", "normal"), ("normal", "quote"),
    ("quote", None), ("quote", "quote"), ("quote", "normal"),
])
def test_cycle_closures_keep_state_status_identity_priority_and_save_log_order(monkeypatch, priority, posted):
    trace, state, quote_status, expected = _cycle_trace(monkeypatch, priority)
    saves = []
    trace.save_state.side_effect = lambda current: saves.append(current["next_reply_lane_priority"])

    def action(lane, result):
        def run(current):
            assert current is state
            if posted == lane:
                current["last_reply_epoch"] = 7
            return result
        return run

    trace.maybe_reply_to_mentions.side_effect = action("normal", object())
    trace.maybe_reply_to_quote_tweets.side_effect = action("quote", quote_status)
    assert bot.run_test_cycle() == 0
    lanes = [priority] if posted == priority else [priority, "quote" if priority == "normal" else "normal"]
    for lane in lanes:
        expected += [(call.maybe_reply_to_mentions(state) if lane == "normal" else call.maybe_reply_to_quote_tweets(state)),
                     call.ambiguous_remote_post_is_blocking()]
        if lane == "quote":
            expected += [call.log.info("Test-cycle quote-tweet check status=%s", quote_status),
                         call.log_event("quote_check_status", status=quote_status, priority="test_cycle")]
        if posted == lane:
            message = ("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote" if lane == "normal"
                       else "Test-cycle quote-tweet lane posted; next reply-lane priority=normal")
            expected += [call.save_state(state), call.log.info(message)]
    expected += [call.save_state(state), call.log.info("Test cycle finished")]
    assert trace.mock_calls == expected
    next_priority = priority if posted is None else ("quote" if posted == "normal" else "normal")
    assert saves == [next_priority] * (1 if posted is None else 2)
    if "quote" in lanes:
        assert trace.log_event.call_args.kwargs["status"] is quote_status


@pytest.mark.parametrize("priority,blocked_after", [("normal", 1), ("quote", 2)])
def test_cycle_ambiguity_predicate_saves_and_stops_before_result_log_or_later_lane(monkeypatch, priority, blocked_after):
    trace, state, _status, _prefix = _cycle_trace(monkeypatch, priority)
    trace.ambiguous_remote_post_is_blocking.side_effect = [False] * (blocked_after - 1) + [True]
    assert bot.run_test_cycle() == 0
    assert trace.maybe_reply_to_mentions.call_count + trace.maybe_reply_to_quote_tweets.call_count == blocked_after
    assert trace.mock_calls[-3:] == [
        call.ambiguous_remote_post_is_blocking(),
        call.log.critical("Test cycle stopped after an ambiguous remote post; no later lane will run"),
        call.save_state(state),
    ]
    trace.wait_for_durable_barrier_before_one_shot_exit.assert_not_called()
    assert trace.log_event.call_count == blocked_after - 1


@pytest.mark.parametrize("priority,error_name", [("normal", "UnrecoverableConfirmedReplyPersistenceError"), ("quote", "AmbiguousRemotePostOutcome")])
def test_cycle_second_lane_safety_catch_waits_and_returns_without_final_save(monkeypatch, priority, error_name):
    trace, state, _status, _prefix = _cycle_trace(monkeypatch, priority)
    second = trace.maybe_reply_to_quote_tweets if priority == "normal" else trace.maybe_reply_to_mentions
    second.side_effect = _post_error(error_name)
    lane = "quote_tweet" if priority == "normal" else "normal"
    assert bot.run_test_cycle() == 0
    assert second.call_args.args[0] is state
    assert trace.mock_calls[-2:] == [
        call.log.critical("Test-cycle %s reply lane stopped by the global remote-write safety barrier", lane, exc_info=True),
        call.wait_for_durable_barrier_before_one_shot_exit(lane=f"{lane}_reply"),
    ]
    trace.save_state.assert_not_called()


@pytest.mark.parametrize("boundary", ["priority", "epoch", "event", "save", "wait"])
def test_cycle_native_failures_remain_outside_action_catch(monkeypatch, boundary):
    trace, state, _status, prefix = _cycle_trace(monkeypatch, "quote")
    failure = bot.UnrecoverableConfirmedReplyPersistenceError("native boundary")

    class Priority:
        def __str__(self):
            trace.priority()
            if boundary == "priority":
                raise failure
            return "quote"

    class Epoch:
        def __int__(self):
            trace.epoch()
            if boundary == "epoch":
                raise failure
            return 0

    state.update(next_reply_lane_priority=Priority(), last_reply_epoch=Epoch())
    if boundary == "event":
        trace.log_event.side_effect = failure
    elif boundary == "save":
        trace.save_state.side_effect = failure
    elif boundary == "wait":
        trace.maybe_reply_to_quote_tweets.side_effect = _post_error("AmbiguousRemotePostOutcome")
        trace.wait_for_durable_barrier_before_one_shot_exit.side_effect = failure
    with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError) as caught:
        bot.run_test_cycle()
    assert caught.value is failure
    assert trace.mock_calls[len(prefix)] == call.priority()
    if boundary != "priority":
        assert trace.mock_calls[len(prefix) + 1] == call.epoch()
    if boundary != "wait":
        trace.wait_for_durable_barrier_before_one_shot_exit.assert_not_called()
    assert call.log.info("Test cycle finished") not in trace.mock_calls


@pytest.mark.parametrize("changed,blocked", [((False, False), False), ((True, False), False), ((False, True), True)])
def test_tick_eager_scheduler_reads_health_order_and_barrier_before_completion(monkeypatch, changed, blocked):
    state, current, reply_epoch, quote_epoch = {}, object(), object(), object()
    trace = _execution_trace(monkeypatch, state)
    for name in ("report_bot_health_progress", "now_epoch", "scheduler_epoch_from_state",
                 "run_reply_lane_checks_for_tick", "ambiguous_remote_post_is_blocking"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.now_epoch.return_value = current
    trace.scheduler_epoch_from_state.side_effect = [(reply_epoch, changed[0]), (quote_epoch, changed[1])]
    trace.ambiguous_remote_post_is_blocking.return_value = blocked
    assert bot.run_test_main_tick() == 0
    expected = _preamble("--test-main-tick")
    expected.insert(2, call.report_bot_health_progress("startup"))
    expected.insert(4, call.report_bot_health_progress("recovery"))
    expected += [
        call.log.info("Running one test production reply-lane tick"), call.load_runtime_state(), call.now_epoch(),
        call.scheduler_epoch_from_state(state, "last_reply_check_epoch", current=current),
        call.scheduler_epoch_from_state(state, "last_quote_tweet_check_epoch", current=current),
    ]
    if any(changed):
        expected += [call.save_state(state)]
    expected += [
        call.report_bot_health_progress("main_loop", loop_started=True),
        call.report_bot_health_progress("reply_checks"),
        call.run_reply_lane_checks_for_tick(state, current),
        call.report_bot_health_progress("main_loop"), call.ambiguous_remote_post_is_blocking(),
    ]
    expected += ([call.wait_for_durable_barrier_before_one_shot_exit(lane="production_reply_tick")] if blocked else [
        call.save_state(state), call.log.info("Test production reply-lane tick finished"),
        call.report_bot_health_progress("shutdown", loop_completed=True),
    ])
    assert trace.mock_calls == expected
    tick = trace.run_reply_lane_checks_for_tick.call_args
    assert tick.args[0] is state and tick.args[1] is current
    assert tick.kwargs == {}


def _cli_trace(monkeypatch, arguments):
    trace = Mock()
    process = SimpleNamespace(argv=["bot", *arguments], stderr=io.StringIO())
    parser = bot.parse_cli_mode
    monkeypatch.setattr(bot, "sys", process)
    monkeypatch.setattr(bot, "IMPORT_TIME_CLI_ARGUMENTS", arguments)
    monkeypatch.setattr(bot, "IMPORT_TIME_TEST_MODE", True)
    for name in ("parse_cli_mode", "production_bootstrap", "initialise_installation", "run_self_test",
                 "run_test_cycle", "run_test_main_tick", "run_test_post_quote", "run_test_post_meme", "main"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.parse_cli_mode.side_effect = parser
    return trace, process


@pytest.mark.parametrize("mode,entry", [("--self-test", "run_self_test"), (None, "main")])
def test_cli_current_dispatch_returns_original_result_and_ignores_main_result(monkeypatch, mode, entry):
    arguments = () if mode is None else (mode,)
    trace, process = _cli_trace(monkeypatch, arguments)
    result = object()
    getattr(trace, entry).return_value = result
    assert bot.run_cli(list(arguments)) is (None if mode is None else result)
    assert trace.mock_calls == [call.parse_cli_mode(arguments), call.production_bootstrap(), getattr(call, entry)()]
    assert trace.parse_cli_mode.call_args.args[0] is arguments
    assert process.stderr.getvalue() == ""


@pytest.mark.parametrize("case,message", [
    ("process", "process argv changed after module import"),
    ("explicit", "explicit argv must exactly match the import-time command line"),
    ("parser", "unknown command mode: '--unknown'"),
    ("authority", "--test-cycle requires MRS_TEST_MODE=1 before bot import"),
])
def test_cli_validation_has_exact_stderr_and_no_bootstrap(monkeypatch, case, message):
    arguments = ("--unknown",) if case == "parser" else ("--test-cycle",)
    trace, process = _cli_trace(monkeypatch, arguments)
    explicit = None
    if case == "process":
        process.argv = ["bot"]
    elif case == "explicit":
        explicit = []
    elif case == "authority":
        monkeypatch.setattr(bot, "IMPORT_TIME_TEST_MODE", False)
    assert bot.run_cli(explicit) == 2
    assert trace.mock_calls == ([call.parse_cli_mode(arguments)] if case in {"parser", "authority"} else [])
    assert process.stderr.getvalue() == f"{bot.CLI_USAGE}\nmrsMThatcher2.py: error: {message}\n"


def test_cli_snapshots_process_before_explicit_iterator_and_validation(monkeypatch):
    arguments = ("--self-test",)
    trace, process = _cli_trace(monkeypatch, arguments)

    class ProcessArguments:
        def __getitem__(self, key):
            assert key == slice(1, None)
            trace.process_snapshot()
            return arguments

    def explicit():
        trace.explicit_snapshot()
        process.argv = ["mutated after snapshot"]
        yield "--self-test"

    process.argv = ProcessArguments()
    assert bot.run_cli(explicit()) is trace.run_self_test.return_value
    assert trace.mock_calls == [call.process_snapshot(), call.explicit_snapshot(), call.parse_cli_mode(arguments),
                                call.production_bootstrap(), call.run_self_test()]


@pytest.mark.parametrize("boundary", ["process", "explicit", "parser", "bootstrap", "dispatch"])
def test_cli_native_conversions_and_callbacks_escape_original_catch_scope(monkeypatch, boundary):
    trace, process = _cli_trace(monkeypatch, ("--self-test",))
    failure = TypeError("parser native error") if boundary == "parser" else bot.CliUsageError("outside validation")

    class BrokenArguments:
        def __getitem__(self, key):
            raise failure

        def __iter__(self):
            raise failure

    explicit = None
    if boundary == "process":
        process.argv = BrokenArguments()
    elif boundary == "explicit":
        explicit = BrokenArguments()
    else:
        target = {"parser": trace.parse_cli_mode, "bootstrap": trace.production_bootstrap, "dispatch": trace.run_self_test}[boundary]
        target.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        bot.run_cli(explicit)
    assert caught.value is failure
    assert process.stderr.getvalue() == ""
    expected = [] if boundary in {"process", "explicit"} else [call.parse_cli_mode(("--self-test",))]
    if boundary in {"bootstrap", "dispatch"}:
        expected += [call.production_bootstrap()]
    if boundary == "dispatch":
        expected += [call.run_self_test()]
    assert trace.mock_calls == expected
