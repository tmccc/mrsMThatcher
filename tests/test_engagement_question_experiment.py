from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import engagement_question_experiment as experiment
import mrsMThatcher2 as bot
from tools import prepare_engagement_question_experiment as prepare


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PATH = (
    PROJECT_ROOT
    / "engagement_question_experiment"
    / "approved_question_catalogue.json"
)
EXPECTED_CATALOGUE_SHA256 = (
    "cdce2b7f7a7ceb140e907716f1ef7392d77aab99061997bb492cddf98952f6ba"
)


def _catalogue_entry(quote: str, question: str, source: str = "generated") -> dict:
    treatment = experiment.complete_treatment_text(quote, question)
    return {
        "quote_text_sha256": experiment.sha256_text(quote),
        "question_body": question,
        "question_sha256": experiment.sha256_text(question),
        "complete_treatment_sha256": experiment.sha256_text(treatment),
        "complete_treatment_weighted_length": experiment.x_weighted_length(
            treatment
        ),
        "question_source": source,
    }


def _small_catalogue(quotes: list[str]) -> tuple[dict, dict[str, str]]:
    quote_text_by_id = {
        experiment.sha256_text(quote): quote for quote in quotes
    }
    entries = {
        quote_id: _catalogue_entry(
            quote,
            f"Why does approved synthetic question {index} matter?",
            tuple(sorted(experiment.QUESTION_SOURCES))[index % 3],
        )
        for index, (quote_id, quote) in enumerate(quote_text_by_id.items())
    }
    return (
        {
            "schema_version": 1,
            "experiment_id": experiment.EXPERIMENT_ID,
            "source_snapshot_sha256": "1" * 64,
            "entries": entries,
        },
        quote_text_by_id,
    )


@pytest.fixture(scope="module")
def approved_catalogue_bundle() -> tuple[dict, str, dict[str, str]]:
    quote_text_by_id, _lines, _source = prepare.canonical_quote_source(
        PROJECT_ROOT
    )
    catalogue, digest = experiment.load_approved_catalogue(
        CATALOGUE_PATH,
        quote_text_by_id,
    )
    return catalogue, digest, quote_text_by_id


@pytest.fixture(scope="module")
def synthetic_plan_bundle() -> dict:
    quotes: list[str] = []
    topics: dict[str, str] = {}
    for topic_index in range(11):
        for member_index in range(6):
            ordinal = topic_index * 6 + member_index
            quote = (
                f"Approved synthetic quotation {ordinal:03d}: "
                + "x" * (2 + member_index)
            )
            quotes.append(quote)
            topics[experiment.sha256_text(quote)] = f"topic_{topic_index:02d}"
    catalogue, quote_text_by_id = _small_catalogue(quotes)
    catalogue = experiment.validate_catalogue_document(
        catalogue,
        quote_text_by_id,
        expected_entry_count=len(quotes),
    )
    candidates = [
        experiment.candidate_from_catalogue(
            quote_id=quote_id,
            exact_quote_text=quote,
            topic=topics[quote_id],
            verification_label=(
                "Exact wording" if index % 3 else "Normalised wording"
            ),
            source_class=("Hansard" if index % 2 else "Foundation"),
            catalogue=catalogue,
        )
        for index, (quote_id, quote) in enumerate(quote_text_by_id.items())
    ]
    catalogue_sha256 = experiment.canonical_sha256(catalogue)
    plan, diagnostics = experiment.build_plan(
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256,
        candidates=candidates,
        used_history_sha256="2" * 64,
        plan_created_at="2026-08-30T08:00:00Z",
        plan_kind="preview",
    )
    return {
        "catalogue": catalogue,
        "catalogue_sha256": catalogue_sha256,
        "quote_text_by_id": quote_text_by_id,
        "candidates": candidates,
        "plan": plan,
        "diagnostics": diagnostics,
    }


def _current_member(
    bundle: dict,
    state: dict,
    epoch: int,
) -> tuple[dict, str, str]:
    plan = bundle["plan"]
    member, reason = experiment.member_for_current_opportunity(
        plan,
        state,
        current_epoch=epoch,
    )
    assert reason is None and member is not None
    exact = bundle["quote_text_by_id"][member["quote_id"]]
    public = experiment.validate_complete_public_text(
        exact_quote_text=exact,
        catalogue_entry=bundle["catalogue"]["entries"][member["quote_id"]],
        arm=member["arm"],
    )
    return member, exact, public


def _confirm_current(
    bundle: dict,
    state: dict,
    *,
    epoch: int,
    post_id: str,
    plan: dict | None = None,
) -> tuple[dict, dict]:
    member, exact, public = _current_member(bundle, state, epoch)
    binding = experiment.build_attempt_binding(
        plan=bundle["plan"],
        state=state,
        member=member,
        exact_quote_text=exact,
        public_text=public,
    )
    changed = experiment.apply_confirmed_publication(
        state,
        binding=binding,
        plan=bundle["plan"] if plan is None else plan,
        post_id=post_id,
        published_epoch=epoch,
        exact_quote_text=exact,
        public_text=public,
        approved_question_body=member["approved_question_body"],
    )
    assert changed is True
    return member, binding


def _regular_recovery_plan(quote_id: str) -> dict:
    return {
        "quote_delay_seconds": 7200,
        "meme_delay_seconds": 3600,
        "meme_scheduling_enabled": True,
        "meme_trigger_after_hour": 12,
        "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
        "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        "meme_schedule_before": bot.bound_meme_schedule_state(
            {},
            schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
        ),
        "quote_history_after": [quote_id],
        "image_history_after": ["t01.jpg"],
    }


def _experimental_attempt(
    bundle: dict,
    state: dict,
    *,
    epoch: int,
) -> tuple[dict, dict, dict, str, str]:
    member, exact, public = _current_member(bundle, state, epoch)
    binding = experiment.build_attempt_binding(
        plan=bundle["plan"],
        state=state,
        member=member,
        exact_quote_text=exact,
        public_text=public,
    )
    envelope = {
        "binding": binding,
        "canonical_quote_text": exact,
        "approved_question_body": member["approved_question_body"],
        "complete_treatment_sha256": member["complete_treatment_sha256"],
        "complete_treatment_weighted_length": member[
            "complete_treatment_weighted_length"
        ],
    }
    attempt = bot.build_main_post_attempt(
        lane="quote_image",
        text=public,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={
            "quote_hash": member["quote_id"],
            "line_no": 0,
            "source_line_number": 1,
            "image_basename": "t01.jpg",
            "image_no": 0,
        },
        recovery_plan=_regular_recovery_plan(member["quote_id"]),
        attempt_epoch=epoch,
        engagement_experiment=envelope,
    )
    return attempt, envelope, member, exact, public


def test_approved_catalogue_exact_identity_and_recomputed_values(
    approved_catalogue_bundle,
) -> None:
    catalogue, digest, quote_text_by_id = approved_catalogue_bundle
    assert hashlib.sha256(CATALOGUE_PATH.read_bytes()).hexdigest() == (
        EXPECTED_CATALOGUE_SHA256
    )
    assert digest == EXPECTED_CATALOGUE_SHA256
    assert catalogue["schema_version"] == 1
    assert catalogue["experiment_id"] == experiment.EXPERIMENT_ID
    assert len(catalogue["entries"]) == 93
    for quote_id, entry in catalogue["entries"].items():
        quote = quote_text_by_id[quote_id]
        treatment = experiment.complete_treatment_text(
            quote,
            entry["question_body"],
        )
        assert experiment.sha256_text(quote) == quote_id
        assert experiment.sha256_text(entry["question_body"]) == entry[
            "question_sha256"
        ]
        assert experiment.sha256_text(treatment) == entry[
            "complete_treatment_sha256"
        ]
        assert experiment.x_weighted_length(treatment) == entry[
            "complete_treatment_weighted_length"
        ]
        assert experiment.x_weighted_length(treatment) <= 280


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("question_body", "An altered approved question?"),
        ("quote_text_sha256", "0" * 64),
        ("complete_treatment_sha256", "0" * 64),
        ("complete_treatment_weighted_length", 1),
    ],
)
def test_catalogue_altered_values_fail_closed(
    approved_catalogue_bundle,
    field: str,
    replacement: object,
) -> None:
    catalogue, _digest, quote_text_by_id = approved_catalogue_bundle
    changed = copy.deepcopy(catalogue)
    first_id = next(iter(changed["entries"]))
    changed["entries"][first_id][field] = replacement
    with pytest.raises(experiment.ExperimentValidationError):
        experiment.validate_catalogue_document(changed, quote_text_by_id)


def test_duplicate_normalised_question_body_fails_closed(
    approved_catalogue_bundle,
) -> None:
    catalogue, _digest, quote_text_by_id = approved_catalogue_bundle
    changed = copy.deepcopy(catalogue)
    first_id, second_id = list(changed["entries"])[:2]
    duplicate = changed["entries"][first_id]["question_body"].upper()
    changed["entries"][second_id].update(
        _catalogue_entry(quote_text_by_id[second_id], duplicate)
    )
    with pytest.raises(
        experiment.ExperimentValidationError,
        match="duplicate normalised",
    ):
        experiment.validate_catalogue_document(changed, quote_text_by_id)


@pytest.mark.parametrize("mutation", ["schema", "field", "source"])
def test_unknown_catalogue_schema_field_or_source_fails_closed(
    approved_catalogue_bundle,
    mutation: str,
) -> None:
    catalogue, _digest, quote_text_by_id = approved_catalogue_bundle
    changed = copy.deepcopy(catalogue)
    first_id = next(iter(changed["entries"]))
    if mutation == "schema":
        changed["schema_version"] = 2
    elif mutation == "field":
        changed["entries"][first_id]["unexpected"] = True
    else:
        changed["entries"][first_id]["question_source"] = "unapproved"
    with pytest.raises(experiment.ExperimentValidationError):
        experiment.validate_catalogue_document(changed, quote_text_by_id)


@pytest.mark.parametrize(("weighted_length", "accepted"), [(280, True), (281, False)])
def test_treatment_weighted_length_boundary(
    weighted_length: int,
    accepted: bool,
) -> None:
    quote = "A"
    body_length = weighted_length - len(quote) - len(experiment.TREATMENT_SUFFIX)
    question = "Q" * (body_length - 1) + "?"
    catalogue, quote_text_by_id = _small_catalogue([quote])
    quote_id = experiment.sha256_text(quote)
    catalogue["entries"][quote_id] = _catalogue_entry(quote, question)
    if accepted:
        validated = experiment.validate_catalogue_document(
            catalogue,
            quote_text_by_id,
            expected_entry_count=1,
        )
        assert validated["entries"][quote_id][
            "complete_treatment_weighted_length"
        ] == 280
    else:
        with pytest.raises(experiment.ExperimentValidationError, match="exceeds"):
            experiment.validate_catalogue_document(
                catalogue,
                quote_text_by_id,
                expected_entry_count=1,
            )


def test_plan_is_canonical_deterministic_and_exactly_balanced(
    synthetic_plan_bundle,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    reverse_plan, _diagnostics = experiment.build_plan(
        catalogue=bundle["catalogue"],
        catalogue_sha256=bundle["catalogue_sha256"],
        candidates=list(reversed(bundle["candidates"])),
        used_history_sha256="2" * 64,
        plan_created_at="2026-08-30T08:00:00Z",
        plan_kind="preview",
    )
    assert reverse_plan == plan
    assert plan["plan_sha256"] == experiment.calculate_plan_sha256(plan)
    assert len(plan["pairs"]) == 30
    assert sum(
        pair["planned_publication_order"] == "treatment_first"
        for pair in plan["pairs"]
    ) == 15
    assert sum(
        pair["planned_publication_order"] == "control_first"
        for pair in plan["pairs"]
    ) == 15
    quote_ids = [
        member["quote_id"]
        for pair in plan["pairs"]
        for member in pair["members"]
    ]
    assert len(quote_ids) == len(set(quote_ids)) == 60
    assert set(quote_ids) <= set(bundle["catalogue"]["entries"])
    for pair in plan["pairs"]:
        assert len({member["arm"] for member in pair["members"]}) == 2
        assert all(
            bundle["candidates"][
                next(
                    index
                    for index, candidate in enumerate(bundle["candidates"])
                    if candidate["quote_id"] == member["quote_id"]
                )
            ]["topic"]
            == pair["topic"]
            for member in pair["members"]
        )
        assert all(
            experiment.quotation_length_band(
                bundle["quote_text_by_id"][member["quote_id"]]
            )
            == pair["quotation_length_band"]
            for member in pair["members"]
        )
        treatment = next(
            member for member in pair["members"] if member["arm"] == "treatment"
        )
        assert treatment["quote_id"] == experiment.treatment_quote_id_for_pair(
            bundle["catalogue_sha256"],
            pair["pair_id"],
            [member["quote_id"] for member in pair["members"]],
        )


def test_fewer_than_30_exact_pairs_fails_with_group_diagnostics(
    synthetic_plan_bundle,
) -> None:
    candidates = synthetic_plan_bundle["candidates"][:58]
    with pytest.raises(experiment.PlanPreparationError) as caught:
        experiment.construct_exact_matched_pairs(
            candidates,
            catalogue_sha256=synthetic_plan_bundle["catalogue_sha256"],
        )
    assert caught.value.diagnostics["possible_exact_pair_count"] == 29
    assert caught.value.diagnostics["groups"]


def test_odd_group_matching_chooses_deterministic_lowest_cost_omission() -> None:
    rows = []
    for index, length in enumerate((20, 21, 40, 41, 90)):
        text = chr(65 + index) * length
        rows.append(
            {
                "quote_id": experiment.sha256_text(text),
                "exact_quote_text": text,
                "topic": "economy",
                "verification_label": "same",
                "source_class": "same",
            }
        )
    matched, omitted = experiment._minimum_matching(rows)
    assert omitted == rows[-1]["quote_id"]
    assert sorted(
        abs(
            experiment.x_weighted_length(left["exact_quote_text"])
            - experiment.x_weighted_length(right["exact_quote_text"])
        )
        for left, right in matched
    ) == [1, 1]


def test_used_and_currently_ineligible_catalogue_entries_are_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quotes = [f"Candidate exact quote {index}." for index in range(4)]
    catalogue, quote_text_by_id = _small_catalogue(quotes)
    quote_ids = list(quote_text_by_id)
    packets = {
        quote_id: {
            "quote_id": quote_id,
            "quote_text": quote,
            "verification_status": "exact",
            "research_confidence": "high",
        }
        for quote_id, quote in quote_text_by_id.items()
    }
    analysis_items = {
        quote_id: {
            "text": quote,
            "analysis": {"primary_topics": ["economy"]},
        }
        for quote_id, quote in quote_text_by_id.items()
    }
    analysis_items[quote_ids[1]]["analysis"]["seasonality"] = {
        "preferred_windows": [{"start_mm_dd": "00-00", "end_mm_dd": "00-00"}],
        "hard_exclude_outside_windows": True,
    }
    monkeypatch.setattr(
        prepare,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: (packets, set()),
    )
    monkeypatch.setattr(
        prepare,
        "runtime_manifest_ids",
        lambda *_args, **_kwargs: ({quote_ids[0], quote_ids[1], quote_ids[3]}, {}),
    )
    monkeypatch.setattr(
        prepare,
        "load_quote_analysis",
        lambda _root: {
            "schema_version": 2,
            "analysis_kind": "quotes",
            "items": analysis_items,
        },
    )
    monkeypatch.setattr(
        prepare,
        "historical_context_config",
        lambda _root: dict(prepare.HISTORICAL_CONTEXT_DEFAULT),
    )
    monkeypatch.setattr(
        prepare,
        "packet_for_posted_quote",
        lambda packet_map, _unresolved, quote_id, _text: packet_map.get(quote_id),
    )
    monkeypatch.setattr(
        prepare,
        "packet_is_attributed_to_margaret_thatcher",
        lambda _packet: True,
    )
    monkeypatch.setattr(
        prepare,
        "format_context_reply_public",
        lambda _packet, **_kwargs: {
            "text": "Context",
            "rendering_mode": "public",
            "verification_label": "Exact wording",
            "source_class": "Hansard",
        },
    )
    candidates, excluded = prepare.candidate_metadata(
        runtime_root=tmp_path,
        quote_text_by_id=quote_text_by_id,
        catalogue=catalogue,
        used_ids={quote_ids[0]},
        enforce_unused=True,
    )
    assert [candidate["quote_id"] for candidate in candidates] == [quote_ids[3]]
    assert "used_history" in excluded[quote_ids[0]]
    assert "currently_hard_seasonally_excluded" in excluded[quote_ids[1]]
    assert "outside_runtime_eligible_partition" in excluded[quote_ids[2]]


def test_runtime_manifest_aliases_resolve_to_exact_source_identities() -> None:
    quote_text_by_id, _lines, _source = prepare.canonical_quote_source(PROJECT_ROOT)
    packets, _unresolved = prepare.load_and_validate_corpus(
        PROJECT_ROOT / prepare.RESEARCH_RELATIVE_PATH,
        require_source_role_audit=True,
    )
    eligible_ids, aliases = prepare.runtime_manifest_ids(
        PROJECT_ROOT,
        quote_text_by_id,
        packets,
    )
    assert aliases
    assert set(aliases.values()) <= eligible_ids
    assert eligible_ids <= set(quote_text_by_id)
    assert not set(aliases).issubset(quote_text_by_id)


def test_stable_history_and_active_receipt_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_id = "a" * 64
    first = json.dumps([quote_id]).encode()
    reads = iter((first, b"[]"))
    monkeypatch.setattr(
        prepare,
        "stable_read_bytes",
        lambda *_args, **_kwargs: next(reads),
    )
    with pytest.raises(prepare.PreparationError, match="changed during capture"):
        prepare.capture_used_history(tmp_path)
    receipt = tmp_path / prepare.REGULAR_RECEIPT_NAME
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(prepare.PreparationError, match="blocked"):
        prepare.regular_receipt_must_be_absent(tmp_path)


def test_preview_preparation_writes_only_requested_outputs(
    synthetic_plan_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime_files = {
        "lines_used.json": b"[]\n",
        "bot_state.json": b'{"minimum_reader_version":2}\n',
        "engagement_analytics.sqlite3": b"do-not-read-or-change",
    }
    for name, content in runtime_files.items():
        runtime_root.joinpath(name).write_bytes(content)
    bundle = synthetic_plan_bundle
    history_source = b"[]\n"
    history_sha = hashlib.sha256(history_source).hexdigest()
    monkeypatch.setattr(
        prepare,
        "capture_used_history",
        lambda _root: (set(), history_source, history_sha),
    )
    monkeypatch.setattr(
        prepare,
        "validate_catalogue_from_paths",
        lambda *_args: (
            bundle["catalogue"],
            bundle["catalogue_sha256"],
            bundle["quote_text_by_id"],
        ),
    )
    monkeypatch.setattr(
        prepare,
        "candidate_metadata",
        lambda **_kwargs: (bundle["candidates"], {}),
    )
    output_root = tmp_path / "preview"
    args = argparse.Namespace(
        repository_root=PROJECT_ROOT,
        runtime_root=runtime_root,
        catalogue_path=CATALOGUE_PATH,
        plan_path=output_root / "proposed_plan.json",
        report_path=output_root / "proposed_plan.md",
        manifest_path=output_root / "proposed_plan_manifest.json",
        state_path=None,
        plan_kind="preview",
        require_plan_kind=None,
        prepare_plan=True,
        validate_plan=False,
        status=False,
    )
    before = {
        name: runtime_root.joinpath(name).read_bytes() for name in runtime_files
    }
    manifest = prepare.prepare_plan(args)
    after = {
        name: runtime_root.joinpath(name).read_bytes() for name in runtime_files
    }
    assert before == after
    assert manifest["non_live_preview"] is True
    assert manifest["reservation_created"] is False
    assert manifest["network_requests_made"] == 0
    assert json.loads(args.plan_path.read_text())["pair_count"] == 30
    assert "NON-LIVE PREVIEW" in args.report_path.read_text()
    assert stat.S_IMODE(args.plan_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(args.report_path.stat().st_mode) == 0o600


def test_source_default_disabled_does_not_load_or_create_experiment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", False)
    monkeypatch.setattr(
        bot,
        "load_engagement_question_runtime_plan",
        lambda: pytest.fail("disabled mode loaded the plan or catalogue"),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail("disabled mode created state"),
    )
    state: dict = {}
    assert bot.initialise_engagement_question_experiment(
        state,
        current_epoch=1_800_000_000,
    ) == (None, None)
    assert state == {}
    monkeypatch.setattr(
        bot,
        "engagement_question_notification_output_path",
        str(tmp_path / "notification.json"),
    )
    assert bot.publish_pending_engagement_question_notification(state) is False
    assert not (tmp_path / "notification.json").exists()


def test_enabling_requires_notification_output_path() -> None:
    errors = bot.validate_runtime_config_values(
        {
            "engagement_question_experiment_enabled": True,
            "engagement_question_experiment_plan_path": (
                "engagement_question_experiment/active_plan.json"
            ),
            "engagement_question_notification_output_path": "",
        }
    )
    assert any("notification_output_path is required" in error for error in errors)


def test_disabled_quote_selection_preserves_rng_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {"line_no": index, "text": str(index), "quote_hash": str(index), "weight": 1.0}
        for index in range(5)
    ]
    monkeypatch.setattr(
        bot,
        "quote_candidates_for_current_cycle",
        lambda _used, **_kwargs: copy.deepcopy(candidates),
    )
    random.seed(9173)
    before = random.getstate()
    selected_without_exclusion = bot.choose_unused_line_candidate(set())
    after_without_exclusion = random.getstate()
    random.setstate(before)
    selected_with_empty_exclusion = bot.choose_unused_line_candidate(
        set(),
        excluded_quote_hashes=set(),
    )
    after_with_empty_exclusion = random.getstate()
    assert selected_without_exclusion == selected_with_empty_exclusion
    assert after_without_exclusion == after_with_empty_exclusion


def test_reservation_pause_and_calendar_scheduling(
    synthetic_plan_bundle,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    state = experiment.new_experiment_state(plan)
    assert experiment.reserved_quote_ids(plan, state) == set()
    experiment.start_next_pair(state, plan)
    assert len(experiment.reserved_quote_ids(plan, state)) == 60
    first_epoch = int(datetime(2026, 8, 30, 9, tzinfo=timezone.utc).timestamp())
    _confirm_current(bundle, state, epoch=first_epoch, post_id="1001")
    assert len(experiment.reserved_quote_ids(plan, state)) == 59
    experiment.set_experiment_paused(state, paused=True, plan=plan)
    assert len(experiment.reserved_quote_ids(plan, state)) == 59
    member, reason = experiment.member_for_current_opportunity(
        plan,
        state,
        current_epoch=first_epoch + 3600,
    )
    assert member is None and reason == "experiment_not_active"
    experiment.set_experiment_paused(state, paused=False, plan=plan)
    _confirm_current(bundle, state, epoch=first_epoch + 7200, post_id="1002")
    assert state["completed_pair_count"] == 1
    member, reason = experiment.member_for_current_opportunity(
        plan,
        state,
        current_epoch=first_epoch + 8000,
    )
    assert member is None
    assert reason == "experimental_publication_already_confirmed_today"
    next_day = first_epoch + 24 * 3600
    member, reason = experiment.member_for_current_opportunity(
        plan,
        state,
        current_epoch=next_day,
    )
    assert member is None and reason == "pair_start_required"
    experiment.start_next_pair(state, plan)
    assert state["active_pair_id"] == plan["pairs"][1]["pair_id"]


def test_bot_temporary_disable_pauses_but_retains_all_reservations(
    synthetic_plan_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = synthetic_plan_bundle
    state = experiment.new_experiment_state(bundle["plan"])
    experiment.start_next_pair(state, bundle["plan"])
    wrapped = {"engagement_question_experiment": state}
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", False)
    monkeypatch.setattr(
        bot,
        "load_engagement_question_runtime_plan",
        lambda: (
            bundle["plan"],
            bundle["catalogue"],
            bundle["quote_text_by_id"],
        ),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    plan, member, reservations = bot.engagement_question_opportunity(
        wrapped,
        current_epoch=1_800_000_000,
    )
    assert plan == bundle["plan"]
    assert member is None
    assert state["status"] == "paused"
    assert len(reservations) == 60


def test_started_plan_disappearance_invalidates_trial_without_reservation(
    synthetic_plan_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = synthetic_plan_bundle
    state = experiment.new_experiment_state(bundle["plan"])
    experiment.start_next_pair(state, bundle["plan"])
    wrapped = {"engagement_question_experiment": state}
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", True)
    monkeypatch.setattr(
        bot,
        "load_engagement_question_runtime_plan",
        lambda: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    plan, member, reservations = bot.engagement_question_opportunity(
        wrapped,
        current_epoch=1_800_000_000,
    )
    assert plan is None and member is None and reservations == set()
    assert state["status"] == "invalid"
    assert state["current_deferral_reason"]["code"] == (
        "configured_plan_unavailable_or_invalid"
    )


def test_carry_over_completion_blocks_new_pair_that_date(
    synthetic_plan_bundle,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    state = experiment.new_experiment_state(plan)
    experiment.start_next_pair(state, plan)
    day_one = int(datetime(2026, 8, 30, 22, tzinfo=timezone.utc).timestamp())
    day_two = day_one + 3 * 3600
    _confirm_current(bundle, state, epoch=day_one, post_id="2001")
    _confirm_current(bundle, state, epoch=day_two, post_id="2002")
    assert experiment.local_date_for_epoch(day_one) != experiment.local_date_for_epoch(day_two)
    member, reason = experiment.member_for_current_opportunity(
        plan,
        state,
        current_epoch=day_two + 3600,
    )
    assert member is None
    assert reason == "experimental_publication_already_confirmed_today"


def test_exactly_30_pairs_complete_and_recovery_is_idempotent(
    synthetic_plan_bundle,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    state = experiment.new_experiment_state(plan)
    epoch = int(datetime(2026, 9, 1, 8, tzinfo=timezone.utc).timestamp())
    latest_binding = None
    latest_member = None
    latest_exact = latest_public = None
    for pair_index in range(30):
        experiment.start_next_pair(state, plan)
        for position in range(2):
            latest_member, latest_exact, latest_public = _current_member(
                bundle,
                state,
                epoch + position * 7200,
            )
            latest_binding = experiment.build_attempt_binding(
                plan=plan,
                state=state,
                member=latest_member,
                exact_quote_text=latest_exact,
                public_text=latest_public,
            )
            assert experiment.apply_confirmed_publication(
                state,
                binding=latest_binding,
                plan=plan,
                post_id=str(3000 + pair_index * 2 + position),
                published_epoch=epoch + position * 7200,
                exact_quote_text=latest_exact,
                public_text=latest_public,
                approved_question_body=latest_member["approved_question_body"],
            )
        epoch += 24 * 3600
    assert state["status"] == "completed"
    assert state["completed_pair_count"] == 30
    assert state["treatment_publication_count"] == 30
    assert len(state["confirmed_publications"]) == 60
    assert state["latest_treatment_notification_identity"]["document"][
        "treatment_number"
    ] == 30
    assert state["latest_treatment_notification_identity"]["document"][
        "target_treatment_count"
    ] == 30
    assert experiment.reserved_quote_ids(plan, state) == set()
    assert latest_binding is not None and latest_member is not None
    assert experiment.apply_confirmed_publication(
        state,
        binding=latest_binding,
        plan=None,
        post_id=str(3000 + 29 * 2 + 1),
        published_epoch=epoch - 24 * 3600 + 7200,
        exact_quote_text=str(latest_exact),
        public_text=str(latest_public),
        approved_question_body=latest_member["approved_question_body"],
    ) is False
    assert state["completed_pair_count"] == 30


def test_exact_payload_binding_and_canonical_identity_remain_distinct(
    synthetic_plan_bundle,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    state = experiment.new_experiment_state(plan)
    experiment.start_next_pair(state, plan)
    epoch = int(datetime(2026, 8, 30, 10, tzinfo=timezone.utc).timestamp())
    member, exact, public = _current_member(bundle, state, epoch)
    if member["arm"] == "control":
        _confirm_current(bundle, state, epoch=epoch, post_id="3901")
        member, exact, public = _current_member(bundle, state, epoch + 3600)
    assert member["arm"] == "treatment"
    assert public == exact + "\n\nQuestion — " + member["approved_question_body"]
    assert public != exact
    binding = experiment.build_attempt_binding(
        plan=plan,
        state=state,
        member=member,
        exact_quote_text=exact,
        public_text=public,
    )
    assert binding["canonical_quote_sha256"] == experiment.sha256_text(exact)
    assert binding["public_text_sha256"] == experiment.sha256_text(public)
    with pytest.raises(experiment.ExperimentValidationError):
        experiment.validate_attempt_binding(
            binding,
            plan=plan,
            exact_quote_text=exact,
            public_text=public + " ",
        )
    altered_transition = copy.deepcopy(binding)
    altered_transition["expected_transition"][
        "completed_pair_count_after"
    ] += 1
    with pytest.raises(
        experiment.ExperimentValidationError,
        match="internally inconsistent",
    ):
        experiment.validate_attempt_binding(
            altered_transition,
            plan=plan,
            exact_quote_text=exact,
            public_text=public,
        )
    entry = copy.deepcopy(bundle["catalogue"]["entries"][member["quote_id"]])
    entry["complete_treatment_sha256"] = "0" * 64
    with pytest.raises(experiment.ExperimentValidationError):
        experiment.validate_complete_public_text(
            exact_quote_text=exact,
            catalogue_entry=entry,
            arm="treatment",
        )
    assert experiment.validate_complete_public_text(
        exact_quote_text=exact,
        catalogue_entry=bundle["catalogue"]["entries"][member["quote_id"]],
        arm="control",
    ) == exact


def test_experimental_attempt_pending_receipt_and_restart_recovery_keep_identity(
    synthetic_plan_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = synthetic_plan_bundle
    state = experiment.new_experiment_state(bundle["plan"])
    experiment.start_next_pair(state, bundle["plan"])
    epoch = int(datetime(2026, 8, 30, 10, tzinfo=timezone.utc).timestamp())
    attempt, envelope, member, exact, public = _experimental_attempt(
        bundle,
        state,
        epoch=epoch,
    )
    assert attempt["schema_version"] == 6
    assert attempt["text"] == public
    assert attempt["selected_identity"]["quote_hash"] == experiment.sha256_text(
        exact
    )
    assert bot.engagement_experiment_attempt_envelope_is_valid(
        envelope,
        public_text=public,
        quote_hash=member["quote_id"],
        plan=bundle["plan"],
    )
    attempting = copy.deepcopy(attempt)
    attempting["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="6001",
        confirmation_epoch=epoch + 5,
    )
    assert pending["source_attempt"]["text"] == public
    receipt = bot.materialize_bound_regular_schedule_receipt(pending)
    assert receipt["schema_version"] == 4
    assert receipt["text"] == public
    assert receipt["quote_text"] == exact
    assert receipt["quote_hash"] == member["quote_id"]
    assert receipt["engagement_question_experiment"] == envelope
    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    event = bot.engagement_experiment_event_fields(receipt)
    assert event == {
        "engagement_experiment_id": experiment.EXPERIMENT_ID,
        "engagement_experiment_plan_sha256": bundle["plan"]["plan_sha256"],
        "engagement_experiment_pair_id": member["pair_id"],
        "engagement_experiment_arm": member["arm"],
        "engagement_experiment_member_position": member["position"],
        "engagement_experiment_publication_order": member["publication_order"],
        "engagement_experiment_sequence": 1,
        "engagement_question_present": member["arm"] == "treatment",
        "engagement_approved_question_sha256": member[
            "approved_question_sha256"
        ],
        "engagement_public_text_sha256": experiment.sha256_text(public),
    }
    wrapped = {"engagement_question_experiment": state}
    monkeypatch.setattr(
        bot,
        "load_engagement_question_runtime_plan",
        lambda: (_ for _ in ()).throw(RuntimeError("plan temporarily absent")),
    )
    assert bot.apply_confirmed_engagement_experiment_receipt(receipt, wrapped) is True
    assert len(state["confirmed_publications"]) == 1
    assert bot.apply_confirmed_engagement_experiment_receipt(receipt, wrapped) is False
    assert len(state["confirmed_publications"]) == 1


def test_ordinary_attempt_and_receipt_schema_remain_unchanged() -> None:
    text = "An ordinary canonical quotation."
    quote_id = bot.quote_text_hash(text)
    attempt = bot.build_main_post_attempt(
        lane="quote_image",
        text=text,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={
            "quote_hash": quote_id,
            "line_no": 0,
            "source_line_number": 1,
            "image_basename": "t01.jpg",
            "image_no": 0,
        },
        recovery_plan=_regular_recovery_plan(quote_id),
        attempt_epoch=1_800_000_000,
    )
    assert attempt["schema_version"] == 5
    assert attempt["text"] == text
    assert "engagement_question_experiment" not in attempt
    attempting = copy.deepcopy(attempt)
    attempting["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="6002",
        confirmation_epoch=1_800_000_005,
    )
    receipt = bot.materialize_bound_regular_schedule_receipt(pending)
    assert receipt["schema_version"] == 3
    assert receipt["text"] == text
    assert "quote_text" not in receipt
    assert "engagement_question_experiment" not in receipt
    assert bot.engagement_experiment_event_fields(receipt) == {}


def test_image_mismatch_restores_history_and_uses_existing_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images_used = {"old.jpg"}
    calls: list[tuple[bool, bool, str]] = []
    monkeypatch.setattr(bot, "log_generated_image_spacing_status", lambda _state: True)

    def mismatch(used: set, _quote: dict, _state: dict, **kwargs):
        calls.append(
            (
                kwargs["force_cycle_reset"],
                kwargs["avoid_last_image_at_cycle_boundary"],
                kwargs["selection_phase"],
            )
        )
        used.add(f"mutated-{len(calls)}.jpg")
        raise bot.QuoteSpecificImageMismatch("none")

    monkeypatch.setattr(bot, "choose_matched_unused_image", mismatch)
    with pytest.raises(bot.QuoteSpecificImageMismatch):
        bot.choose_engagement_question_image(
            images_used,
            {"quote_hash": "a" * 64},
            {},
        )
    assert images_used == {"old.jpg"}
    assert calls == [
        (False, True, "normal"),
        (True, True, "forced_cycle_reset"),
        (True, False, "last_image_fallback"),
    ]


def test_confirmed_treatment_notification_is_exact_retryable_and_idempotent(
    synthetic_plan_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = synthetic_plan_bundle
    plan = bundle["plan"]
    state = experiment.new_experiment_state(plan)
    experiment.start_next_pair(state, plan)
    epoch = int(datetime(2026, 8, 30, 9, tzinfo=timezone.utc).timestamp())
    first, _binding = _confirm_current(
        bundle,
        state,
        epoch=epoch,
        post_id="4001",
    )
    if first["arm"] == "control":
        assert state["latest_treatment_notification_identity"] is None
        second, _binding = _confirm_current(
            bundle,
            state,
            epoch=epoch + 7200,
            post_id="4002",
        )
        assert second["arm"] == "treatment"
        treatment_post_id = "4002"
    else:
        treatment_post_id = "4001"
    identity = state["latest_treatment_notification_identity"]
    assert identity["post_id"] == treatment_post_id
    document = identity["document"]
    assert document["treatment_number"] == 1
    assert document["target_treatment_count"] == 30
    assert document["post_url"] == (
        f"https://x.com/MrsMThatcher/status/{treatment_post_id}"
    )
    output = tmp_path / "treatment.json"
    monkeypatch.setattr(
        bot,
        "engagement_question_notification_output_path",
        str(output),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    real_atomic = bot.atomic_write_json
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    wrapped_state = {"engagement_question_experiment": state}
    assert bot.publish_pending_engagement_question_notification(wrapped_state) is False
    assert identity["delivered"] is False
    assert not output.exists()
    monkeypatch.setattr(bot, "atomic_write_json", real_atomic)
    assert bot.publish_pending_engagement_question_notification(wrapped_state) is True
    assert json.loads(output.read_text()) == document
    output_before = output.read_bytes()
    assert bot.publish_pending_engagement_question_notification(wrapped_state) is False
    assert output.read_bytes() == output_before


def test_historical_context_obligation_uses_canonical_quote_not_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class Store:
        def enqueue(self, parent_post_id: str, **kwargs):
            captured.update({"parent_post_id": parent_post_id, **kwargs})
            return {
                "main_post": {"state": "main_post_confirmed"},
                "context_reply": {"state": "pending"},
            }

    canonical = "The exact canonical quotation."
    public = canonical + "\n\nQuestion — Why does this matter?"
    monkeypatch.setitem(bot.historical_context_reply, "enabled", True)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "historical_context_outbox_store", lambda: Store())
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    quote_id = bot.quote_text_hash(canonical)
    bot.enqueue_historical_context_obligation(
        {
            "post_id": "5001",
            "quote_post_epoch": 1_800_000_000,
            "quote_hash": quote_id,
            "quote_text": canonical,
            "text": public,
        }
    )
    assert captured["quote_id"] == quote_id
    assert captured["quote_text"] == canonical
    assert "Question —" not in captured["quote_text"]


def test_home_assistant_package_is_treatment_only_and_stale_guarded() -> None:
    package = (
        PROJECT_ROOT
        / "deploy"
        / "home-assistant"
        / "mrs_m_thatcher_engagement_question.yaml"
    ).read_text(encoding="utf-8")
    assert "/config/.runtime/mrs_m_thatcher_engagement_question.json" in package
    assert "sensor.mrs_m_thatcher_engagement_question" in package
    assert "notify.millie_powerwall_alert_devices" in package
    assert "age <= 600" in package
    assert "treatment_number" in package
    assert "MrsMThatcher treatment post" in package
    assert "mrs_m_thatcher_treatment_{{ trigger.to_state.state }}" in package
    assert "control" not in package.casefold()


def test_reply_pipeline_and_historical_context_formatter_are_unchanged() -> None:
    assert hashlib.sha256(
        PROJECT_ROOT.joinpath("tested_reply_pipeline.py").read_bytes()
    ).hexdigest() == "4972991d89e7438075a61f5ae84a10b6743e049582346e7af407ca092b1f2d1d"
    assert hashlib.sha256(
        PROJECT_ROOT.joinpath("historical_context_formatter.py").read_bytes()
    ).hexdigest() == "55982ee4906c020e6dc3fbcd0ea09950d4de6a3e3a75968240ca04163dd806a8"


def test_pure_experiment_module_has_no_network_provider_or_posting_import() -> None:
    source = PROJECT_ROOT.joinpath("engagement_question_experiment.py").read_text(
        encoding="utf-8"
    )
    assert "import requests" not in source
    assert "import openai" not in source.casefold()
    assert "import xai" not in source.casefold()
    assert "create_tweet" not in source
    assert "post_random_quote" not in source
    tool_source = PROJECT_ROOT.joinpath(
        "tools/prepare_engagement_question_experiment.py"
    ).read_text(encoding="utf-8")
    assert "import requests" not in tool_source
    assert "import openai" not in tool_source.casefold()
    assert "import xai" not in tool_source.casefold()
    assert "os.umask(" not in tool_source
