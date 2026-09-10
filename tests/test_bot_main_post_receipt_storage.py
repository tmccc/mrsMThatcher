from __future__ import annotations

import builtins
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_main_post_receipt_storage as storage
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_regular_post_receipt,  # noqa: F401
    schema_current_main_attempt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('receipt storage import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_main_post_receipt_storage':
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
import mrs_bot_main_post_receipt_storage
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


@pytest.mark.parametrize(
    "name, signature, dependency_count",
    [
        ("main_post_attempt_path", "(attempt: 'dict') -> 'Path'", 2),
        ("write_main_post_attempt", "(attempt: 'dict') -> 'None'", 11),
        ("mark_main_post_attempt_attempting", "(attempt: 'dict') -> 'dict'", 10),
        ("remove_main_post_attempt", "(attempt: 'dict', *, sending_disposition: 'str') -> 'None'", 7),
        ("finalize_confirmed_pending_schedule_receipt", "(pending: 'dict') -> 'dict'", 10),
        ("write_regular_post_receipt", "(receipt: 'dict') -> 'None'", 14),
        ("load_regular_post_receipt", "() -> 'tuple[str, dict | None]'", 6),
        ("remove_regular_post_receipt", "(receipt: 'dict') -> 'None'", 4),
        ("write_meme_post_receipt", "(receipt: 'dict') -> 'None'", 14),
        ("load_meme_post_receipt", "() -> 'tuple[str, dict | None]'", 6),
        ("remove_meme_post_receipt", "(receipt: 'dict') -> 'None'", 4),
    ],
)
def test_adapters_forward_current_dependencies_signatures_references_and_errors(
    monkeypatch, name, signature, dependency_count,
):
    adapter = getattr(bot, name)
    parameters = inspect.signature(adapter).parameters
    assert str(inspect.signature(adapter)) == signature
    dependencies = {
        key: parameter for key, parameter in
        inspect.signature(getattr(storage, name)).parameters.items()
        if key not in parameters
    }
    assert len(dependencies) == dependency_count
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY
               and parameter.default is inspect.Parameter.empty
               for parameter in dependencies.values())
    args = ({"original": []},) if any(
        p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in parameters.values()
    ) else ()
    options = {key: object() for key, p in parameters.items()
               if p.kind is inspect.Parameter.KEYWORD_ONLY}
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {key: object() for key in dependencies}
            owner = Mock(return_value={"original return": []})
            patch.setattr(bot, "_main_post_receipt_storage", SimpleNamespace(**{name: owner}))
            for key, dependency in current.items():
                patch.setattr(bot, key, dependency)
            assert adapter(*args, **options) is owner.return_value
            owner.assert_called_once_with(*args, **options, **current)
            assert all(actual is original for actual, original in zip(owner.call_args.args, args))
            assert all(owner.call_args.kwargs[key] is value
                       for key, value in {**options, **current}.items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def _callbacks(monkeypatch, **returns):
    trace = Mock()
    for name, value in returns.items():
        callback = Mock(return_value=value)
        trace.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    logger = Mock()
    trace.attach_mock(logger, "log")
    monkeypatch.setattr(bot, "log", logger)
    return trace


def _steps(trace):
    return [entry[0] for entry in trace.mock_calls]


def test_attempt_path_keeps_lane_conversion_and_current_path_references(monkeypatch):
    class Lane:
        def __str__(self):
            return "daily_meme"

    regular, meme = object(), object()
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", meme)
    assert bot.main_post_attempt_path({"lane": "quote_image"}) is regular
    assert bot.main_post_attempt_path({"lane": Lane()}) is meme
    with pytest.raises(ValueError, match="^Unsupported main-post attempt lane: $"):
        bot.main_post_attempt_path({"lane": None})


def test_sending_publication_orders_gates_and_wraps_only_exclusive_creation(monkeypatch):
    attempt = schema_current_main_attempt("daily_meme")
    path = bot.MEME_POST_RECEIPT_FILE
    trace = _callbacks(
        monkeypatch, current_main_post_attempt_is_semantically_valid=True,
        remote_receipt_retirement_is_blocking=False,
        receipt_namespace_entry_exists=False, main_post_attempt_path=path,
        durable_create_receipt_json=None,
    )
    assert bot.write_main_post_attempt(attempt) is None
    assert _steps(trace) == [
        "current_main_post_attempt_is_semantically_valid",
        "remote_receipt_retirement_is_blocking",
        *["receipt_namespace_entry_exists"] * 3,
        "main_post_attempt_path", "durable_create_receipt_json", "log.warning",
    ]
    assert trace.receipt_namespace_entry_exists.call_args_list == [
        call(bot.REGULAR_POST_RECEIPT_FILE), call(path), call(bot.CONFIRMED_REPLY_RECEIPT_FILE),
    ]
    assert trace.main_post_attempt_path.call_args.args[0] is attempt
    assert trace.durable_create_receipt_json.call_args.args == (path, attempt)
    assert trace.durable_create_receipt_json.call_args.args[1] is attempt

    trace.reset_mock()
    race = FileExistsError("publication race")
    trace.durable_create_receipt_json.side_effect = race
    with pytest.raises(bot.UnresolvedRegularPostReceipt) as caught:
        bot.write_main_post_attempt(attempt)
    assert caught.value.__cause__ is race
    trace.log.warning.assert_not_called()
    trace.durable_create_receipt_json.side_effect = None
    trace.log.warning.side_effect = race
    with pytest.raises(FileExistsError) as caught:
        bot.write_main_post_attempt(attempt)
    assert caught.value is race


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_attempting_promotion_keeps_path_equality_shallow_copy_and_authority_order(monkeypatch, lane):
    attempt = schema_current_main_attempt(lane)
    # Loader selection follows the current path callback, independently of lane.
    prefix = "meme" if lane == "quote_image" else "regular"
    path = Path(str(getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE")))
    loader = f"load_{prefix}_post_receipt"
    trace = _callbacks(
        monkeypatch, current_main_post_attempt_is_semantically_valid=True,
        main_post_attempt_path=path, canonical_atomic_json_bytes=None,
        transaction_mutation_authority=object(), replace_exact_source_receipt_document=None,
        **{loader: ("sending", dict(attempt))},
    )
    trace.canonical_atomic_json_bytes.side_effect = [b"sending", b"attempting"]
    result = bot.mark_main_post_attempt_attempting(attempt)
    assert result is not attempt and result["lifecycle_state"] == "attempting"
    assert attempt["lifecycle_state"] == "sending"
    assert result["recovery_plan"] is attempt["recovery_plan"]
    assert result["media_ids"] is attempt["media_ids"]
    assert _steps(trace) == [
        "current_main_post_attempt_is_semantically_valid", "main_post_attempt_path", loader,
        "current_main_post_attempt_is_semantically_valid", "canonical_atomic_json_bytes",
        "canonical_atomic_json_bytes", "transaction_mutation_authority",
        "replace_exact_source_receipt_document", "log.warning",
    ]
    for callback in (trace.current_main_post_attempt_is_semantically_valid, trace.canonical_atomic_json_bytes):
        assert callback.call_args_list[0].args[0] is attempt
        assert callback.call_args_list[1].args[0] is result
    trace.transaction_mutation_authority.assert_called_once_with(
        "main-post sending-to-attempting receipt promotion"
    )
    trace.replace_exact_source_receipt_document.assert_called_once_with(
        path, expected_bytes=b"sending", replacement_bytes=b"attempting",
        mutation_authority=trace.transaction_mutation_authority.return_value,
    )
    for changed in (("valid", attempt), ("sending", {**attempt, "attempt_id": "changed"})):
        trace.reset_mock()
        getattr(trace, loader).return_value = changed
        with pytest.raises(bot.AmbiguousRemotePostOutcome, match="changed before transmission"):
            bot.mark_main_post_attempt_attempting(attempt)
        assert _steps(trace) == ["current_main_post_attempt_is_semantically_valid", "main_post_attempt_path", loader]


@pytest.mark.parametrize("prefix,lane", [("regular", "quote_image"), ("meme", "daily_meme")])
def test_pending_finalization_orders_read_materialization_publication_and_original_return(monkeypatch, prefix, lane):
    pending = {"source_attempt": {"lane": lane}, "post_id": "confirmed"}
    receipt = {"materialized": []}
    path = getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE")
    loader, materializer, writer = (
        f"load_{prefix}_post_receipt", f"materialize_bound_{prefix}_schedule_receipt",
        f"write_{prefix}_post_receipt",
    )
    trace = _callbacks(
        monkeypatch, confirmed_pending_schedule_receipt_is_semantically_valid=True,
        main_post_attempt_path=path,
        **{loader: ("pending_schedule", dict(pending)), materializer: receipt, writer: None},
    )
    expected = ["confirmed_pending_schedule_receipt_is_semantically_valid", "main_post_attempt_path", loader, materializer, writer, "log.warning"]
    assert bot.finalize_confirmed_pending_schedule_receipt(pending) is receipt
    assert _steps(trace) == expected
    assert trace.main_post_attempt_path.call_args.args[0] is pending["source_attempt"]
    assert getattr(trace, materializer).call_args.args[0] is pending
    assert getattr(trace, writer).call_args.args[0] is receipt
    trace.reset_mock()
    getattr(trace, loader).return_value = ("pending_schedule", {**pending, "post_id": "changed"})
    with pytest.raises(RuntimeError, match="changed before finalisation"):
        bot.finalize_confirmed_pending_schedule_receipt(pending)
    assert _steps(trace) == expected[:3]
    getattr(trace, loader).return_value = ("pending_schedule", pending)
    for boundary in (materializer, writer):
        trace.reset_mock()
        failure = OSError(boundary)
        getattr(trace, boundary).side_effect = failure
        with pytest.raises(OSError) as caught:
            bot.finalize_confirmed_pending_schedule_receipt(pending)
        assert caught.value is failure
        assert _steps(trace) == expected[:expected.index(boundary) + 1]
        getattr(trace, boundary).side_effect = None


@pytest.mark.parametrize("prefix,lane", [("regular", "quote_image"), ("meme", "daily_meme")])
@pytest.mark.parametrize("branch", ["pending", "finalize", "legacy", "absent"])
def test_lane_writers_keep_ordered_publication_branches(monkeypatch, prefix, lane, branch):
    path = getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE")
    opposite = bot.MEME_POST_RECEIPT_FILE if prefix == "regular" else bot.REGULAR_POST_RECEIPT_FILE
    source = {"schema_version": 3 if prefix == "regular" else 2}
    receipt = {"source_attempt": source, "post_id": "confirmed"}
    stored = {"durable plan": []} if branch == "finalize" else dict(source)
    loader, semantic, materializer = (
        f"load_{prefix}_post_receipt", f"{prefix}_post_receipt_is_semantically_valid",
        f"materialize_bound_{prefix}_schedule_receipt",
    )
    trace = _callbacks(
        monkeypatch, remote_receipt_retirement_is_blocking=False,
        receipt_namespace_entry_exists=False,
        confirmed_pending_schedule_receipt_is_semantically_valid=branch == "pending",
        confirmed_receipt_matches_main_attempt=True, atomic_write_json=None,
        durable_create_receipt_json=None,
        **{loader: ("pending_schedule" if branch == "finalize" else "sending", stored),
           semantic: True, materializer: dict(receipt)},
    )
    trace.receipt_namespace_entry_exists.side_effect = [False, branch != "absent"]
    assert getattr(bot, f"write_{prefix}_post_receipt")(receipt) is None
    expected = ["remote_receipt_retirement_is_blocking", "receipt_namespace_entry_exists", "confirmed_pending_schedule_receipt_is_semantically_valid"]
    if branch != "pending":
        expected += [semantic, "receipt_namespace_entry_exists"]
    if branch != "absent":
        expected += [loader]
    if branch == "finalize":
        expected += [materializer]
        assert getattr(trace, materializer).call_args.args[0] is stored
    if branch == "legacy":
        expected += ["confirmed_receipt_matches_main_attempt"]
        assert trace.confirmed_receipt_matches_main_attempt.call_args.args[0] is receipt
        assert trace.confirmed_receipt_matches_main_attempt.call_args.args[1] is stored
    publication = "durable_create_receipt_json" if branch == "absent" else "atomic_write_json"
    expected += [publication, "log.warning"]
    assert _steps(trace) == expected
    assert trace.receipt_namespace_entry_exists.call_args_list == (
        [call(opposite)] if branch == "pending" else [call(opposite), call(path)]
    )
    trace.confirmed_pending_schedule_receipt_is_semantically_valid.assert_called_once_with(receipt, expected_lane=lane)
    published = getattr(trace, publication).call_args
    assert published.args[0] is path and published.args[1] is receipt
    assert published.kwargs == ({} if branch == "absent" else {"durable": True})
    messages = {
        "pending": (
            f"Wrote confirmed {prefix} pending-schedule receipt post_id=%s path=%s",
            "confirmed", path,
        ),
        "finalize": (
            f"Finalised {prefix}-post pending schedule post_id=%s path=%s",
            "confirmed", path,
        ),
        "legacy": (
            f"Promoted {prefix}-post sending receipt to confirmed attempt_id=%s "
            "post_id=%s path=%s",
            None, "confirmed", path,
        ),
        "absent": (
            f"Wrote confirmed {prefix}-post receipt pending local reconciliation: %s",
            path,
        ),
    }
    trace.log.warning.assert_called_once_with(*messages[branch])

    # Only exclusive creation translates FileExistsError. Validators, loaders,
    # matching/materialization and replacement retain their native failures.
    boundaries = ["confirmed_pending_schedule_receipt_is_semantically_valid"]
    if branch != "pending":
        boundaries += [semantic]
    if branch != "absent":
        boundaries += [loader]
    if branch == "finalize":
        boundaries += [materializer]
    if branch == "legacy":
        boundaries += ["confirmed_receipt_matches_main_attempt"]
    if branch != "absent":
        boundaries += ["atomic_write_json"]
    for boundary in boundaries:
        trace.reset_mock()
        trace.receipt_namespace_entry_exists.side_effect = [False, branch != "absent"]
        failure = FileExistsError(boundary)
        getattr(trace, boundary).side_effect = failure
        with pytest.raises(FileExistsError) as caught:
            getattr(bot, f"write_{prefix}_post_receipt")(receipt)
        assert caught.value is failure
        assert _steps(trace) == expected[:expected.index(boundary) + 1]
        trace.log.warning.assert_not_called()
        getattr(trace, boundary).side_effect = None


@pytest.mark.parametrize(
    "prefix,schema_version,must_stage",
    [
        ("regular", 3, False),
        ("regular", 4, True),
        ("regular", 5, True),
        ("regular", 6, True),
        ("meme", 2, False),
        ("meme", 3, True),
        ("meme", 4, True),
        ("meme", 5, True),
        ("meme", 6, False),
    ],
)
def test_lane_current_schema_gate_precedes_legacy_matching(
    monkeypatch, prefix, schema_version, must_stage,
):
    receipt = {"post_id": "confirmed"}
    attempt = {"schema_version": schema_version}
    loader, semantic = (
        f"load_{prefix}_post_receipt", f"{prefix}_post_receipt_is_semantically_valid",
    )
    trace = _callbacks(
        monkeypatch, remote_receipt_retirement_is_blocking=False,
        receipt_namespace_entry_exists=False,
        confirmed_pending_schedule_receipt_is_semantically_valid=False,
        confirmed_receipt_matches_main_attempt=True,
        atomic_write_json=None, durable_create_receipt_json=None,
        **{loader: ("sending", attempt), semantic: True},
    )
    trace.receipt_namespace_entry_exists.side_effect = [False, True]
    writer = getattr(bot, f"write_{prefix}_post_receipt")
    expected = [
        "remote_receipt_retirement_is_blocking", "receipt_namespace_entry_exists",
        "confirmed_pending_schedule_receipt_is_semantically_valid", semantic,
        "receipt_namespace_entry_exists", loader,
    ]
    if must_stage:
        own_error = getattr(bot, f"Unresolved{prefix.title()}PostReceipt")
        with pytest.raises(own_error, match="durable confirmed pending-schedule receipt"):
            writer(receipt)
        assert _steps(trace) == expected
    else:
        writer(receipt)
        assert _steps(trace) == expected + [
            "confirmed_receipt_matches_main_attempt", "atomic_write_json", "log.warning",
        ]
        trace.confirmed_receipt_matches_main_attempt.assert_called_once_with(receipt, attempt)
        trace.atomic_write_json.assert_called_once_with(
            getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE"), receipt, durable=True,
        )
    trace.durable_create_receipt_json.assert_not_called()


@pytest.mark.parametrize("prefix", ["regular", "meme"])
@pytest.mark.parametrize(
    "scenario,message",
    [
        ("pending_changed", "changed .* main-post attempt"),
        ("pending_wrong_state", "changed .* main-post attempt"),
        ("invalid_receipt", "failed semantic validation"),
        ("finalize_mismatch", "does not match the durable confirmed .* plan"),
        ("empty_pending", "unresolved .*post receipt"),
        ("legacy_mismatch", "unresolved .*post receipt"),
    ],
)
def test_lane_writers_reject_changed_receipts_before_publication(
    monkeypatch, prefix, scenario, message,
):
    source = {"schema_version": 3 if prefix == "regular" else 2}
    receipt = {"source_attempt": source, "post_id": "confirmed"}
    stored = {
        "pending_changed": ("sending", {**source, "attempt_id": "changed"}),
        "pending_wrong_state": ("attempting", dict(source)),
        "invalid_receipt": ("sending", dict(source)),
        "finalize_mismatch": ("pending_schedule", {"durable plan": []}),
        "empty_pending": ("pending_schedule", None),
        "legacy_mismatch": ("sending", dict(source)),
    }[scenario]
    pending = scenario in {"pending_changed", "pending_wrong_state"}
    loader, semantic, materializer = (
        f"load_{prefix}_post_receipt", f"{prefix}_post_receipt_is_semantically_valid",
        f"materialize_bound_{prefix}_schedule_receipt",
    )
    trace = _callbacks(
        monkeypatch, remote_receipt_retirement_is_blocking=False,
        receipt_namespace_entry_exists=False,
        confirmed_pending_schedule_receipt_is_semantically_valid=pending,
        confirmed_receipt_matches_main_attempt=False,
        atomic_write_json=None, durable_create_receipt_json=None,
        **{loader: stored, semantic: scenario != "invalid_receipt",
           materializer: {"post_id": "changed"}},
    )
    trace.receipt_namespace_entry_exists.side_effect = [False, True]
    own_error = getattr(bot, f"Unresolved{prefix.title()}PostReceipt")
    error = RuntimeError if scenario == "invalid_receipt" else own_error
    with pytest.raises(error, match=message):
        getattr(bot, f"write_{prefix}_post_receipt")(receipt)
    expected = [
        "remote_receipt_retirement_is_blocking", "receipt_namespace_entry_exists",
        "confirmed_pending_schedule_receipt_is_semantically_valid",
    ]
    if not pending:
        expected += [semantic]
        if scenario != "invalid_receipt":
            expected += ["receipt_namespace_entry_exists"]
    if scenario != "invalid_receipt":
        expected += [loader]
    if scenario == "finalize_mismatch":
        expected += [materializer]
    if scenario == "legacy_mismatch":
        expected += ["confirmed_receipt_matches_main_attempt"]
    assert _steps(trace) == expected
    trace.atomic_write_json.assert_not_called()
    trace.durable_create_receipt_json.assert_not_called()
    trace.log.warning.assert_not_called()


@pytest.mark.parametrize("prefix", ["regular", "meme"])
def test_lane_publication_gates_precede_validation_and_keep_creation_error_scope(monkeypatch, prefix):
    own_error = bot.UnresolvedRegularPostReceipt if prefix == "regular" else bot.UnresolvedMemePostReceipt
    opposite_error = bot.UnresolvedMemePostReceipt if prefix == "regular" else bot.UnresolvedRegularPostReceipt
    writer = getattr(bot, f"write_{prefix}_post_receipt")
    trace = _callbacks(
        monkeypatch, remote_receipt_retirement_is_blocking=True,
        receipt_namespace_entry_exists=True,
        confirmed_pending_schedule_receipt_is_semantically_valid=False,
        durable_create_receipt_json=None,
        **{f"{prefix}_post_receipt_is_semantically_valid": True},
    )
    with pytest.raises(own_error, match="source-receipt retirement"):
        writer({})
    assert _steps(trace) == ["remote_receipt_retirement_is_blocking"]
    trace.reset_mock()
    trace.remote_receipt_retirement_is_blocking.return_value = False
    with pytest.raises(opposite_error, match="unresolved"):
        writer({})
    assert _steps(trace) == ["remote_receipt_retirement_is_blocking", "receipt_namespace_entry_exists"]
    trace.receipt_namespace_entry_exists.return_value = False
    for failure in (FileExistsError("race"), OSError("durability failure")):
        trace.reset_mock()
        trace.durable_create_receipt_json.side_effect = failure
        with pytest.raises(own_error if isinstance(failure, FileExistsError) else OSError) as caught:
            writer({})
        assert (caught.value.__cause__ if isinstance(failure, FileExistsError) else caught.value) is failure
        trace.log.warning.assert_not_called()
    trace.durable_create_receipt_json.side_effect = None
    failure = FileExistsError("logging failure")
    trace.log.warning.side_effect = failure
    with pytest.raises(FileExistsError) as caught:
        writer({})
    assert caught.value is failure


@pytest.mark.parametrize("prefix,lane", [("regular", "quote_image"), ("meme", "daily_meme")])
def test_loaders_keep_read_only_catch_status_references_and_lane_specific_checks(monkeypatch, prefix, lane):
    loader = getattr(bot, f"load_{prefix}_post_receipt")
    semantic = f"{prefix}_post_receipt_is_semantically_valid"
    data = {"lane": lane}
    trace = _callbacks(
        monkeypatch, load_receipt_json_no_follow=(True, data),
        main_post_attempt_is_semantically_valid=True,
        confirmed_pending_schedule_receipt_is_semantically_valid=True,
        **{semantic: True},
    )
    path = getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE")
    trace.load_receipt_json_no_follow.side_effect = ValueError("read failed")
    assert loader() == ("invalid", None)
    assert _steps(trace) == ["load_receipt_json_no_follow", "log.exception"]
    trace.load_receipt_json_no_follow.assert_called_once_with(path)
    trace.reset_mock()
    failure = KeyboardInterrupt()
    trace.load_receipt_json_no_follow.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        loader()
    assert caught.value is failure and _steps(trace) == ["load_receipt_json_no_follow"]
    trace.load_receipt_json_no_follow.side_effect = None
    trace.reset_mock()
    trace.load_receipt_json_no_follow.return_value = (False, data)
    assert loader() == ("absent", None)
    assert _steps(trace) == ["load_receipt_json_no_follow"]
    trace.load_receipt_json_no_follow.return_value = (True, data)
    status, loaded = loader()
    assert status == "sending" and loaded is data
    data["lane"] = "daily_meme" if lane == "quote_image" else "quote_image"
    status, loaded = loader()
    assert status == "invalid" and loaded is data
    trace.main_post_attempt_is_semantically_valid.return_value = False
    trace.reset_mock()
    status, loaded = loader()
    assert status == "pending_schedule" and loaded is data
    assert _steps(trace) == ["load_receipt_json_no_follow", "main_post_attempt_is_semantically_valid", "confirmed_pending_schedule_receipt_is_semantically_valid"]
    trace.confirmed_pending_schedule_receipt_is_semantically_valid.assert_called_once_with(data, expected_lane=lane)
    trace.confirmed_pending_schedule_receipt_is_semantically_valid.return_value = False
    data.update(schema_version=1, post_id="confirmed", quote_hash="quote", image_basename="image",
                quote_post_epoch=1, next_quote_post_epoch=2, meme_basename="meme",
                meme_post_epoch=1, next_meme_post_epoch=2)
    status, loaded = loader()
    assert status == "valid" and loaded is data
    getattr(trace, semantic).return_value = False
    assert loader() == ("invalid", None)
    failure = TypeError("validator failed outside read catch")
    getattr(trace, semantic).side_effect = failure
    trace.reset_mock()
    with pytest.raises(TypeError) as caught:
        loader()
    assert caught.value is failure
    trace.log.assert_not_called()
    assert not trace.log.mock_calls
    getattr(trace, semantic).side_effect = None
    getattr(trace, semantic).return_value = True
    # The regular reader has an exact int check; the meme reader uses membership.
    data["schema_version"] = True
    trace.reset_mock()
    assert loader() == (("invalid", None) if prefix == "regular" else ("valid", data))
    assert getattr(trace, semantic).call_count == (0 if prefix == "regular" else 1)
    data["schema_version"] = 1
    data.pop("quote_hash")
    trace.reset_mock()
    assert loader() == (("invalid", None) if prefix == "regular" else ("valid", data))
    assert getattr(trace, semantic).call_count == (0 if prefix == "regular" else 1)


def test_attempt_retirement_keeps_disposition_reader_identity_and_wide_missing_scope(monkeypatch, tmp_path):
    attempt = schema_current_main_attempt("quote_image")
    current = dict(attempt)
    path = tmp_path / "attempt.json"
    path.write_text("{}", encoding="utf-8")
    trace = _callbacks(
        monkeypatch, current_main_post_attempt_is_semantically_valid=True,
        main_post_attempt_path=path, canonical_atomic_json_bytes=b"canonical",
        retire_current_source_receipt=None,
    )
    json_reader = Mock(return_value=current)
    trace.attach_mock(json_reader, "read_json")
    monkeypatch.setattr(bot, "json", SimpleNamespace(load=json_reader))
    handles = []

    def recorded_open(*args, **kwargs):
        trace.open(*args, **kwargs)
        handle = builtins.open(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(storage, "open", recorded_open, raising=False)
    with pytest.raises(ValueError, match="explicit disposition"):
        bot.remove_main_post_attempt(attempt, sending_disposition="unproved")
    assert not trace.mock_calls
    assert bot.remove_main_post_attempt(attempt, sending_disposition="definite_non_success") is None
    expected = ["current_main_post_attempt_is_semantically_valid", "main_post_attempt_path", "open", "read_json", "current_main_post_attempt_is_semantically_valid", "canonical_atomic_json_bytes", "retire_current_source_receipt", "log.info"]
    assert _steps(trace) == expected
    trace.open.assert_called_once_with(path, "r", encoding="utf-8")
    assert json_reader.call_args.args[0] is handles[0] and handles[0].closed
    assert trace.current_main_post_attempt_is_semantically_valid.call_args_list[0].args[0] is attempt
    assert trace.current_main_post_attempt_is_semantically_valid.call_args_list[1].args[0] is current
    assert trace.canonical_atomic_json_bytes.call_args.args[0] is attempt
    trace.retire_current_source_receipt.assert_called_once_with(path, b"canonical")
    trace.reset_mock()
    failure = FileNotFoundError("logger inside original retirement try")
    trace.log.info.side_effect = failure
    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.remove_main_post_attempt(attempt, sending_disposition="confirmed_state_fallback")
    assert caught.value.__cause__ is failure and _steps(trace) == expected
    trace.reset_mock()
    failure = ValueError("malformed JSON")
    json_reader.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.remove_main_post_attempt(attempt, sending_disposition="definite_non_success")
    assert caught.value is failure and _steps(trace) == expected[:4]
    json_reader.side_effect = None
    json_reader.return_value = {**attempt, "attempt_id": "changed"}
    trace.reset_mock()
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="changed main-post"):
        bot.remove_main_post_attempt(attempt, sending_disposition="definite_non_success")
    assert _steps(trace) == expected[:4]


@pytest.mark.parametrize("prefix", ["regular", "meme"])
def test_reconciled_retirement_uses_original_canonical_bytes_before_logging(monkeypatch, prefix):
    receipt = {"original": []}
    path = getattr(bot, f"{prefix.upper()}_POST_RECEIPT_FILE")
    trace = _callbacks(monkeypatch, canonical_atomic_json_bytes=b"canonical", retire_current_source_receipt=None)
    remove = getattr(bot, f"remove_{prefix}_post_receipt")
    assert remove(receipt) is None
    assert _steps(trace) == ["canonical_atomic_json_bytes", "retire_current_source_receipt", "log.info"]
    assert trace.canonical_atomic_json_bytes.call_args.args[0] is receipt
    trace.retire_current_source_receipt.assert_called_once_with(path, b"canonical")
    trace.reset_mock()
    failure = OSError("retirement uncertain")
    trace.retire_current_source_receipt.side_effect = failure
    with pytest.raises(OSError) as caught:
        remove(receipt)
    assert caught.value is failure
    assert _steps(trace) == ["canonical_atomic_json_bytes", "retire_current_source_receipt"]
