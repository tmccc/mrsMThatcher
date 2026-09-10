"""Focused contracts for engagement opportunities and publication authority."""
from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import historical_context_formatter as formatter
import mrs_bot_engagement_publication as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.test_engagement_question_experiment import (
    _prepared_synthetic_authority_case,
    synthetic_plan_bundle,
)


DEPENDENCIES = {'invalidate_engagement_question_experiment': ['engagement_question_trial',
                                               'log_event',
                                               'save_state'],
 'initialise_engagement_question_experiment': ['engagement_question_experiment_enabled',
                                               'engagement_question_trial',
                                               'invalidate_engagement_question_experiment',
                                               'load_engagement_question_runtime_plan',
                                               'log',
                                               'log_event',
                                               'save_state'],
 'engagement_question_opportunity': ['engagement_question_experiment_enabled',
                                     'engagement_question_trial',
                                     'initialise_engagement_question_experiment',
                                     'log_event',
                                     'save_state'],
 'resolve_engagement_question_quote_choice': ['HISTORICAL_CONTEXT_RESEARCH_DIR',
                                              '_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT',
                                              'completed_research_quote_hashes',
                                              'engagement_question_trial',
                                              'historical_context_reply',
                                              'load_quote_lines_and_analysis',
                                              'quote_candidate_weight',
                                              'quote_metadata_for_hash',
                                              'quote_text_hash'],
 'revalidate_engagement_question_publication_authority': ['engagement_experiment_attempt_envelope_is_valid',
                                                          'engagement_question_trial',
                                                          'load_engagement_question_runtime_plan',
                                                          'resolve_engagement_question_quote_choice'],
 'engagement_question_authority_failure_diagnostic': ['ENGAGEMENT_QUESTION_MEMBER_AUTHORITY_KEYS',
                                                      'json',
                                                      're'],
 'revalidate_or_invalidate_engagement_question_publication': ['engagement_question_authority_failure_diagnostic',
                                                              'engagement_question_trial',
                                                              'invalidate_engagement_question_experiment',
                                                              'log',
                                                              'now_epoch',
                                                              'revalidate_engagement_question_publication_authority'],
 'defer_engagement_question_member': ['engagement_question_trial',
                                      'log_event',
                                      'save_state']}

SIGNATURES = {'invalidate_engagement_question_experiment': "(state: 'dict', *, code: 'str', "
                                              "recorded_epoch: 'int', "
                                              "exception_class: 'str | None' = "
                                              "None, authority_component: 'str "
                                              "| None' = None) -> 'None'",
 'initialise_engagement_question_experiment': "(state: 'dict', *, "
                                              "current_epoch: 'int') -> "
                                              "'tuple[dict | None, dict | "
                                              "None]'",
 'engagement_question_opportunity': "(state: 'dict', *, current_epoch: 'int') "
                                    "-> 'tuple[dict | None, dict | None, "
                                    "set[str]]'",
 'resolve_engagement_question_quote_choice': "(member: 'dict', *, catalogue: "
                                             "'dict') -> 'tuple[dict, str]'",
 'revalidate_engagement_question_publication_authority': "(*, state: 'dict', "
                                                         "lines_used: 'set', "
                                                         "envelope: 'dict', "
                                                         'quote_choice: '
                                                         "'dict', public_text: "
                                                         "'str') -> 'None'",
 'engagement_question_authority_failure_diagnostic': "(exc: 'BaseException') "
                                                     "-> 'tuple[str, str]'",
 'revalidate_or_invalidate_engagement_question_publication': '(*, state: '
                                                             "'dict', "
                                                             'lines_used: '
                                                             "'set', envelope: "
                                                             "'dict', "
                                                             'quote_choice: '
                                                             "'dict', "
                                                             'public_text: '
                                                             "'str') -> 'None'",
 'defer_engagement_question_member': "(state: 'dict', *, code: 'str', "
                                     "recorded_epoch: 'int') -> 'None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Engagement publication import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter'} or name.startswith('mrs_bot_') and name != 'mrs_bot_engagement_publication':
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
import mrs_bot_engagement_publication
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

            patch.setattr(bot, "_engagement_publication", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_engagement_publication", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("operation", ["invalidate", "defer"])
def test_transitions_keep_state_identity_and_save_before_event_fields(monkeypatch, operation):
    events = []
    protected = {"status": "active"}

    class State(dict):
        def __setitem__(self, key, value):
            assert key == "engagement_question_experiment" and value is protected
            events.append("assign")
            super().__setitem__(key, value)

    state = State(engagement_question_experiment=protected)
    record = Mock(side_effect=lambda *args, **kwargs: events.append("record"))
    trial = SimpleNamespace(EXPERIMENT_ID="current-id", mark_experiment_invalid=record, record_deferral=record)
    monkeypatch.setattr(bot, "engagement_question_trial", trial)
    logged = Mock(side_effect=lambda *args, **kwargs: events.append("log"))
    monkeypatch.setattr(bot, "log_event", logged)

    def save(current, *, durable):
        assert current is state and current["engagement_question_experiment"] is protected
        assert durable is True
        events.append("save")
        # These fields must not be read until durable publication has returned.
        protected.update(experiment_id="current-id", active_plan_sha256="plan", current_deferral_reason={
            "code": "recorded-code", "pair_id": "pair", "member_position": 2,
        })

    monkeypatch.setattr(bot, "save_state", save)
    function = getattr(bot, f"{operation}_engagement_question_" + (
        "experiment" if operation == "invalidate" else "member"
    ))
    options = {"code": "requested-code", "recorded_epoch": 123}
    if operation == "invalidate":
        options.update(exception_class="", authority_component="")
    assert function(state, **options) is None
    assert events == ["record", "assign", "save", "log"]
    record.assert_called_once_with(protected, code="requested-code", recorded_epoch=123)
    assert record.call_args.args[0] is protected
    if operation == "invalidate":
        logged.assert_called_once_with(
            "engagement_question_experiment_invalid", experiment_id="current-id",
            plan_sha256="plan", reason="recorded-code", started=True,
            exception_class="", authority_component="",
        )
    else:
        logged.assert_called_once_with(
            "engagement_question_experimental_member_deferred", experiment_id="current-id",
            plan_sha256="plan", pair_id="pair", member_position=2, reason="recorded-code",
        )

    failure = OSError("durable save failed")

    def failed_save(*args, **kwargs):
        events.append("save")
        raise failure

    events.clear()
    logged.reset_mock()
    monkeypatch.setattr(bot, "save_state", failed_save)
    with pytest.raises(OSError) as caught:
        function(state, **options)
    assert caught.value is failure and events == ["record", "assign", "save"]
    logged.assert_not_called()
    protected.clear()
    protected["status"] = "active"
    events.clear()
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: events.append("save"))
    with pytest.raises(KeyError):
        function(state, **options)
    assert events == ["record", "assign", "save"]
    logged.assert_not_called()


@pytest.mark.parametrize("status", ["invalid", "completed", "unstarted"])
def test_terminal_and_unstarted_transitions_keep_early_gates(monkeypatch, status):
    raw = {"status": status} if status != "unstarted" else []
    state = {"engagement_question_experiment": raw}
    forbidden = Mock(side_effect=AssertionError("early gate crossed"))
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", True)
    monkeypatch.setattr(bot, "load_engagement_question_runtime_plan", forbidden)
    monkeypatch.setattr(bot, "save_state", forbidden)
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        EXPERIMENT_ID="current-id", mark_experiment_invalid=forbidden,
    ))
    logged = Mock()
    monkeypatch.setattr(bot, "log_event", logged)
    assert bot.invalidate_engagement_question_experiment(state, code="stop", recorded_epoch=1) is None
    assert state["engagement_question_experiment"] is raw
    if status == "unstarted":
        logged.assert_called_once_with(
            "engagement_question_experiment_invalid", experiment_id="current-id", reason="stop", started=False,
        )
        with pytest.raises(RuntimeError, match="cannot defer an experiment before it starts"):
            bot.defer_engagement_question_member(state, code="wait", recorded_epoch=1)
    else:
        result = bot.initialise_engagement_question_experiment(state, current_epoch=1)
        assert result[0] is None and result[1] is raw
        logged.assert_not_called()
    forbidden.assert_not_called()


@pytest.mark.parametrize("boundary", ["loader", "validation", "native", "interrupt"])
def test_initialization_keeps_current_error_boundary_and_post_invalidation_lookup(monkeypatch, boundary):
    trace = Mock()
    validation_error = type("CurrentValidationError", (ValueError,), {})
    failure = {"loader": OSError("missing plan"), "validation": validation_error("binding"),
               "native": TypeError("engine failure"), "interrupt": KeyboardInterrupt()}[boundary]
    plan, raw, replacement = {}, {"status": "active"}, {"status": "invalid"}
    state = {"engagement_question_experiment": raw}
    trace.load.return_value = (plan, {}, {})
    if boundary in {"loader", "interrupt"}:
        trace.load.side_effect = failure
    else:
        trace.validate.side_effect = failure
    trace.invalidate.side_effect = lambda *args, **kwargs: state.update(engagement_question_experiment=replacement)
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", True)
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        ExperimentValidationError=validation_error, validate_experiment_state=trace.validate,
    ))
    monkeypatch.setattr(bot, "load_engagement_question_runtime_plan", trace.load)
    monkeypatch.setattr(bot, "invalidate_engagement_question_experiment", trace.invalidate)
    monkeypatch.setattr(bot, "log", SimpleNamespace(error=trace.error))
    if boundary in {"native", "interrupt"}:
        with pytest.raises(type(failure)) as caught:
            bot.initialise_engagement_question_experiment(state, current_epoch=123)
        assert caught.value is failure and state["engagement_question_experiment"] is raw
        assert trace.mock_calls == [call.load(), *(
            [call.validate(raw, plan=plan)] if boundary == "native" else []
        )]
    else:
        result = bot.initialise_engagement_question_experiment(state, current_epoch=123)
        assert result[0] is None and result[1] is replacement
        expected = [call.load()]
        if boundary == "loader":
            expected.append(call.error("Engagement-question plan is unavailable or invalid: %s", failure, exc_info=True))
        else:
            expected.append(call.validate(raw, plan=plan))
        expected.append(call.invalidate(state, code=(
            "configured_plan_unavailable_or_invalid" if boundary == "loader" else "active_plan_binding_changed"
        ), recorded_epoch=123))
        assert trace.mock_calls == expected
        assert trace.invalidate.call_args.args[0] is state


@pytest.mark.parametrize("mode", ["pause", "unchanged", "unpause", "unstarted"])
def test_initialization_preserves_pause_save_rules_and_state_references(monkeypatch, mode):
    trace = Mock()
    plan = {}
    protected = {"status": "active" if mode == "pause" else "paused", "experiment_id": "trial",
                 "active_plan_sha256": "plan", "completed_pair_count": 2}
    state = {} if mode == "unstarted" else {"engagement_question_experiment": protected}
    trace.pause.return_value = mode == "pause"
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", mode in {"unpause", "unstarted"})
    monkeypatch.setattr(bot, "load_engagement_question_runtime_plan", lambda: (plan, {}, {}))
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        ExperimentValidationError=ValueError, validate_experiment_state=trace.validate, set_experiment_paused=trace.pause,
    ))
    monkeypatch.setattr(bot, "save_state", trace.save)
    monkeypatch.setattr(bot, "log_event", trace.event)
    result = bot.initialise_engagement_question_experiment(state, current_epoch=123)
    assert result[0] is plan
    if mode == "unstarted":
        assert result[1] is None and state == {} and trace.mock_calls == []
        return
    assert result[1] is protected and state["engagement_question_experiment"] is protected
    expected = [call.validate(protected, plan=plan), call.pause(protected, paused=mode != "unpause", plan=plan)]
    if mode in {"pause", "unpause"}:
        expected.append(call.save(state, durable=True))
        assert trace.save.call_args.args[0] is state
    if mode == "pause":
        expected.append(call.event("engagement_question_experiment_paused", experiment_id="trial", plan_sha256="plan", completed_pairs=2))
    assert trace.mock_calls == expected


@pytest.mark.parametrize("mode", ["new", "restart", "deferred", "disabled", "no_plan"])
def test_opportunity_keeps_start_member_reservation_order_and_references(monkeypatch, mode):
    trace = Mock()
    plan, member, reserved = {}, {"quote_id": "member"}, {"reserved"}
    protected = {"experiment_id": "trial", "active_plan_sha256": "plan", "active_pair_id": "pair", "current_pair_index": 2}
    state = {} if mode == "new" else {"engagement_question_experiment": protected}
    trace.initialize.return_value = (None if mode == "no_plan" else plan, None if mode == "new" else protected)
    trace.new.return_value = protected
    trace.member.side_effect = ([(object(), "pair_start_required"), (member, None)] if mode == "restart"
                                else [(member, "wait" if mode == "deferred" else None)])
    trace.reserved.return_value = reserved

    def save(current, *, durable):
        assert current is state and current["engagement_question_experiment"] is protected and durable is True

    trace.save.side_effect = save
    monkeypatch.setattr(bot, "initialise_engagement_question_experiment", trace.initialize)
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", mode != "disabled")
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        new_experiment_state=trace.new, start_next_pair=trace.start,
        member_for_current_opportunity=trace.member, reserved_quote_ids=trace.reserved,
    ))
    monkeypatch.setattr(bot, "save_state", trace.save)
    monkeypatch.setattr(bot, "log_event", trace.event)
    current_epoch = object()
    result = bot.engagement_question_opportunity(state, current_epoch=current_epoch)
    expected = [call.initialize(state, current_epoch=current_epoch)]
    started = [call.start(protected, plan), call.save(state, durable=True), call.event(
        "engagement_question_experiment_pair_started", experiment_id="trial", plan_sha256="plan", pair_id="pair", pair_index=2,
    )]
    lookup = call.member(plan, protected, current_epoch=current_epoch)
    if mode == "new":
        expected += [call.new(plan), *started, lookup]
    elif mode == "restart":
        expected += [lookup, *started, lookup]
    elif mode == "deferred":
        expected += [lookup]
    if mode != "no_plan":
        expected.append(call.reserved(plan, protected))
        assert result[0] is plan and result[2] is reserved
        assert result[1] is (member if mode in {"new", "restart"} else None)
    assert trace.mock_calls == expected
    if mode == "no_plan":
        again = bot.engagement_question_opportunity(state, current_epoch=current_epoch)
        assert result == again == (None, None, set()) and result[2] is not again[2]


@pytest.mark.parametrize("use_snapshot", [True, False])
def test_quote_resolution_keeps_current_formatter_exact_scan_references_and_native_errors(
    monkeypatch, synthetic_plan_bundle, use_snapshot,
):
    bundle, _, _, member, choice, _, public_text, _ = _prepared_synthetic_authority_case(synthetic_plan_bundle, monkeypatch)
    exact, quote_id = choice["text"], member["quote_id"]
    trace = Mock()
    source_analysis, analysis, today, season, packet = {}, {"primary_topics": [member["topic"]]}, object(), object(), object()
    packets, unresolved = bot._HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    research_dir = object()
    trace.load.return_value = ([exact + "\r\r\n", exact + "\r\n", exact + "\n"], source_analysis, today)
    trace.scan.side_effect = bot.engagement_question_trial.sha256_text
    trace.production_hash.return_value = quote_id
    trace.eligible.return_value = {quote_id}
    trace.corpus.return_value = (packets, unresolved)
    trace.packet.return_value = packet
    trace.attributed.return_value = True
    trace.format.return_value = {"rendering_mode": "public", "verification_label": member["verification_label"], "source_class": member["source_class"]}
    trace.metadata.return_value = analysis
    trace.weight.return_value = (2.5, season)
    trace.public.return_value = public_text
    for name, callback in {
        "load_quote_lines_and_analysis": trace.load, "quote_text_hash": trace.production_hash,
        "completed_research_quote_hashes": trace.eligible, "quote_metadata_for_hash": trace.metadata,
        "quote_candidate_weight": trace.weight,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot.engagement_question_trial, "sha256_text", trace.scan)
    monkeypatch.setattr(bot.engagement_question_trial, "validate_complete_public_text", trace.public)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", (packets, unresolved) if use_snapshot else None)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", research_dir)
    monkeypatch.setattr(bot, "historical_context_reply", {
        "maximum_length": "37", "include_meaning": [], "include_source": "yes", "include_verification": 0,
    })
    for name, callback in {
        "load_and_validate_corpus": trace.corpus, "packet_for_posted_quote": trace.packet,
        "packet_is_attributed_to_margaret_thatcher": trace.attributed, "format_context_reply_public": trace.format,
    }.items():
        monkeypatch.setattr(formatter, name, callback)
    resolved, public = bot.resolve_engagement_question_quote_choice(member, catalogue=bundle["catalogue"])
    assert resolved == {"line_no": 1, "text": exact, "quote_hash": quote_id, "analysis": analysis, "weight": 2.5, "season_status": season}
    assert resolved["analysis"] is analysis and resolved["season_status"] is season and public is public_text
    assert trace.mock_calls == [
        call.load(), call.scan(exact + "\r"), call.scan(exact), call.production_hash(exact), call.eligible(),
        *([] if use_snapshot else [call.corpus(research_dir, require_source_role_audit=True)]),
        call.packet(packets, unresolved, quote_id, exact), call.attributed(packet),
        call.format(packet, maximum_length=37, include_meaning=False, include_source=True, include_verification=False),
        call.metadata(source_analysis, quote_id, exact), call.weight(analysis, today_mm_dd=today),
        call.public(exact_quote_text=exact, catalogue_entry=bundle["catalogue"]["entries"][quote_id], arm=member["arm"]),
    ]
    assert trace.packet.call_args.args[0] is packets and trace.packet.call_args.args[1] is unresolved
    assert trace.metadata.call_args.args[0] is source_analysis and trace.weight.call_args.args[0] is analysis
    assert trace.public.call_args.kwargs["catalogue_entry"] is bundle["catalogue"]["entries"][quote_id]
    failure = TypeError("current public-text validator failed")
    trace.public.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.resolve_engagement_question_quote_choice({**member, "approved_question_body": "changed"}, catalogue=bundle["catalogue"])
    assert caught.value is failure  # Complete-public-text validation precedes approved metadata comparisons.
    trace.reset_mock()
    with pytest.raises(KeyError) as caught:
        bot.resolve_engagement_question_quote_choice({}, catalogue=bundle["catalogue"])
    assert caught.value.args == ("quote_id",) and trace.mock_calls == [call.load()]


@pytest.mark.parametrize("changed_envelope", [False, True])
def test_authority_rebuild_keeps_current_member_references_and_envelope_short_circuit(
    monkeypatch, synthetic_plan_bundle, changed_envelope,
):
    bundle, _, state, _, choice, envelope, public, _ = _prepared_synthetic_authority_case(synthetic_plan_bundle, monkeypatch)
    before = copy.deepcopy((state, choice, envelope))
    trace = Mock()
    trace.load.return_value = (bundle["plan"], bundle["catalogue"], bundle["quote_text_by_id"])
    trace.resolve.return_value = (choice, public)
    trace.build.return_value = envelope["binding"]
    trace.valid.return_value = True
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        ExperimentValidationError=ValueError, validate_experiment_state=trace.validate, build_attempt_binding=trace.build,
    ))
    monkeypatch.setattr(bot, "load_engagement_question_runtime_plan", trace.load)
    monkeypatch.setattr(bot, "resolve_engagement_question_quote_choice", trace.resolve)
    monkeypatch.setattr(bot, "engagement_experiment_attempt_envelope_is_valid", trace.valid)
    supplied = {**envelope, "extra": True} if changed_envelope else envelope
    options = dict(state=state, lines_used=set(), envelope=supplied, quote_choice=choice, public_text=public)
    if changed_envelope:
        with pytest.raises(ValueError, match="publication authority changed"):
            bot.revalidate_engagement_question_publication_authority(**options)
        trace.valid.assert_not_called()
    else:
        assert bot.revalidate_engagement_question_publication_authority(**options) is None
        trace.valid.assert_called_once_with(envelope, public_text=public, quote_hash=choice["quote_hash"], plan=bundle["plan"])
        assert trace.valid.call_args.args[0] is envelope
    current_member = trace.resolve.call_args.args[0]
    pair = bundle["plan"]["pairs"][envelope["binding"]["pair_index"]]
    assert current_member["topic"] == pair["topic"] and current_member["publication_order"] == pair["planned_publication_order"]
    assert trace.build.call_args.kwargs["member"] is current_member
    assert trace.build.call_args.kwargs["state"] is state["engagement_question_experiment"]
    assert trace.build.call_args.kwargs["plan"] is bundle["plan"]
    assert trace.build.call_args.kwargs["public_text"] is public
    assert trace.resolve.call_args.kwargs["catalogue"] is bundle["catalogue"]
    assert [entry[0] for entry in trace.mock_calls] == ["load", "validate", "resolve", "build", *([] if changed_envelope else ["valid"])]
    assert (state, choice, envelope) == before


@pytest.mark.parametrize("boundary", ["retrieval", "position", "builder"])
def test_authority_keeps_narrow_member_catch_and_native_failures(monkeypatch, synthetic_plan_bundle, boundary):
    bundle, _, state, _, choice, envelope, public, _ = _prepared_synthetic_authority_case(synthetic_plan_bundle, monkeypatch)
    failure = KeyError("unavailable member") if boundary == "retrieval" else TypeError("builder failure")

    class UnavailablePairs(dict):
        def __getitem__(self, key):
            raise failure

    plan = {**bundle["plan"], "pairs": UnavailablePairs()} if boundary == "retrieval" else bundle["plan"]
    if boundary == "position":
        envelope["binding"]["member_position"] = []
    resolver, builder = Mock(return_value=(choice, public)), Mock(side_effect=failure)
    monkeypatch.setattr(bot, "load_engagement_question_runtime_plan", lambda: (plan, bundle["catalogue"], {}))
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        ExperimentValidationError=ValueError, validate_experiment_state=Mock(), build_attempt_binding=builder,
    ))
    monkeypatch.setattr(bot, "resolve_engagement_question_quote_choice", resolver)
    with pytest.raises(ValueError if boundary == "retrieval" else TypeError) as caught:
        bot.revalidate_engagement_question_publication_authority(
            state=state, lines_used=set(), envelope=envelope, quote_choice=choice, public_text=public,
        )
    if boundary == "retrieval":
        assert str(caught.value) == "prepared experimental publication member is unavailable"
        assert caught.value.__cause__ is failure
    elif boundary == "builder":
        assert caught.value is failure
    else:
        assert "unhashable" in str(caught.value) and caught.value.__cause__ is None
    if boundary != "builder":
        resolver.assert_not_called()
        builder.assert_not_called()


@pytest.mark.parametrize("exc, expected", [
    (type("A" * 80, (Exception,), {})(), ("A" * 80, "authority_revalidation")),
    (type("A" * 81, (Exception,), {})(), ("Exception", "authority_revalidation")),
    (type("_InvalidName", (Exception,), {})(), ("Exception", "authority_revalidation")),
    (Exception(type("StringSubclass", (str,), {})("plan")), ("Exception", "authority_revalidation")),
    (Exception("x" * 240 + "plan"), ("Exception", "authority_revalidation")),
    (Exception("USED HISTORY catalogue plan state binding payload member"), ("Exception", "used_history")),
    (OSError("catalogue plan state"), ("OSError", "catalogue")),
    (OSError("unavailable"), ("OSError", "authority_input")),
], ids=["class-limit", "class-too-long", "class-pattern", "exact-string-type", "message-limit", "category-priority", "message-before-type", "input-type"])
def test_diagnostic_keeps_bounded_types_and_category_precedence(exc, expected):
    assert bot.engagement_question_authority_failure_diagnostic(exc) == expected


@pytest.mark.parametrize("failure_at", [None, "error", "diagnostic", "clock", "invalidate", "base_exception"])
def test_revalidation_failure_keeps_logging_diagnostic_clock_invalidation_and_cause_order(monkeypatch, failure_at):
    trace = Mock()
    original = KeyboardInterrupt() if failure_at == "base_exception" else ValueError("payload changed")
    native = TypeError("current callback failure")
    current_error = type("CurrentValidationError", (ValueError,), {})
    trace.revalidate.side_effect = original
    trace.diagnostic.return_value = ("ObservedError", "public_payload")
    epoch = object()
    trace.clock.return_value = epoch
    if failure_at not in {None, "base_exception"}:
        getattr(trace, failure_at).side_effect = native
    monkeypatch.setattr(bot, "revalidate_engagement_question_publication_authority", trace.revalidate)
    monkeypatch.setattr(bot, "log", SimpleNamespace(error=trace.error))
    monkeypatch.setattr(bot, "engagement_question_authority_failure_diagnostic", trace.diagnostic)
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "invalidate_engagement_question_experiment", trace.invalidate)
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(ExperimentValidationError=current_error))
    options = dict(state={}, lines_used=set(), envelope={}, quote_choice={}, public_text="public")
    expected_error = current_error if failure_at is None else KeyboardInterrupt if failure_at == "base_exception" else TypeError
    with pytest.raises(expected_error) as caught:
        bot.revalidate_or_invalidate_engagement_question_publication(**options)
    expected = [call.revalidate(**options)]
    subsequent = [
        call.error("Experimental publication authority changed at remote-write handoff", exc_info=True),
        call.diagnostic(original), call.clock(), call.invalidate(
            options["state"], code="pre_write_authority_changed", recorded_epoch=epoch,
            exception_class="ObservedError", authority_component="public_payload",
        ),
    ]
    if failure_at != "base_exception":
        stop = 4 if failure_at is None else ["error", "diagnostic", "clock", "invalidate"].index(failure_at) + 1
        expected += subsequent[:stop]
    assert trace.mock_calls == expected
    assert all(trace.revalidate.call_args.kwargs[key] is value for key, value in options.items())
    if failure_at is None:
        assert caught.value.__cause__ is original
        assert str(caught.value) == "experimental publication authority changed before remote root posting"
        assert trace.invalidate.call_args.args[0] is options["state"]
    else:
        assert caught.value is (original if failure_at == "base_exception" else native)
