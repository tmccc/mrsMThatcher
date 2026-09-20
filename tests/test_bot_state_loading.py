from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_state_loading as loading
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, hashlib, io, json, logging, os, random, re, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state loading import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_state_loading', 'mrs_bot_observability'}:
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
import mrs_bot_state_loading
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


def test_adapter_forwards_all_current_dependencies_and_original_return(monkeypatch):
    dependencies = (
        "STATE_BACKUP_COUNT", "STATE_FILE", "STATE_MINIMUM_READER_VERSION",
        "STATE_PREVIOUS_READER_COMPATIBILITY_FENCES", "STATE_READER_COMPATIBILITY_FENCE",
        "UnsafeDurableStateNamespace", "default_state", "log", "log_event",
        "log_json_debug", "normalise_state_candidate", "read_stable_owned_json_bytes_no_follow",
        "require_compatible_state_reader", "save_state",
    )
    adapter = bot.load_state
    assert str(inspect.signature(adapter)) == "() -> 'dict'"
    assert tuple(inspect.signature(loading.load_state).parameters) == dependencies
    monkeypatch.setattr(bot, "Path", object())  # Postponed/local annotations only.
    for _ in range(2):
        current = {key: object() for key in dependencies}
        owner = Mock(return_value={"original": []})
        monkeypatch.setattr(bot, "_state_loading", SimpleNamespace(load_state=owner))
        for key, value in current.items():
            monkeypatch.setattr(bot, key, value)
        assert adapter() is owner.return_value
        owner.assert_called_once_with(**current)
        assert all(owner.call_args.kwargs[key] is value for key, value in current.items())
    failure = TypeError("current owner failure")
    owner.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter()
    assert caught.value is failure


def _legacy(path, **overrides):
    candidate = {"minimum_reader_version": 4, "pending_reply_drafts": {"__mrs_state_reader_compatibility_fence__": 4}}
    candidate.update(overrides)
    bot.atomic_write_json(path, candidate)
    return candidate


def test_equal_legacy_pair_migrates_once_retaining_extension_and_exact_generation(monkeypatch):
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    primary = bot.STATE_FILE
    backup = primary.with_name(primary.name + ".bak1")
    _legacy(primary, extension={"preserved": [1]}, last_seen_mention_id="99")
    backup.write_bytes(primary.read_bytes())
    backup.chmod(0o600)
    save = Mock(wraps=bot.save_state)
    monkeypatch.setattr(bot, "save_state", save)
    loaded = bot.load_state()
    assert loaded["extension"] == {"preserved": [1]}
    assert loaded["last_seen_mention_id"] == "99"
    save.assert_called_once_with(loaded, durable=True)
    assert json.loads(primary.read_bytes())["_state_generation"] == loaded["_state_generation"]
    assert primary.read_bytes() == backup.read_bytes()
    save.reset_mock()
    assert bot.load_state() == loaded
    save.assert_not_called()


def test_loader_scans_every_replica_before_choosing_provably_latest(monkeypatch):
    from mrs_bot_state_generation import encode_generation
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)
    for index, sequence in ((0, 2), (1, 1), (2, 5), (3, 3)):
        target = bot.STATE_FILE if index == 0 else bot.STATE_FILE.with_name(bot.STATE_FILE.name + f".bak{index}")
        doc, _ = encode_generation(bot.state_document_for_persistence({"selected_sequence": sequence}), sequence, bot.DURABLE_RUNTIME_JSON_MAX_BYTES)
        bot.atomic_write_json(target, doc)
    loaded = bot.load_state()
    assert loaded["selected_sequence"] == 5
    assert loaded["_state_generation"]["sequence"] == 6
    assert bot.load_state() == loaded


def test_absent_state_returns_default_without_writing(monkeypatch):
    default, save = Mock(return_value={"extension": []}), Mock()
    monkeypatch.setattr(bot, "default_state", default)
    monkeypatch.setattr(bot, "save_state", save)
    assert bot.load_state() is default.return_value
    save.assert_not_called()
    assert not bot.STATE_FILE.exists()


def test_invalid_documents_never_become_default_state(monkeypatch):
    bot.STATE_FILE.write_bytes(b"{invalid json")
    bot.STATE_FILE.chmod(0o600)
    default = Mock()
    monkeypatch.setattr(bot, "default_state", default)
    with pytest.raises(RuntimeError):
        bot.load_state()
    default.assert_not_called()


@pytest.mark.parametrize("drafts", [{"unretired": True}, {"__mrs_state_reader_compatibility_fence__": 999}])
def test_unretired_legacy_drafts_refuse_fallback_before_state_mutation(monkeypatch, drafts):
    _legacy(bot.STATE_FILE, pending_reply_drafts=drafts)
    backup = bot.STATE_FILE.with_name(bot.STATE_FILE.name + ".bak1")
    _legacy(backup)
    before = {path: path.read_bytes() for path in (bot.STATE_FILE, backup)}
    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts"):
        bot.load_state()
    assert {path: path.read_bytes() for path in before} == before


def test_unsafe_latest_namespace_refuses_even_usable_primary(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.save_state({})
    target = tmp_path / "outside.json"
    target.write_text("{}")
    backup = bot.STATE_FILE.with_name(bot.STATE_FILE.name + ".bak1")
    backup.unlink(missing_ok=True)
    backup.symlink_to(target)
    with pytest.raises(bot.UnsafeDurableStateNamespace):
        bot.load_state()
    assert target.read_text() == "{}"


@pytest.mark.parametrize("boundary,error_type", [
    ("read_stable_owned_json_bytes_no_follow", OSError),
    ("require_compatible_state_reader", bot.IncompatibleStateReaderError),
    ("normalise_state_candidate", ValueError),
])
def test_native_reader_and_validator_failures_never_trigger_fallback(monkeypatch, boundary, error_type):
    _legacy(bot.STATE_FILE)
    failure = error_type("native boundary failure")
    save = Mock()
    monkeypatch.setattr(bot, boundary, Mock(side_effect=failure))
    monkeypatch.setattr(bot, "save_state", save)
    with pytest.raises(error_type) as caught:
        bot.load_state()
    assert caught.value is failure
    save.assert_not_called()


def test_parser_baseexception_preserves_controlled_interrupt(monkeypatch):
    import mrs_bot_state_generation as generations
    _legacy(bot.STATE_FILE)
    failure = KeyboardInterrupt("retained signal")
    monkeypatch.setattr(generations, "strict_document", Mock(side_effect=failure))
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.load_state()
    assert caught.value is failure


def test_legacy_divergence_precedes_recovery_events_and_repair_save(monkeypatch):
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    _legacy(bot.STATE_FILE, extension=["primary"])
    backup = bot.STATE_FILE.with_name(bot.STATE_FILE.name + ".bak1")
    _legacy(backup, extension=["backup"])
    save, event = Mock(), Mock()
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "log_event", event)
    with pytest.raises(RuntimeError, match="refusing to guess"):
        bot.load_state()
    save.assert_not_called()
    event.assert_not_called()


def test_only_selected_candidate_recovery_events_are_published_before_migration(monkeypatch):
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    _legacy(bot.STATE_FILE)
    _legacy(bot.STATE_FILE.with_name(bot.STATE_FILE.name + ".bak1"))
    real_normalise = bot.normalise_state_candidate
    real_save = bot.save_state
    seen = []

    def normalise(candidate, **kwargs):
        events = kwargs.get("recovery_events")
        if events is not None:
            events.append({"reason": "selected" if kwargs["path"] == bot.STATE_FILE else "unselected"})
        return real_normalise(candidate, **kwargs)

    def save(candidate, **kwargs):
        seen.append("save")
        return real_save(candidate, **kwargs)

    monkeypatch.setattr(bot, "normalise_state_candidate", normalise)
    monkeypatch.setattr(bot, "log_event", lambda name, **kwargs: seen.append(kwargs["reason"]))
    monkeypatch.setattr(bot, "save_state", save)
    bot.load_state()
    assert seen == ["selected", "save"]


def test_migration_failure_precedes_post_load_maintenance(monkeypatch):
    _legacy(bot.STATE_FILE)
    failure = OSError("repair failed")
    maintenance = Mock()
    monkeypatch.setattr(bot, "save_state", Mock(side_effect=failure))
    monkeypatch.setattr(bot, "clear_expired_api_cooldowns", maintenance)
    monkeypatch.setattr(bot, "sanitize_next_reply_lane_priority", maintenance)
    with pytest.raises(OSError) as caught:
        bot.load_runtime_state()
    assert caught.value is failure
    maintenance.assert_not_called()


@pytest.mark.parametrize("repair_pending_identity", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
def test_generation_selection_has_one_collision_and_tie_rule_before_repairs(
    monkeypatch, repair_pending_identity, conflicting,
):
    from mrs_bot_state_generation import encode_generation

    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    paths = [bot.STATE_FILE] + [
        bot.STATE_FILE.with_name(f"{bot.STATE_FILE.name}.bak{i}") for i in (1, 2)
    ]
    documents = {}
    for index, path in enumerate(paths):
        state = bot.state_document_for_persistence({"extension": "different" if conflicting and index == 2 else "same"})
        _, data = encode_generation(state, 1 if index == 0 else 3, bot.DURABLE_RUNTIME_JSON_MAX_BYTES)
        documents[path] = data
    reads, normalized = [], {}

    def read_candidate(path):
        reads.append(path)
        return True, documents[path]

    def normalize(candidate, *, path, recovery_events, recover_pending_identity):
        if repair_pending_identity and not recover_pending_identity:
            return None
        if repair_pending_identity:
            recovery_events.append({"reason": "orphaned_pending_candidates", "discarded_candidates": 1})
        normalized[path] = candidate
        return candidate

    save, event = Mock(), Mock()
    monkeypatch.setattr(bot, "read_stable_owned_json_bytes_no_follow", read_candidate)
    monkeypatch.setattr(bot, "normalise_state_candidate", normalize)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "log_event", event)
    if conflicting:
        with pytest.raises(RuntimeError, match="^conflicting state generation identities; refusing to guess$"):
            bot.load_state()
        save.assert_not_called()
        event.assert_not_called()
    else:
        result = bot.load_state()
        assert result is normalized[paths[1]]
        save.assert_called_once_with(result, durable=True)
        assert event.call_count == int(repair_pending_identity)
    assert reads == paths * (2 if repair_pending_identity else 1)
