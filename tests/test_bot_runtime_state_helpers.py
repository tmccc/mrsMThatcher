from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_runtime_state_helpers as owner
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {'default_state': ['STATE_MINIMUM_READER_VERSION'],
 'append_unique_capped': [],
 'append_unique_durable': [],
 'scheduler_epoch_from_state': ['log', 'math'],
 'load_runtime_state': ['clear_expired_api_cooldowns',
                        'load_state',
                        'sanitize_next_reply_lane_priority'],
 'apply_state_fields': [],
 'next_quote_schedule_fields': ['POST_SLEEP_MAX', 'POST_SLEEP_MIN', 'now_epoch', 'random'],
 'schedule_next_quote_post': ['apply_state_fields',
                              'datetime',
                              'log',
                              'next_quote_schedule_fields',
                              'save_state'],
 'prepare_test_main_post_state': ['ENABLE_DAILY_MEME_POSTS', 'ensure_meme_schedule_initialized']}

SIGNATURES = {'default_state': "() -> 'dict'",
 'append_unique_capped': "(values: 'object', item: 'object', max_items: 'int') -> 'list[str]'",
 'append_unique_durable': "(values: 'object', item: 'object') -> 'list[str]'",
 'scheduler_epoch_from_state': "(state: 'dict', key: 'str', *, current: 'int | None' = None) -> "
                               "'tuple[int, bool]'",
 'load_runtime_state': "() -> 'dict'",
 'apply_state_fields': "(state: 'dict', fields: 'dict') -> 'None'",
 'next_quote_schedule_fields': "(from_epoch: 'int | None' = None, *, delay: 'int | None' = None) "
                               "-> 'tuple[dict, int]'",
 'schedule_next_quote_post': "(state: 'dict', from_epoch: 'int | None' = None, *, save: 'bool' = "
                             "True) -> 'None'",
 'prepare_test_main_post_state': "(state: 'dict') -> 'None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Runtime-state-helpers import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_runtime_state_helpers':
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
import mrs_bot_runtime_state_helpers
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_runtime_state_helpers.default_state.__annotations__['return'] == 'dict'
assert mrs_bot_runtime_state_helpers.scheduler_epoch_from_state.__annotations__['current'] == 'int | None'
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
    if not DEPENDENCIES[name]:
        assert adapter is getattr(owner, name)
        assert adapter.__annotations__ == getattr(owner, name).__annotations__
        return
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

            patch.setattr(bot, "_runtime_state_helpers", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_runtime_state_helpers", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_default_state_keeps_complete_ordered_values_current_policies_and_fresh_containers(monkeypatch):
    STATE_MINIMUM_READER_VERSION = object()
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", STATE_MINIMUM_READER_VERSION)
    expected = {
        "minimum_reader_version": STATE_MINIMUM_READER_VERSION,
        "last_seen_mention_id": None,
        "mention_pagination": {},
        "mention_backlog": {},
        "mention_backlog_reset_guard": {},
        "mention_pending_candidates": {},
        "author_evaluation_quarantines": {},
        "replied_to_ids": [],
        "dry_run_seen_mention_ids": [],
        "skipped_hot_reply_ids": [],
        "skipped_hot_reply_records": {},
        "hot_post_reply_since_ids": {},
        "hot_post_reply_pagination_tokens": {},
        "hot_post_reply_check_counts": {},

        "daily_reply_date": None,
        "daily_reply_count": 0,
        "daily_replied_author_ids": [],
        "daily_replied_author_counts": {},

        "own_auto_reply_ids": [],
        "tweet_cache": {},
        "pending_ai_reply_drafts": {},
        "ai_reply_history": [],

        "posted_meme_filenames": [],
        "last_meme_post_epoch": 0,
        "next_meme_post_epoch": 0,
        "meme_schedule_version": 0,
        "next_meme_schedule_mode": "",
        "next_meme_schedule_date": "",
        "meme_anchor_quote_post_epoch": 0,

        "last_reply_epoch": 0,
        "last_reply_check_epoch": 0,
        "next_reply_lane_priority": "normal",
        "last_main_post_id": None,
        "last_regular_image_filename": None,
        "last_quote_post_epoch": 0,
        "next_quote_post_epoch": 0,

        "recent_own_post_ids": [],

        "seen_quote_post_ids": [],
        "replied_to_quote_post_ids": [],
        "skipped_quote_post_ids": [],
        "quote_lookup_pagination_tokens": {},
        "quote_search_pagination_tokens": {},
        "quote_lookup_repeated_cursor_suppressions": {},
        "quote_spam_author_ids": [],
        "daily_quote_reply_date": None,
        "daily_quote_reply_count": 0,
        "last_quote_tweet_check_epoch": 0,

        "x_error_epochs": [],
        "x_write_error_epochs": [],
        "openai_error_epochs": [],
        "api_cooldown_until_epoch": 0,
        "api_cooldown_reason": "",
        "x_write_api_cooldown_until_epoch": 0,
        "x_write_api_cooldown_reason": "",
        "openai_api_cooldown_until_epoch": 0,
        "openai_api_cooldown_reason": "",
        "quote_x_error_epochs": [],
        "quote_api_cooldown_until_epoch": 0,
        "quote_api_cooldown_reason": "",
    }

    first, second = bot.default_state(), bot.default_state()
    assert list(first.items()) == list(second.items()) == list(expected.items())
    assert first["minimum_reader_version"] is STATE_MINIMUM_READER_VERSION
    containers = [state for state in (first, second)] + [
        value for state in (first, second) for value in state.values()
        if isinstance(value, (dict, list))
    ]
    assert len({id(value) for value in containers}) == len(containers)
    for value in first.values():
        if isinstance(value, dict):
            value["changed"] = object()
        elif isinstance(value, list):
            value.append(object())
    assert list(second.items()) == list(expected.items())
    assert list(bot.default_state().items()) == list(expected.items())
    for key, policy in (
        ("minimum_reader_version", "STATE_MINIMUM_READER_VERSION"),
    ):
        current = object()
        monkeypatch.setattr(bot, policy, current)
        assert bot.default_state()[key] is current


@pytest.mark.parametrize("name, expected", [
    ("append_unique_capped", ["a", "a", "b", "x"]),
    ("append_unique_durable", ["a", "x", "b"]),
])
def test_append_helpers_keep_item_first_list_subclasses_distinct_duplicates_and_input(name, expected):
    events = []

    class Text:
        def __init__(self, label, value):
            self.label, self.value = label, value

        def __str__(self):
            events.append(self.label)
            return self.value

    class Values(list):
        def __iter__(self):
            events.append("iterate")
            return super().__iter__()

    original = [Text(str(i), value) for i, value in enumerate(["a", "x", "a", "x", "b"])]
    values = Values(original)
    args = (99,) if name == "append_unique_capped" else ()
    result = getattr(bot, name)(values, Text("item", "x"), *args)
    assert result == expected
    assert events == ["item", "iterate", "0", "1", "2", "3", "4"]
    assert values == original and result is not values
    assert type(result) is list
    assert getattr(bot, name)([1, "1", 2], 3, *args) == (["1", "1", "2", "3"] if args else ["1", "2", "3"])


@pytest.mark.parametrize("values", [None, ("a", "b"), {"a": 1}, "abc", 42])
def test_append_helpers_only_accept_existing_lists(values):
    assert bot.append_unique_capped(values, 7, 4) == ["7"]
    assert bot.append_unique_durable(values, 7) == ["7"]


@pytest.mark.parametrize("cap, expected", [
    (0, ["a", "b", "c"]), (-1, ["b", "c"]), (-9, []),
    (True, ["c"]), (False, ["a", "b", "c"]), (2, ["b", "c"]),
])
def test_capped_append_keeps_native_zero_negative_and_boolean_slices(cap, expected):
    assert bot.append_unique_capped(["a", "b"], "c", cap) == expected


def test_capped_append_keeps_negation_then_index_and_native_errors():
    events = []

    class Index:
        def __index__(self):
            events.append("index")
            return -2

    class Cap:
        def __neg__(self):
            events.append("negate")
            return Index()

    assert bot.append_unique_capped(["a", "b"], "c", Cap()) == ["b", "c"]
    assert events == ["negate", "index"]
    for cap in (None, 1.5, "2"):
        with pytest.raises(TypeError):
            bot.append_unique_capped(["a"], "b", cap)


@pytest.mark.parametrize("name", ["append_unique_capped", "append_unique_durable"])
@pytest.mark.parametrize("where", ["item", "iteration", "value"])
def test_append_conversion_failures_escape_in_order_without_input_mutation(name, where):
    failure, events = ValueError(where), []

    class Text:
        def __init__(self, label):
            self.label = label

        def __str__(self):
            events.append(self.label)
            if where == self.label:
                raise failure
            return self.label

    class Values(list):
        def __iter__(self):
            events.append("iteration")
            if where == "iteration":
                raise failure
            return super().__iter__()

    value = Text("value")
    values = Values([value])
    with pytest.raises(ValueError) as caught:
        getattr(bot, name)(values, Text("item"), *((2,) if name == "append_unique_capped" else ()))
    assert caught.value is failure
    assert events == ["item", "iteration", "value"][:{"item": 1, "iteration": 2, "value": 3}[where]]
    assert len(values) == 1 and values[0] is value


def test_durable_append_keeps_native_hash_failure():
    class UnhashableText(str):
        __hash__ = None

    class Value:
        def __str__(self):
            return UnhashableText("a")

    with pytest.raises(TypeError, match="unhashable"):
        bot.append_unique_durable([Value()], "b")


@pytest.mark.parametrize("failure_at", [None, "iteration", "unpack", "write"])
def test_apply_fields_keeps_items_order_references_and_individual_partial_writes(failure_at):
    first, second, value, other = object(), object(), object(), object()
    events, stored = [], {}
    failure = RuntimeError("field failure")

    class Fields:
        def items(self):
            events.append("items")
            yield first, value
            events.append("next")
            if failure_at == "iteration":
                raise failure
            yield (second,) if failure_at == "unpack" else (second, other)

    class State:
        def __setitem__(self, key, item):
            events.append((key, item))
            if key is second and failure_at == "write":
                raise failure
            stored[key] = item

    if failure_at is None:
        assert bot.apply_state_fields(State(), Fields()) is None
        assert stored == {first: value, second: other}
    else:
        with pytest.raises(ValueError if failure_at == "unpack" else RuntimeError) as caught:
            bot.apply_state_fields(State(), Fields())
        if failure_at != "unpack":
            assert caught.value is failure
        assert stored == {first: value}
    assert events == ["items", (first, value), "next"] + (
        [(second, other)] if failure_at in (None, "write") else []
    )


@pytest.mark.parametrize("present, raw, current, value, changed, warnings", [
    (False, None, None, 0, False, []),
    (True, 0, None, 0, False, []),
    (True, 12, 12, 12, False, []),
    (True, None, None, 0, True, []),
    (True, [], None, 0, True, []),
    (True, "", None, 0, True, []),
    (True, " 12 ", None, 12, True, []),
    (True, 12.0, None, 12, True, []),
    (True, "bad", None, 0, True, ["malformed"]),
    (True, float("nan"), None, 0, True, ["malformed"]),
    (True, -2, None, 0, True, ["negative"]),
    (True, 12, 11, 0, True, ["future"]),
    (True, -2, -1, 0, True, ["negative", "future"]),
    (True, False, -1, 0, True, ["malformed", "future"]),
])
def test_scheduler_epoch_keeps_conversion_warning_order_and_conditional_writes(monkeypatch, present, raw, current, value, changed, warnings):
    trace = Mock()

    class State(dict):
        def get(self, key, default):
            trace.get(key, default)
            return super().get(key, default)

        def __setitem__(self, key, item):
            trace.write(key, item)
            super().__setitem__(key, item)

    state = State(epoch=raw) if present else State()
    monkeypatch.setattr(bot, "log", trace.log)
    assert bot.scheduler_epoch_from_state(state, "epoch", current=current) == (value, changed)
    expected = [call.get("epoch", 0)]
    for warning in warnings:
        if warning == "future":
            expected.append(call.log.warning("Ignoring future scheduler epoch %s=%r current=%s", "epoch", raw, current))
        else:
            expected.append(call.log.warning(f"Ignoring {warning} scheduler epoch %s=%r", "epoch", raw))
    if changed:
        expected.append(call.write("epoch", value))
    assert trace.mock_calls == expected
    assert ("epoch" in state) is (present or changed)
    if "epoch" in state:
        assert type(state["epoch"]) is int and state["epoch"] == value


@pytest.mark.parametrize("where", ["truth", "int"])
@pytest.mark.parametrize("error_type", [TypeError, ValueError, OverflowError, RuntimeError, KeyboardInterrupt])
def test_scheduler_conversion_catches_only_original_narrow_errors(monkeypatch, where, error_type):
    failure, events, log = error_type("conversion"), [], Mock()

    class Raw:
        def __bool__(self):
            events.append("truth")
            if where == "truth":
                raise failure
            return True

        def __int__(self):
            events.append("int")
            raise failure

    raw = Raw()
    state = {"epoch": raw}
    monkeypatch.setattr(bot, "log", log)
    if error_type in (TypeError, ValueError, OverflowError):
        assert bot.scheduler_epoch_from_state(state, "epoch") == (0, True)
        assert state == {"epoch": 0}
        log.warning.assert_called_once_with("Ignoring malformed scheduler epoch %s=%r", "epoch", raw)
    else:
        with pytest.raises(error_type) as caught:
            bot.scheduler_epoch_from_state(state, "epoch")
        assert caught.value is failure and state["epoch"] is raw
        log.warning.assert_not_called()
    assert events == ["truth"] + (["int"] if where == "int" else [])


@pytest.mark.parametrize("where", ["finite", "integer"])
def test_scheduler_float_checks_stay_before_conversion_try(monkeypatch, where):
    failure, events, log = ValueError("pre-try"), [], Mock()

    class Raw(float):
        def is_integer(self):
            events.append("integer")
            raise failure

    raw = Raw(2)

    def finite(value):
        assert value is raw
        events.append("finite")
        if where == "finite":
            raise failure
        return True

    monkeypatch.setattr(bot, "math", SimpleNamespace(isfinite=finite))
    monkeypatch.setattr(bot, "log", log)
    state = {"epoch": raw}
    with pytest.raises(ValueError) as caught:
        bot.scheduler_epoch_from_state(state, "epoch")
    assert caught.value is failure and state["epoch"] is raw
    assert events == ["finite"] + (["integer"] if where == "integer" else [])
    log.warning.assert_not_called()


def test_scheduler_keeps_lazy_raw_comparison_and_uncoerced_current(monkeypatch):
    events = []

    class Raw(int):
        def __ne__(self, other):
            pytest.fail("non-exact ints must short-circuit raw comparison")

    class Current:
        def __int__(self):
            pytest.fail("current must not be coerced")

        def __lt__(self, other):
            events.append(other)
            return False

    monkeypatch.setattr(bot, "log", Mock())
    state = {"epoch": Raw(12)}
    assert bot.scheduler_epoch_from_state(state, "epoch", current=Current()) == (12, True)
    assert events == [12] and type(state["epoch"]) is int


@pytest.mark.parametrize("where", ["get", "current", "warning", "write"])
def test_scheduler_native_failures_outside_conversion_stop_without_mutation(monkeypatch, where):
    failure, events = ValueError(where), []

    class State(dict):
        def get(self, key, default):
            events.append("get")
            if where == "get":
                raise failure
            return -2

        def __setitem__(self, key, value):
            events.append("write")
            raise failure

    class Current:
        def __lt__(self, value):
            events.append("current")
            raise failure

    def warning(*args):
        events.append("warning")
        if where == "warning":
            raise failure

    state = State(epoch=-2)
    monkeypatch.setattr(bot, "log", SimpleNamespace(warning=warning))
    with pytest.raises(ValueError) as caught:
        bot.scheduler_epoch_from_state(state, "epoch", current=Current() if where == "current" else None)
    assert caught.value is failure and state == {"epoch": -2}
    assert events == {
        "get": ["get"], "warning": ["get", "warning"],
        "current": ["get", "warning", "current"], "write": ["get", "warning", "write"],
    }[where]


@pytest.mark.parametrize("failure_at", [None, "load", "cooldown", "priority"])
def test_runtime_load_keeps_current_callbacks_same_state_order_and_no_save(monkeypatch, failure_at):
    state, events, failure = {}, [], RuntimeError("maintenance")

    def load():
        events.append("load")
        if failure_at == "load":
            raise failure
        return state

    def maintain(name, current):
        assert current is state
        events.append(name)
        state[name] = True
        if failure_at == name:
            raise failure
        return object()

    monkeypatch.setattr(bot, "load_state", load)
    monkeypatch.setattr(bot, "clear_expired_api_cooldowns", lambda current: maintain("cooldown", current))
    monkeypatch.setattr(bot, "sanitize_next_reply_lane_priority", lambda current: maintain("priority", current))
    save = Mock(side_effect=AssertionError("unexpected save"))
    monkeypatch.setattr(bot, "save_state", save)
    if failure_at is None:
        assert bot.load_runtime_state() is state
    else:
        with pytest.raises(RuntimeError) as caught:
            bot.load_runtime_state()
        assert caught.value is failure
    expected = ["load", "cooldown", "priority"]
    if failure_at:
        expected = expected[:expected.index(failure_at) + 1]
    assert events == expected
    assert list(state) == expected[1:]
    save.assert_not_called()


@pytest.mark.parametrize("use_clock, use_draw", [(True, True), (True, False), (False, True), (False, False)])
def test_quote_fields_keep_lazy_clock_draw_int_add_order_delay_reference_and_fresh_dict(monkeypatch, use_clock, use_draw):
    events, result, low, high = [], object(), object(), object()

    class Epoch:
        def __int__(self):
            events.append("int")
            return 100

    class Delay:
        def __int__(self):
            pytest.fail("delay must not be coerced")

        def __radd__(self, epoch):
            assert epoch == 100
            events.append("add")
            return result

    epoch, delay = Epoch(), Delay()

    def now():
        events.append("clock")
        return epoch

    def draw(minimum, maximum):
        assert minimum is low and maximum is high
        events.append("draw")
        return delay

    monkeypatch.setattr(bot, "now_epoch", now)
    monkeypatch.setattr(bot, "random", SimpleNamespace(randint=draw))
    monkeypatch.setattr(bot, "POST_SLEEP_MIN", low)
    monkeypatch.setattr(bot, "POST_SLEEP_MAX", high)
    first, returned = bot.next_quote_schedule_fields(None if use_clock else epoch, delay=None if use_draw else delay)
    assert returned is delay and first == {"next_quote_post_epoch": result}
    assert events == (["clock"] if use_clock else []) + (["draw"] if use_draw else []) + ["int", "add"]
    second, returned = bot.next_quote_schedule_fields(epoch, delay=delay)
    assert second == first and second is not first and returned is delay
    assert bot.next_quote_schedule_fields("10", delay=0.5) == ({"next_quote_post_epoch": 10.5}, 0.5)
    assert bot.next_quote_schedule_fields(True, delay=-2) == ({"next_quote_post_epoch": -1}, -2)


@pytest.mark.parametrize("failure_at", ["clock", "draw", "int", "add"])
def test_quote_fields_native_failures_keep_lazy_evaluation_order(monkeypatch, failure_at):
    events, failure = [], ValueError(failure_at)

    def step(name, result):
        events.append(name)
        if failure_at == name:
            raise failure
        return result

    class Epoch:
        def __int__(self):
            return step("int", 10)

    class Delay:
        def __radd__(self, other):
            return step("add", 20)

    monkeypatch.setattr(bot, "now_epoch", lambda: step("clock", Epoch()))
    monkeypatch.setattr(bot, "random", SimpleNamespace(randint=lambda *_: step("draw", Delay())))
    with pytest.raises(ValueError) as caught:
        bot.next_quote_schedule_fields()
    assert caught.value is failure
    assert events == ["clock", "draw", "int", "add"][:["clock", "draw", "int", "add"].index(failure_at) + 1]


@pytest.mark.parametrize("save_enabled, failure_at", [
    (enabled, failure)
    for enabled in (False, True)
    for failure in (None, "fields", "apply", "truth", "save", "read", "timestamp", "format", "log")
    if enabled or failure != "save"
])
def test_quote_scheduling_keeps_apply_optional_save_state_reread_and_format_log_order(monkeypatch, save_enabled, failure_at):
    trace, failure = Mock(), RuntimeError("schedule")
    initial, saved, delay, rendered, origin = object(), object(), object(), object(), object()
    fields = {"next_quote_post_epoch": initial}

    def step(name, *args):
        getattr(trace, name)(*args)
        if failure_at == name:
            raise failure

    class State(dict):
        def __getitem__(self, key):
            step("read", key)
            return super().__getitem__(key)

    class Save:
        def __bool__(self):
            step("truth")
            return save_enabled

    state = State()

    def next_fields(value):
        assert value is origin
        step("fields", value)
        return fields, delay

    def apply(current, supplied):
        assert current is state and supplied is fields
        owner.apply_state_fields(current, supplied)
        step("apply", current, supplied)

    def save(current):
        assert current is state
        current["next_quote_post_epoch"] = saved
        step("save", current)

    def timestamp(value):
        assert value is (saved if save_enabled else initial)
        step("timestamp", value)
        return SimpleNamespace(strftime=format_time)

    def format_time(fmt):
        step("format", fmt)
        return rendered

    monkeypatch.setattr(bot, "next_quote_schedule_fields", next_fields)
    monkeypatch.setattr(bot, "apply_state_fields", apply)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "datetime", SimpleNamespace(fromtimestamp=timestamp))
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=lambda *args: step("log", *args)))
    if failure_at is None:
        assert bot.schedule_next_quote_post(state, origin, save=Save()) is None
    else:
        with pytest.raises(RuntimeError) as caught:
            bot.schedule_next_quote_post(state, origin, save=Save())
        assert caught.value is failure
    expected = [call.fields(origin), call.apply(state, fields), call.truth()]
    if save_enabled:
        expected.append(call.save(state))
    expected += [
        call.read("next_quote_post_epoch"), call.timestamp(saved if save_enabled else initial),
        call.format("%Y-%m-%d %H:%M:%S"),
        call.log("Next quote/image post in %d seconds at %s", delay, rendered),
    ]
    if failure_at:
        expected = expected[:next(i for i, c in enumerate(expected) if c[0] == failure_at) + 1]
    assert trace.mock_calls == expected
    if failure_at == "fields":
        assert state == {}
    else:
        saved_already = any(c[0] == "save" for c in expected)
        assert dict(state) == {"next_quote_post_epoch": saved if saved_already else initial}


@pytest.mark.parametrize("enabled, failure_at", [(False, None), (True, None), (True, "truth"), (True, "callback")])
def test_test_preparation_keeps_current_truth_callback_same_state_and_implicit_none(monkeypatch, enabled, failure_at):
    events, state, failure = [], {}, ValueError("preparation")

    class Enabled:
        def __bool__(self):
            events.append("truth")
            if failure_at == "truth":
                raise failure
            return enabled

    def prepare(current):
        assert current is state
        events.append("callback")
        state["prepared"] = True
        if failure_at == "callback":
            raise failure
        return object()

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", Enabled())
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", prepare)
    save = Mock(side_effect=AssertionError("unexpected save"))
    monkeypatch.setattr(bot, "save_state", save)
    if failure_at:
        with pytest.raises(ValueError) as caught:
            bot.prepare_test_main_post_state(state)
        assert caught.value is failure
    else:
        assert bot.prepare_test_main_post_state(state) is None
    invoked = enabled and failure_at != "truth"
    assert events == ["truth"] + (["callback"] if invoked else [])
    assert state == ({"prepared": True} if invoked else {})
    save.assert_not_called()
