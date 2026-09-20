from __future__ import annotations

from datetime import datetime, timedelta
import inspect
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock, call
from zoneinfo import ZoneInfo

import pytest

import mrs_bot_daily_meme as meme
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    configure_simple_meme_post,
    isolate_bot_runtime,  # noqa: F401
    mock_confirmed_main_post,
)


def test_import_needs_no_runtime_access_and_keeps_shared_standard_library():
    code = """
import builtins, collections.abc, dataclasses, datetime, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('daily meme import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_daily_meme', 'mrs_bot_runtime_state_helpers'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_daily_meme
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert meme.random is bot.random is random
    assert meme.re is bot.re and meme.datetime is bot.datetime
    assert bot.original_meme_filename is meme.original_meme_filename


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "run_daily_meme_stage", "require_valid_meme_post_id", "post_next_meme",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(meme, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(meme, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                factories = {}
                for key, factory_name, factory_args in (
                    ("publication", "_main_post_publication_owner", ("daily_meme",)),
                    ("catalog", "_meme_catalog_owner", ()),
                    ("schedule", "_meme_schedule_owner", ()),
                ):
                    if key in current:
                        factories[key] = Mock(return_value=current[key])
                        patch.setattr(bot, factory_name, factories[key])
                for key, value in current.items():
                    if key not in factories:
                        patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result, name
                for key, factory in factories.items():
                    factory.assert_called_once_with(
                        *(("daily_meme",) if key == "publication" else ())
                    )
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args)), name
                expected = {**options, **current}
                assert actual_kwargs.keys() == expected.keys(), name
                assert all(actual_kwargs[key] is value for key, value in expected.items()), name
            failure = KeyboardInterrupt(name)
            owner.side_effect = failure
            with pytest.raises(KeyboardInterrupt) as caught:
                adapter(*args, **options)
            assert caught.value is failure


SCHEDULE_METHODS = {'meme_schedule_datetime': 'datetime',
 'meme_schedule_date_str': 'date_str',
 'meme_posted_on_date': 'posted_on_date',
 'next_meme_fallback_epoch': 'next_fallback_epoch',
 'next_meme_schedule_fields': 'next_fields',
 'meme_delay_schedule_fields': 'delay_fields',
 'set_meme_delay_schedule': 'set_delay',
 'schedule_next_meme_post': 'schedule_next',
 'ensure_meme_schedule_initialized': 'ensure_initialized',
 'meme_schedule_fields_after_quote_post': 'fields_after_quote',
 'maybe_schedule_meme_after_quote_post': 'maybe_after_quote'}
SCHEDULE_INPUTS = {'bound_datetime': 'bound_schedule_datetime',
 'timezone': 'MAIN_POST_SCHEDULE_TIMEZONE',
 'now_epoch': 'now_epoch',
 'fallback_hour': 'MEME_FALLBACK_HOUR',
 'fallback_minute': 'MEME_FALLBACK_MINUTE',
 'version': 'MEME_SCHEDULE_VERSION',
 'modes': 'MEME_SCHEDULE_MODES',
 'enabled': 'ENABLE_DAILY_MEME_POSTS',
 'trigger_hour': 'MEME_TRIGGER_AFTER_HOUR',
 'minimum_delay': 'MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS',
 'maximum_delay': 'MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS',
 'save_state': 'save_state',
 'log': 'log'}


def patch_schedule(monkeypatch, name, callback):
    monkeypatch.setattr(meme.MemeSchedule, name, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_schedule_owner_binds_current_inputs_without_runtime_access(monkeypatch):
    from dataclasses import FrozenInstanceError

    previous = None
    for _ in range(2):
        current = {name: Mock(side_effect=AssertionError("construction performed runtime work")) for name in SCHEDULE_INPUTS}
        for name, value in current.items():
            monkeypatch.setattr(bot, SCHEDULE_INPUTS[name], value)
        owner = bot._meme_schedule_owner()
        assert owner is not previous
        assert vars(owner).keys() == current.keys()
        assert all(getattr(owner, name) is value for name, value in current.items())
        assert all(not value.called for value in current.values())
        with pytest.raises(FrozenInstanceError):
            owner.version = 123
        previous = owner


def test_schedule_adapters_preserve_signatures_arguments_results_and_errors(monkeypatch):
    for root_name, method in SCHEDULE_METHODS.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(meme.MemeSchedule, method)).parameters
        assert list(public) == list(owned)[1:]
        assert [(p.kind, p.default) for p in public.values()] == [(p.kind, p.default) for p in list(owned.values())[1:]]
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        callback = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch_schedule(patch, method, callback)
            assert adapter(*args, **options) is result
            actual_args, actual_kwargs = callback.call_args
            assert len(actual_args) == len(args)
            assert all(actual is expected for actual, expected in zip(actual_args, args))
            assert actual_kwargs.keys() == options.keys()
            assert all(actual_kwargs[key] is value for key, value in options.items())
            failure = KeyboardInterrupt(root_name)
            callback.side_effect = failure
            with pytest.raises(KeyboardInterrupt) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_owned_quote_schedule_keeps_two_clock_samples_around_save(monkeypatch):
    epoch = int(datetime(2026, 7, 6, 13, tzinfo=ZoneInfo("Europe/London")).timestamp())
    events, state = [], {}
    samples = iter([epoch, epoch + 2])

    def clock():
        events.append("clock")
        return next(samples)

    def save(current):
        assert current is state and current["meme_anchor_quote_post_epoch"] == epoch
        events.append("save")

    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot.random, "randint", lambda *_args: 3600)
    monkeypatch.setattr(bot, "log", Mock(info=lambda *args: events.append(("log", args))))
    for root_name in SCHEDULE_METHODS:
        if root_name != "maybe_schedule_meme_after_quote_post":
            monkeypatch.setattr(bot, root_name, Mock(side_effect=AssertionError("owned schedule bounced through root")))
    bot.maybe_schedule_meme_after_quote_post(state)
    assert events[:3] == ["clock", "save", "clock"]
    assert len(events) == 4 and events[3][0] == "log"
    assert events[3][1][2] == 3598
    assert state["next_meme_post_epoch"] == epoch + 3600


CATALOG_METHODS = {'build_meme_cache_summary': 'summary',
 'list_meme_candidates': 'candidates',
 'choose_next_meme': 'choose'}
CATALOG_INPUTS = {'log': 'log',
 'directory': 'MEME_DIR',
 'reset_when_exhausted': 'RESET_MEME_CYCLE_WHEN_ALL_POSTED',
 'save_state': 'save_state'}


def patch_catalog(monkeypatch, name, callback):
    monkeypatch.setattr(meme.MemeCatalog, name, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_catalog_owner_binds_current_inputs_without_reading(monkeypatch):
    from dataclasses import FrozenInstanceError

    previous = None
    for _ in range(2):
        current = {name: Mock(side_effect=AssertionError("construction read runtime inputs")) for name in CATALOG_INPUTS}
        for name, value in current.items():
            monkeypatch.setattr(bot, CATALOG_INPUTS[name], value)
        owner = bot._meme_catalog_owner()
        assert owner is not previous and vars(owner).keys() == current.keys()
        assert all(getattr(owner, name) is value for name, value in current.items())
        assert all(not value.called for value in current.values())
        with pytest.raises(FrozenInstanceError):
            owner.directory = "elsewhere"
        previous = owner


def test_catalog_adapters_preserve_signatures_references_and_errors(monkeypatch):
    for root_name, method in CATALOG_METHODS.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(meme.MemeCatalog, method)).parameters
        assert list(public) == list(owned)[1:]
        assert [(p.kind, p.default) for p in public.values()] == [(p.kind, p.default) for p in list(owned.values())[1:]]
        args = tuple(object() for p in public.values() if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, p in public.items() if p.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        callback = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch_catalog(patch, method, callback)
            assert adapter(*args, **options) is result
            actual_args, actual_kwargs = callback.call_args
            assert len(actual_args) == len(args)
            assert all(actual is expected for actual, expected in zip(actual_args, args))
            assert actual_kwargs.keys() == options.keys()
            assert all(actual_kwargs[key] is value for key, value in options.items())
            failure = KeyboardInterrupt(root_name)
            callback.side_effect = failure
            with pytest.raises(KeyboardInterrupt) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_filename_alias_uses_shared_regex_and_preserves_exact_pattern_order(monkeypatch):
    match = Mock(wraps=bot.re.match)
    monkeypatch.setattr(bot.re, "match", match)
    first = r"^\d{3}_impact\d+_share\d+_grade[A-D]_post_as_is_(.+)$"
    second = r"^\d{3}_score\d+_(?:high|medium|low)_(.+)$"
    for name, expected, patterns in (
        ("123_impact12_share34_gradeD_post_as_is_original.PNG", "original.PNG", [first]),
        ("123_score42_medium_original.webp", "original.webp", [first, second]),
        ("123_impact12_share34_gradeE_post_as_is_original.PNG", "123_impact12_share34_gradeE_post_as_is_original.PNG", [first, second]),
        ("12_score42_high_original.jpg", "12_score42_high_original.jpg", [first, second]),
    ):
        match.reset_mock()
        assert bot.original_meme_filename(Path(name)) == expected
        assert match.call_args_list == [call(pattern, name) for pattern in patterns]


def test_summary_uses_current_filename_callback_and_original_item_without_copy(monkeypatch):
    path, accesses = Path("shortlist.png"), []

    class Item(dict):
        def get(self, key, *args):
            accesses.append((self, key))
            return super().get(key, *args)

    item = Item(grok_description=" Description. ", anti_socialist_message=" Message. ", ranking=0, shareability=" high ")
    index = {"original.png": item}
    original = Mock(return_value="original.png")
    log = Mock()
    monkeypatch.setattr(meme, "original_meme_filename", original)
    monkeypatch.setattr(bot, "log", log)
    assert bot.build_meme_cache_summary(path, index) == (
        "Description. Anti-socialist message: Message. Analysis metadata: ranking 0, shareability high."
    )
    assert all(value is item for value, _ in accesses)
    assert [key for _, key in accesses] == ["grok_description", "anti_socialist_message", "ranking", "shareability"]
    assert original.call_args.args[0] is path
    original.return_value = "missing.png"
    assert bot.build_meme_cache_summary(path, index) == "Anti-socialist meme image. Original filename: missing.png."
    log.warning.assert_called_once_with(
        "No meme analysis found for shortlist file=%s original_name=%s", "shortlist.png", "missing.png",
    )
    assert index["original.png"] is item


def test_catalog_and_cycle_reset_keep_path_state_references_and_save_boundary(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("z.png", "A.JPG", "a.webp", "b.jpeg", "skip.gif")]
    for path in paths:
        path.write_bytes(b"asset")
    folder = tmp_path / "folder.png"
    folder.mkdir()
    directory = Mock()
    directory.exists.return_value = True
    directory.iterdir.return_value = [*paths, folder]
    monkeypatch.setattr(bot, "MEME_DIR", directory)
    candidates = bot.list_meme_candidates()
    assert all(actual is expected for actual, expected in zip(candidates, [paths[1], paths[2], paths[3], paths[0]]))
    assert len(candidates) == 4
    directory.exists.return_value = False
    directory.iterdir.reset_mock()
    assert bot.list_meme_candidates() == []
    directory.iterdir.assert_not_called()

    patch_catalog(monkeypatch, "candidates", lambda: candidates)
    history = [candidates[0].name]
    state = {"posted_meme_filenames": history, "unrelated": object()}
    save = Mock()
    monkeypatch.setattr(bot, "save_state", save)
    assert bot.choose_next_meme(state) is candidates[1]
    assert state["posted_meme_filenames"] is history
    history.extend(path.name for path in candidates[1:])
    monkeypatch.setattr(bot, "RESET_MEME_CYCLE_WHEN_ALL_POSTED", False)
    assert bot.choose_next_meme(state) is None
    save.assert_not_called()
    monkeypatch.setattr(bot, "RESET_MEME_CYCLE_WHEN_ALL_POSTED", True)
    failure = OSError("cycle save failed")
    save.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.choose_next_meme(state)
    assert caught.value is failure and state["posted_meme_filenames"] == []
    assert save.call_args.args[0] is state
    state["posted_meme_filenames"] = history
    save.side_effect = lambda current: None if current is state and current["posted_meme_filenames"] == [] else pytest.fail("save must see the caller's cleared history")
    assert bot.choose_next_meme(state) is candidates[0]


def test_fallback_current_calendar_and_posted_callback_preserve_short_circuit_at_dst(monkeypatch):
    target = datetime(2026, 3, 28, 16, tzinfo=ZoneInfo("Europe/London"))
    epoch = int(target.timestamp())
    state = {}
    calendar = Mock(side_effect=lambda value: datetime.fromtimestamp(value, target.tzinfo))
    posted = Mock(return_value=False)
    patch_schedule(monkeypatch, "datetime", calendar)
    patch_schedule(monkeypatch, "posted_on_date", posted)
    monkeypatch.setattr(bot, "MEME_FALLBACK_HOUR", 16)
    monkeypatch.setattr(bot, "MEME_FALLBACK_MINUTE", 0)
    assert bot.next_meme_fallback_epoch(state, epoch - 1) == epoch
    assert posted.call_args.args[0] is state
    posted.assert_called_once_with(state, "2026-03-28")
    posted.reset_mock()
    tomorrow = int((target + timedelta(days=1)).timestamp())
    assert tomorrow - epoch == 23 * 3600
    assert bot.next_meme_fallback_epoch(state, epoch) == tomorrow
    assert bot.next_meme_fallback_epoch(state, epoch + 1) == tomorrow
    posted.assert_not_called()
    posted.return_value = True
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch - 1)
    assert bot.next_meme_fallback_epoch(state) == tomorrow
    calendar.assert_called_with(epoch - 1)


def test_quote_trigger_draws_only_when_needed_and_saves_original_fields_in_order(monkeypatch):
    epoch = int(datetime(2026, 7, 6, 23, 30, tzinfo=ZoneInfo("Europe/London")).timestamp())
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", 3500)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS", 4500)
    draw = Mock(return_value=3600)
    monkeypatch.setattr(bot.random, "randint", draw)
    assert bot.meme_schedule_fields_after_quote_post({}, epoch - 13 * 3600) == {}
    assert bot.meme_schedule_fields_after_quote_post({"last_meme_post_epoch": epoch}, epoch) == {}
    fields = bot.meme_schedule_fields_after_quote_post({}, epoch, delay=3600)
    assert bot.meme_schedule_fields_after_quote_post(fields, epoch + 60) == {}
    with monkeypatch.context() as patch:
        patch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
        assert bot.meme_schedule_fields_after_quote_post({}, epoch) == {}
    draw.assert_not_called()
    assert bot.meme_schedule_fields_after_quote_post({}, epoch) == fields
    draw.assert_called_once_with(3500, 4500)
    assert fields["meme_anchor_quote_post_epoch"] == epoch
    assert fields["next_meme_schedule_date"] == "2026-07-06"
    assert bot.meme_schedule_date_str(fields["next_meme_post_epoch"]) == "2026-07-07"

    state, events = {}, []
    patch_schedule(monkeypatch, "fields_after_quote", lambda current, value: fields if current is state and value == epoch else pytest.fail("copied caller"))
    apply = bot.apply_state_fields

    def apply_fields(current, supplied):
        assert current is state and supplied is fields
        events.append("apply")
        apply(current, supplied)

    monkeypatch.setattr(meme, "apply_state_fields", apply_fields)
    monkeypatch.setattr(bot, "save_state", lambda current: events.append("save") if current is state and current == fields else pytest.fail("save before apply"))
    monkeypatch.setattr(bot, "log", Mock(info=lambda *args: events.append("log")))
    bot.maybe_schedule_meme_after_quote_post(state, epoch)
    assert events == ["apply", "save", "log"]
    events.clear()
    bot.maybe_schedule_meme_after_quote_post(state, epoch, save=False)
    assert events == ["apply", "log"]


def test_stage_and_id_validation_keep_current_callbacks_native_errors_and_event_order(monkeypatch):
    events, result = [], object()
    logger = Mock(error=lambda *args, **kwargs: events.append(("log", args, kwargs)))
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "log_event", lambda event, **fields: events.append((event, fields)))
    operation = Mock(return_value=result)
    assert bot.run_daily_meme_stage("selection", operation) is result
    operation.assert_called_once_with()
    assert events == []
    failure = ValueError("x" * 600)
    operation.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.run_daily_meme_stage("selection", operation)
    assert caught.value is failure
    assert events[0][0] == "log" and events[0][1][-1] is failure
    assert events[0][2] == {"exc_info": True}
    assert events[1] == ("daily_meme_failure", dict(status="failed", stage="selection", error_type="ValueError", reason="x" * 500))
    events.clear()
    operation.side_effect = KeyboardInterrupt("stop")
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.run_daily_meme_stage("selection", operation)
    assert caught.value is operation.side_effect and events == []

    identifier = object()
    validator = Mock(return_value=True)
    monkeypatch.setattr(bot, "valid_post_id", validator)
    assert bot.require_valid_meme_post_id(identifier) is None
    assert validator.call_args.args[0] is identifier
    validator.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.require_valid_meme_post_id(identifier)
    assert caught.value is failure


def test_posting_closures_keep_assets_prepared_transport_and_named_stage_order(tmp_path, monkeypatch):
    state, receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
    path = bot.MEME_DIR / "001_meme.png"
    item = {"grok_description": "Meme summary."}
    index = {path.name: item}
    stages, prepared, operations = [], {}, []
    original_stage = bot.run_daily_meme_stage
    original_prepare = bot.prepare_main_tweet_transport

    def stage(name, operation):
        stages.append(name)
        operations.append(operation)
        closure = inspect.getclosurevars(operation).nonlocals
        if "state" in closure:
            assert closure["state"] is state
        if "meme_path" in closure:
            assert closure["meme_path"] is path
        if "analysis_index" in closure:
            assert closure["analysis_index"] is index
        if name == "x_post_request":
            publication = closure["self"]
            assert publication.attempt is prepared["attempt"]
            assert publication.transport_authority is prepared["authority"]
            assert publication.transport_source is prepared["source"]
        return original_stage(name, operation)

    def prepare(attempt):
        result = original_prepare(attempt)
        prepared.update(zip(("attempt", "source", "authority"), result))
        return result

    def upload(filename, **kwargs):
        assert filename == str(path) and kwargs == {"lane": "daily_meme"}
        assert stages[-2:] == ["x_request_preparation", "media_upload"]
        return "media-1"

    def handoff(attempt, authority):
        assert attempt is prepared["attempt"] and authority is prepared["authority"]

    def create(**kwargs):
        assert kwargs["prepared_main_post_attempt"] is prepared["attempt"]
        assert kwargs["prepared_transport_authority"] is prepared["authority"]
        assert kwargs["prepared_transport_source"] is prepared["source"]
        assert kwargs["text"] == bot.MEME_POST_TEXT
        assert kwargs["media_ids"] == ["media-1"] and kwargs["reply_to_id"] is None
        assert kwargs["made_with_ai"] is False
        assert kwargs["prepared_main_post_attempt"]["recovery_plan"]["image_summary"] == "Meme summary."
        return mock_confirmed_main_post(kwargs, {"data": {"id": "970001"}})

    monkeypatch.setattr(bot, "run_daily_meme_stage", stage)
    patch_catalog(
        monkeypatch,
        "choose",
        lambda current: path if current is state else pytest.fail("copied state"),
    )
    monkeypatch.setattr(
        bot,
        "choose_next_meme",
        Mock(side_effect=AssertionError("catalog choice bounced through root")),
    )
    monkeypatch.setattr(
        bot,
        "build_meme_cache_summary",
        Mock(side_effect=AssertionError("catalog summary bounced through root")),
    )
    for name in (
        "meme_posted_on_date", "meme_schedule_date_str", "schedule_next_meme_post",
    ):
        monkeypatch.setattr(
            bot,
            name,
            Mock(side_effect=AssertionError(f"meme schedule bounced through {name}")),
        )
    monkeypatch.setattr(bot, "load_meme_analysis_index", lambda: index)
    monkeypatch.setattr(bot, "prepare_main_tweet_transport", prepare)
    monkeypatch.setattr(bot, "upload_media", upload)
    monkeypatch.setattr(bot, "handoff_confirmed_media_upload_to_main_attempt", handoff)
    monkeypatch.setattr(bot, "create_post", create)
    bot.post_next_meme(state)
    assert stages == [
        "remote_write_barrier", "receipt_barrier", "meme_receipt_reconciliation",
        "main_receipt_barrier", "meme_eligibility_and_asset_selection",
        "x_request_preparation", "x_request_preparation", "media_upload",
        "main_post_attempt_persistence", "tweet_transport_preparation",
        "media_upload_handoff", "x_post_request", "x_post_response_validation",
    ]
    assert operations[3] is bot.block_if_unresolved_regular_post_receipt
    assert operations[5] is bot.load_meme_analysis_index
    assert state["posted_meme_filenames"] == [path.name] and not receipt_path.exists()
