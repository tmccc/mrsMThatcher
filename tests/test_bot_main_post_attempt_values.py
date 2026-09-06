from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_main_post_attempt_values as values
from tests.test_unit_helpers import bot, isolate_regular_post_receipt, schema_current_main_attempt


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('attempt values import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_main_post_attempt_values':
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
import mrs_bot_main_post_attempt_values
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
    "name, dependencies",
    [
        ('canonical_remote_post_payload_sha256', ('hashlib', 'json')),
        ('bound_meme_schedule_state', ('MAIN_POST_SCHEDULE_TIMEZONE', 'MEME_SCHEDULE_VERSION', 'safe_bound_schedule_date_str')),
        ('bound_meme_schedule_state_is_valid', ('BOUND_MEME_SCHEDULE_STATE_KEYS', 'MAIN_POST_SCHEDULE_TIMEZONE', 'MEME_SCHEDULE_MODES', 'MEME_SCHEDULE_VERSION', 'safe_bound_schedule_date_str', 'valid_receipt_epoch')),
        ('engagement_experiment_attempt_envelope_is_valid', ('ENGAGEMENT_EXPERIMENT_ATTEMPT_FIELDS', 'engagement_question_trial', 'quote_text_hash', 're')),
        ('main_post_attempt_binds_payload', ('canonical_remote_post_payload_sha256', 'current_main_post_attempt_is_semantically_valid', 'main_post_attempt_payload')),
        ('current_main_post_attempt_is_semantically_valid', ('main_post_attempt_is_semantically_valid',)),
        ('build_main_post_attempt', ('MAIN_POST_SCHEDULE_TIMEZONE', 'canonical_remote_post_payload_sha256', 'copy', 'current_main_post_attempt_is_semantically_valid', 'hashlib', 'now_epoch', 'os')),
        ('confirmed_receipt_matches_main_attempt', ('main_post_attempt_is_semantically_valid',)),
        ('build_confirmed_pending_schedule_receipt', ('confirmed_pending_schedule_receipt_is_semantically_valid', 'copy', 'main_post_attempt_is_semantically_valid', 'valid_post_id', 'valid_receipt_epoch')),
        ('confirmation_epoch_for_main_attempt', ('log',)),
    ],
)
def test_adapters_forward_current_dependencies_signatures_references_and_errors(
    monkeypatch, name, dependencies,
):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    owner_signature = inspect.signature(getattr(values, name))
    owner_parameters = owner_signature.parameters
    assert tuple(key for key in owner_parameters if key not in signature.parameters) == dependencies
    assert signature == owner_signature.replace(parameters=[
        value for key, value in owner_parameters.items() if key not in dependencies
    ])
    assert all(owner_parameters[key].kind is inspect.Parameter.KEYWORD_ONLY
               and owner_parameters[key].default is inspect.Parameter.empty
               for key in dependencies)
    args = tuple(object() for p in signature.parameters.values()
                 if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD)
    options = {key: object() for key, p in signature.parameters.items()
               if p.kind is inspect.Parameter.KEYWORD_ONLY}
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {key: object() for key in dependencies}
            owner = Mock(return_value={"original": []})
            patch.setattr(bot, "_main_post_attempt_values", SimpleNamespace(**{name: owner}))
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


class _ObservedValue:
    def __init__(self, events, name, value):
        self.events, self.name, self.value = events, name, value

    def __bool__(self):
        self.events.append((self.name, "bool"))
        return bool(self.value)

    def __int__(self):
        self.events.append((self.name, "int"))
        return int(self.value)

    def __str__(self):
        self.events.append((self.name, "str"))
        return str(self.value)


def test_payload_hash_preserves_json_options_utf8_and_error_boundary(monkeypatch):
    payload = {"text": "café", "media": {"media_ids": ["9"]}}
    trace = Mock()
    trace.dumps.side_effect = json.dumps
    trace.sha256.return_value.hexdigest.return_value = object()
    monkeypatch.setattr(bot, "json", SimpleNamespace(dumps=trace.dumps))
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=trace.sha256))
    assert bot.canonical_remote_post_payload_sha256(payload) is trace.sha256.return_value.hexdigest.return_value
    assert trace.mock_calls == [
        call.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        call.sha256('{"media":{"media_ids":["9"]},"text":"café"}'.encode("utf-8")),
        call.sha256().hexdigest(),
    ]
    assert trace.dumps.call_args.args[0] is payload
    trace.reset_mock()
    failure = TypeError("not serializable")
    trace.dumps.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.canonical_remote_post_payload_sha256(payload)
    assert caught.value is failure
    trace.sha256.assert_not_called()


def test_payload_alias_keeps_coercion_order_list_gate_and_ai_identity():
    assert bot.main_post_attempt_payload is values.main_post_attempt_payload
    events = []
    class Attempt(dict):
        def get(self, key, *args):
            events.append(("get", key))
            return super().get(key, *args)
    attempt = Attempt(
        text=_ObservedValue(events, "text", "quote"), media_ids=[7, None],
        reply_to_id=_ObservedValue(events, "reply", 8), made_with_ai=1,
    )
    payload = bot.main_post_attempt_payload(attempt)
    assert payload == {"text": "quote", "media": {"media_ids": ["7", "None"]},
                       "reply": {"in_reply_to_tweet_id": "8"}}
    assert events == [
        ("get", "text"), ("text", "bool"), ("text", "str"),
        ("get", "media_ids"), ("get", "reply_to_id"), ("reply", "bool"),
        ("reply", "str"), ("get", "made_with_ai"),
    ]
    again = bot.main_post_attempt_payload(attempt)
    assert again is not payload and again["media"] is not payload["media"]
    assert again["media"]["media_ids"] is not payload["media"]["media_ids"]
    assert bot.main_post_attempt_payload({"media_ids": (7,), "made_with_ai": True}) == {"made_with_ai": True}
    assert bot.main_post_attempt_payload({"text": 0, "media_ids": [], "reply_to_id": 0}) == {}


def test_envelope_alias_returns_original_without_revalidation():
    assert bot.engagement_experiment_envelope_from_attempt is values.engagement_experiment_envelope_from_attempt
    envelope = {"deliberately incomplete": []}
    attempt = {"lane": "quote_image", "schema_version": 6,
               "engagement_question_experiment": envelope}
    assert bot.engagement_experiment_envelope_from_attempt(attempt) is envelope
    assert bot.engagement_experiment_envelope_from_attempt({**attempt, "schema_version": 6.0}) is envelope
    for other in (None, {**attempt, "lane": "daily_meme"},
                  {**attempt, "schema_version": 5},
                  {**attempt, "engagement_question_experiment": []}):
        assert bot.engagement_experiment_envelope_from_attempt(other) is None


def test_bound_snapshot_conversion_defaults_date_order_and_native_errors(monkeypatch):
    events = []
    state = {key: _ObservedValue(events, label, value) for key, label, value in [
        ("next_meme_post_epoch", "next", "20"),
        ("next_meme_schedule_mode", "mode", ""),
        ("next_meme_schedule_date", "date", "bound date"),
        ("meme_schedule_version", "version", 0),
        ("last_meme_post_epoch", "last", "5"),
        ("meme_anchor_quote_post_epoch", "anchor", "10"),
    ]}
    date = Mock(side_effect=lambda *args: events.append(("derive", *args)))
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", date)
    monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", "current timezone")
    monkeypatch.setattr(bot, "MEME_SCHEDULE_VERSION", 7)
    result = bot.bound_meme_schedule_state(state)
    assert events == [
        ("next", "bool"), ("next", "int"), ("mode", "bool"),
        ("date", "bool"), ("date", "str"), ("version", "bool"),
        ("derive", 20, "current timezone"), ("last", "bool"), ("last", "int"),
        ("anchor", "bool"), ("anchor", "int"),
    ]
    assert result["next_meme_schedule_date"] == "bound date"
    assert result["next_meme_schedule_mode"] == "fallback"
    assert result["meme_schedule_version"] == 7
    date.side_effect = None
    date.return_value = "derived date"
    assert bot.bound_meme_schedule_state({"next_meme_post_epoch": 20}, schedule_timezone="")["next_meme_schedule_date"] == "derived date"
    date.assert_called_with(20, "")
    date.return_value = None
    with pytest.raises(ValueError, match="meme schedule epoch has no valid calendar date"):
        bot.bound_meme_schedule_state({"next_meme_post_epoch": 20})
    date.reset_mock()
    assert bot.bound_meme_schedule_state({})["next_meme_post_epoch"] == 0
    with pytest.raises(ValueError):
        bot.bound_meme_schedule_state({"next_meme_post_epoch": "invalid"})
    date.assert_not_called()


def test_bound_validator_keeps_epoch_order_and_local_date_closure(monkeypatch):
    snapshot = bot.bound_meme_schedule_state({})
    snapshot.update(last_meme_post_epoch=5, next_meme_post_epoch=20,
                    meme_schedule_version=1, meme_anchor_quote_post_epoch=10,
                    next_meme_schedule_mode="after_first_quote_after_midday",
                    next_meme_schedule_date="anchor date")
    trace = Mock()
    trace.epoch.return_value = True
    trace.date.return_value = "anchor date"
    monkeypatch.setattr(bot, "valid_receipt_epoch", trace.epoch)
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", trace.date)
    monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", "current timezone")
    assert bot.bound_meme_schedule_state_is_valid(snapshot)
    assert trace.mock_calls == [call.epoch(20), call.epoch(5), call.epoch(10),
                                call.date(10, "current timezone")]
    trace.reset_mock()
    assert not bot.bound_meme_schedule_state_is_valid({**snapshot, "meme_schedule_version": True})
    assert trace.mock_calls == []
    failure = ValueError("date callback failure")
    trace.date.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.bound_meme_schedule_state_is_valid(snapshot, schedule_timezone="")
    assert caught.value is failure
    trace.date.assert_called_once_with(10, "")


@pytest.mark.parametrize("arm, public", [("control", "quote"), ("treatment", "complete")])
def test_experiment_validation_order_reference_and_narrow_error_scope(monkeypatch, arm, public):
    trace = Mock()
    trial = trace.trial
    error_type = bot.engagement_question_trial.ExperimentValidationError
    trial.ExperimentValidationError = error_type
    trial.MAX_ROOT_WEIGHTED_LENGTH = 280
    trial.complete_treatment_text.return_value = "complete"
    trial.sha256_text.side_effect = {"quote": "quote hash", "question": "question hash", "complete": "a" * 64}.__getitem__
    trial.x_weighted_length.return_value = 8
    trace.quote_hash.return_value = "quote hash"
    trace.regex.return_value = True
    monkeypatch.setattr(bot, "engagement_question_trial", trial)
    monkeypatch.setattr(bot, "quote_text_hash", trace.quote_hash)
    monkeypatch.setattr(bot, "re", SimpleNamespace(fullmatch=trace.regex))
    binding = {"approved_question_sha256": "question hash", "arm": arm}
    envelope = {"canonical_quote_text": "quote", "approved_question_body": "question",
                "complete_treatment_sha256": "a" * 64,
                "complete_treatment_weighted_length": 8, "binding": binding}
    plan = {"original": []}
    options = dict(public_text=public, quote_hash="quote hash", plan=plan)
    assert bot.engagement_experiment_attempt_envelope_is_valid(envelope, **options)
    assert trace.mock_calls == [
        call.regex(r"[0-9a-f]{64}", "a" * 64),
        call.trial.validate_attempt_binding(binding, plan=plan, exact_quote_text="quote", public_text=public),
        call.trial.complete_treatment_text("quote", "question"),
        call.trial.sha256_text("quote"), call.quote_hash("quote"),
        call.trial.sha256_text("question"), call.trial.sha256_text("complete"),
        call.trial.x_weighted_length("complete"),
    ]
    assert trial.validate_attempt_binding.call_args.args[0] is binding
    assert trial.validate_attempt_binding.call_args.kwargs["plan"] is plan
    for error in (KeyError("binding"), TypeError("binding"), error_type("binding")):
        trial.validate_attempt_binding.side_effect = error
        assert not bot.engagement_experiment_attempt_envelope_is_valid(envelope, **options)
    failure = ValueError("uncaught binding failure")
    trial.validate_attempt_binding.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.engagement_experiment_attempt_envelope_is_valid(envelope, **options)
    assert caught.value is failure
    trace.regex.side_effect = KeyError("outside try")
    with pytest.raises(KeyError, match="outside try"):
        bot.engagement_experiment_attempt_envelope_is_valid(envelope, **options)


def test_writable_payload_and_matching_predicates_preserve_short_circuits(monkeypatch):
    full = Mock(return_value=False)
    monkeypatch.setattr(bot, "main_post_attempt_is_semantically_valid", full)
    foreign = object()
    assert not bot.current_main_post_attempt_is_semantically_valid(foreign)
    full.assert_called_once_with(foreign)
    assert not bot.confirmed_receipt_matches_main_attempt(foreign, foreign)
    full.return_value = True
    assert not bot.current_main_post_attempt_is_semantically_valid(foreign)
    assert not bot.confirmed_receipt_matches_main_attempt({}, {"attempt_id": "different"})
    trace = Mock()
    trace.valid.return_value = False
    trace.payload.return_value = {"text": "different"}
    trace.hash.return_value = "hash"
    monkeypatch.setattr(bot, "current_main_post_attempt_is_semantically_valid", trace.valid)
    monkeypatch.setattr(bot, "main_post_attempt_payload", trace.payload)
    monkeypatch.setattr(bot, "canonical_remote_post_payload_sha256", trace.hash)
    attempt, payload = {"payload_sha256": "hash"}, {"text": "bound"}
    assert not bot.main_post_attempt_binds_payload(attempt, payload)
    assert trace.mock_calls == [call.valid(attempt)]
    trace.reset_mock()
    trace.valid.return_value = True
    assert not bot.main_post_attempt_binds_payload(attempt, payload)
    assert trace.mock_calls == [call.valid(attempt), call.payload(attempt)]
    trace.payload.return_value = payload
    attempt["payload_sha256"] = 1
    assert not bot.main_post_attempt_binds_payload(attempt, payload)
    trace.hash.assert_not_called()
    trace.reset_mock()
    attempt["payload_sha256"] = "hash"
    assert bot.main_post_attempt_binds_payload(attempt, payload)
    assert trace.mock_calls == [call.valid(attempt), call.payload(attempt), call.hash(payload)]
    assert trace.hash.call_args.args[0] is payload


@pytest.mark.parametrize("explicit_epoch", [False, True])
def test_attempt_construction_order_copies_truthiness_and_final_validator(monkeypatch, explicit_epoch):
    original = schema_current_main_attempt("quote_image")
    events, validated = [], []
    selected, plan, envelope = original["selected_identity"], original["recovery_plan"], {"binding": []}
    selected["child"] = []
    def entropy(size):
        events.append(("entropy", size))
        return b"entropy"
    def sha256(value):
        events.append(("sha256", value))
        def hexdigest():
            events.append(("hexdigest", value))
            return "digest"
        return SimpleNamespace(hexdigest=hexdigest)
    def payload_hash(payload):
        events.append(("payload", payload))
        return "payload hash"
    def copied(value):
        name = next(name for name, original in [("selected", selected), ("plan", plan), ("envelope", envelope)] if value is original)
        events.append(("copy", name))
        return copy.deepcopy(value)
    def validate(attempt):
        events.append(("validate",))
        validated.append(attempt)
        return True
    monkeypatch.setattr(bot, "os", SimpleNamespace(urandom=entropy))
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=sha256))
    monkeypatch.setattr(bot, "now_epoch", lambda: (events.append(("clock",)), 88)[1])
    monkeypatch.setattr(bot, "canonical_remote_post_payload_sha256", payload_hash)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=copied))
    monkeypatch.setattr(bot, "current_main_post_attempt_is_semantically_valid", validate)
    options = dict(lane="quote_image", text=_ObservedValue(events, "text", "téxt"),
                   media_ids=[_ObservedValue(events, "media", 7)],
                   made_with_ai=_ObservedValue(events, "ai", 1), selected_identity=selected,
                   recovery_plan=plan, engagement_experiment=envelope,
                   attempt_epoch=_ObservedValue(events, "epoch", 88) if explicit_epoch else None)
    result = bot.build_main_post_attempt(**options)
    assert events == [
        ("text", "bool"), ("text", "str"), ("media", "str"), ("ai", "bool"),
        ("entropy", 32), ("sha256", b"entropy"), ("hexdigest", b"entropy"),
        ("epoch", "int") if explicit_epoch else ("clock",),
        ("payload", {"text": "téxt", "media": {"media_ids": ["7"]}, "made_with_ai": True}),
        ("text", "str"), ("text", "str"), ("sha256", "téxt".encode()),
        ("hexdigest", "téxt".encode()), ("media", "str"), ("ai", "bool"),
        ("copy", "selected"), ("copy", "plan"), ("copy", "envelope"), ("validate",),
    ]
    assert result is validated[0]
    assert result["schema_version"] == 6 and result["attempt_epoch"] == 88
    assert result["made_with_ai"] is True
    assert result["selected_identity"] == selected and result["selected_identity"] is not selected
    assert result["selected_identity"]["child"] is not selected["child"]
    assert result["recovery_plan"]["meme_schedule_before"] is not plan["meme_schedule_before"]
    assert result["engagement_question_experiment"]["binding"] is not envelope["binding"]
    monkeypatch.setattr(bot, "current_main_post_attempt_is_semantically_valid", lambda attempt: False)
    with pytest.raises(RuntimeError, match="Internal error: generated main-post attempt is invalid"):
        bot.build_main_post_attempt(**options)


@pytest.mark.parametrize("lane, bound_timezone, experiment", [
    ("unsupported", True, None), ("quote_image", False, None), ("daily_meme", True, {}),
])
def test_attempt_input_validation_precedes_entropy(monkeypatch, lane, bound_timezone, experiment):
    entropy = Mock(side_effect=AssertionError("entropy before validation"))
    monkeypatch.setattr(bot, "os", SimpleNamespace(urandom=entropy))
    with pytest.raises(ValueError):
        bot.build_main_post_attempt(
            lane=lane, text="quote", media_ids=[7], made_with_ai=1,
            selected_identity={},
            recovery_plan={"schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE if bound_timezone else None},
            engagement_experiment=experiment,
        )
    entropy.assert_not_called()


def test_pending_construction_conversion_copy_and_original_lane_order(monkeypatch):
    attempt = schema_current_main_attempt("daily_meme")
    events, validated = [], []
    attempt.update(lifecycle_state="attempting", attempt_epoch=_ObservedValue(events, "attempt epoch", 10),
                   lane=_ObservedValue(events, "lane", "daily_meme"))
    def copied(value):
        assert value is attempt
        events.append(("copy",))
        result = copy.deepcopy(value)
        result["lane"] = "copied lane"
        return result
    def validate(pending, *, expected_lane):
        events.append(("pending", expected_lane))
        validated.append(pending)
        return True
    monkeypatch.setattr(bot, "main_post_attempt_is_semantically_valid", lambda value: (events.append(("attempt",)), True)[1])
    monkeypatch.setattr(bot, "valid_post_id", lambda value: (events.append(("post gate",)), True)[1])
    monkeypatch.setattr(bot, "valid_receipt_epoch", lambda value: (events.append(("epoch gate", value)), True)[1])
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=copied))
    monkeypatch.setattr(bot, "confirmed_pending_schedule_receipt_is_semantically_valid", validate)
    options = dict(post_id=_ObservedValue(events, "post", 99),
                   confirmation_epoch=_ObservedValue(events, "confirmed", 12),
                   image_summary=_ObservedValue(events, "summary", "image"))
    result = bot.build_confirmed_pending_schedule_receipt(attempt, **options)
    assert events == [
        ("attempt",), ("post gate",), ("confirmed", "int"), ("epoch gate", 12),
        ("confirmed", "int"), ("attempt epoch", "int"), ("post", "str"),
        ("confirmed", "int"), ("copy",), ("summary", "str"), ("lane", "str"),
        ("pending", "daily_meme"),
    ]
    assert result is validated[0]
    assert list(result) == ["schema_version", "receipt_type", "post_id", "confirmation_epoch", "source_attempt", "image_summary"]
    assert result["source_attempt"] is not attempt
    assert result["source_attempt"]["recovery_plan"] is not attempt["recovery_plan"]
    assert (result["post_id"], result["confirmation_epoch"], result["image_summary"]) == ("99", 12, "image")
    monkeypatch.setattr(bot, "confirmed_pending_schedule_receipt_is_semantically_valid", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="Internal error: confirmed pending-schedule receipt is invalid"):
        bot.build_confirmed_pending_schedule_receipt(attempt, **options)


def test_pending_rejection_short_circuits_before_copy_and_keeps_native_errors(monkeypatch):
    attempt = schema_current_main_attempt("daily_meme")
    attempt["lifecycle_state"] = "attempting"
    validator, post, epoch, copied = Mock(return_value=False), Mock(return_value=False), Mock(return_value=False), Mock()
    monkeypatch.setattr(bot, "main_post_attempt_is_semantically_valid", validator)
    monkeypatch.setattr(bot, "valid_post_id", post)
    monkeypatch.setattr(bot, "valid_receipt_epoch", epoch)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=copied))
    options = dict(post_id="post", confirmation_epoch=1_800_000_010)
    with pytest.raises(RuntimeError, match="Refusing an invalid main-post attempt or confirmation"):
        bot.build_confirmed_pending_schedule_receipt(object(), **options)
    post.assert_not_called()
    validator.return_value = True
    with pytest.raises(RuntimeError):
        bot.build_confirmed_pending_schedule_receipt(attempt, **options)
    epoch.assert_not_called()
    post.return_value = True
    with pytest.raises(ValueError):
        bot.build_confirmed_pending_schedule_receipt(attempt, **{**options, "confirmation_epoch": "invalid"})
    epoch.assert_not_called()
    with pytest.raises(RuntimeError):
        bot.build_confirmed_pending_schedule_receipt(attempt, **options)
    copied.assert_not_called()


@pytest.mark.parametrize("observed", [9, 10, 11])
def test_confirmation_epoch_conversion_and_current_logging_order(monkeypatch, observed):
    events = []
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    attempt = {"attempt_epoch": _ObservedValue(events, "attempt", 10), "lane": "daily_meme", "attempt_id": "id"}
    assert bot.confirmation_epoch_for_main_attempt(attempt, _ObservedValue(events, "observed", observed)) == max(10, observed)
    assert events == [("attempt", "int"), ("observed", "int")]
    if observed < 10:
        logger.warning.assert_called_once_with(
            "Wall clock moved backwards after X confirmation; clamping confirmation epoch lane=%s attempt_id=%s observed=%s attempt=%s",
            "daily_meme", "id", observed, 10,
        )
    else:
        logger.warning.assert_not_called()
    events.clear()
    with pytest.raises(ValueError):
        bot.confirmation_epoch_for_main_attempt({"attempt_epoch": "invalid"}, _ObservedValue(events, "observed", observed))
    assert events == []
