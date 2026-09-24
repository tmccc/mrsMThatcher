from __future__ import annotations

import copy
import json
import os
import random
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.quote_candidate_overrides import patch_completed_research_quotes
from tools import simulate_regular_post_futures as sim


from tests.helpers.selector_simulation import (
    DETERMINISTIC_FLAGS, build_simulator_snapshot,
    isolated_simulator_bot, run_private_future,
)

ROOT = Path(__file__).resolve().parents[1]


def test_production_bot_import_is_safe_without_inherited_openai_environment(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.pop("OPENAI_API_BASE_URL", None)
    env.pop("OPENAI_API_KEY", None)
    env["MRS_TEST_SESSION_DIR"] = str(tmp_path)
    script = """
import os
from pathlib import Path
from tools import simulate_regular_post_futures as simulator

bot = simulator.import_production_bot(Path(os.environ["MRS_TEST_SESSION_DIR"]))
assert bot.OPENAI_BASE == "http://127.0.0.1:9/v1"
assert bot.OPENAI_API_KEY == "simulator-disabled"
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout


@pytest.fixture(scope="module")
def simulator_snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_simulator_snapshot(tmp_path_factory)


def configure_real_selector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot: Path,
) -> tuple[dict, set[str], set[str]]:
    for key, value in DETERMINISTIC_FLAGS.items():
        image_policy = sim.historical_image_selection(bot)
        monkeypatch.setattr(image_policy if key in image_policy.CONFIG_DEFAULTS else bot, key, value)
    monkeypatch.setattr(bot, "LINES_FILE", snapshot / "mrsMThatcher.txt")
    monkeypatch.setattr(bot, "QUOTE_ANALYSIS_FILE", snapshot / "quote_analysis.json")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", snapshot)
    monkeypatch.setattr(bot, "COMPLETED_QUOTE_RESEARCH_FILE", snapshot / "research_packets.json")
    monkeypatch.setattr(
        bot,
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        snapshot / "runtime_eligible_quote_manifest.json",
    )
    validated_eligible_ids = frozenset(
        bot.load_completed_research_quote_hashes()
    )
    patch_completed_research_quotes(monkeypatch, bot, lambda: set(validated_eligible_ids))
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", snapshot / "image_analysis.json")
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IMAGE_ANALYSIS_FILE", str(snapshot / "generated_image_analysis.json"))
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(snapshot / "images" / "t*"))
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IMAGE_DIR", str(snapshot / "generated_images"))
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(snapshot / "original_image_editorial_analysis_experiment_v1.json"))
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IDENTITY_AUDIT_FILE", str(snapshot / "generated_image_identity_dependence_audit.json"))
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    monkeypatch.setattr(sim.historical_image_selection(bot), "_GENERATED_IDENTITY_AUDIT_CACHE", {})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_788_453_600)  # 2026-09-01 12:00 local-ish
    original_sha = bot.current_image_sha256
    sha_cache: dict[str, str] = {}

    def cached_sha(path: str) -> str:
        return sha_cache.setdefault(path, original_sha(path))

    monkeypatch.setattr(bot, "current_image_sha256", cached_sha)
    state = json.loads((snapshot / "bot_state.json").read_text(encoding="utf-8"))
    state["original_regular_posts_since_generated_image"] = sim.historical_image_selection(bot).GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN
    images_used = set(json.loads((snapshot / "images_used.json").read_text(encoding="utf-8")))
    lines_used = set(json.loads((snapshot / "lines_used.json").read_text(encoding="utf-8")))
    return state, images_used, lines_used


def test_private_writer_rejects_production_and_allows_private_paths(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    writer = sim.PrivateWriter(session)
    writer.atomic_json(session / "state.json", {"ok": True})
    assert json.loads((session / "state.json").read_text()) == {"ok": True}
    with pytest.raises(sim.SimulationSafetyError):
        writer.atomic_json(ROOT / "bot_state.json", {"bad": True})


@pytest.mark.parametrize(
    "path",
    [ROOT, ROOT / "bot_state.json", ROOT / "mrsMThatcher.log", ROOT / "mrsMThatcher.local.json"],
)
def test_validate_session_path_rejects_live_paths(path: Path) -> None:
    with pytest.raises(sim.SimulationSafetyError):
        sim.validate_session_path(path)


def test_validate_session_path_accepts_dedicated_output() -> None:
    accepted = sim.validate_session_path(ROOT / "simulation_runs" / "safe-test")
    assert accepted == (ROOT / "simulation_runs" / "safe-test").resolve()


def test_network_guard_blocks_socket_connections() -> None:
    with sim.block_process_network():
        with pytest.raises(sim.SimulationSafetyError):
            socket.create_connection(("example.com", 443))
        with socket.socket() as stream:
            with pytest.raises(sim.SimulationSafetyError):
                stream.connect_ex(("127.0.0.1", 9))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
            with pytest.raises(sim.SimulationSafetyError):
                datagram.sendto(b"test", ("127.0.0.1", 9))


def test_hard_guards_block_post_upload_reply_lock_and_network(tmp_path: Path) -> None:
    writer = sim.PrivateWriter(tmp_path)
    guarded_names = (
        "upload_media",
        "create_post",
        "acquire_instance_lock",
        "post_random_quote",
        "post_next_meme",
        "maybe_reply_to_mentions",
        "maybe_reply_to_quote_tweets",
        "write_regular_post_receipt",
        "write_meme_post_receipt",
        "remove_regular_post_receipt",
        "remove_meme_post_receipt",
        "_reply_assembly",
        "atomic_write_json",
        "save_used_set",
        "save_quote_used_hashes",
        "save_image_used_basenames",
        "save_state",
    )
    original_bot_helpers = {name: getattr(bot, name) for name in guarded_names}
    original_requests = {name: getattr(bot.requests, name) for name in ("request", "get", "post", "put", "patch", "delete")}
    try:
        sim.install_hard_guards(bot, writer)
        for name in (
            "upload_media", "create_post", "acquire_instance_lock",
            "post_random_quote", "post_next_meme", "maybe_reply_to_mentions", "maybe_reply_to_quote_tweets",
        ):
            with pytest.raises(sim.SimulationSafetyError):
                getattr(bot, name)()
        with pytest.raises(sim.SimulationSafetyError):
            bot.requests.get("https://example.com")
    finally:
        for name, value in original_bot_helpers.items():
            setattr(bot, name, value)
        for name, value in original_requests.items():
            setattr(bot.requests, name, value)


def test_real_selector_is_reproducible_and_shadows_receive_exact_candidate_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulator_snapshot: Path,
) -> None:
    state, images_used, lines_used = configure_real_selector(
        monkeypatch, tmp_path, simulator_snapshot,
    )
    first_state = copy.deepcopy(state)
    first_images = set(images_used)
    first_lines = set(lines_used)
    bot.random.seed(71001)
    first = sim.select_with_production_recovery(bot, first_lines, first_images, first_state)

    second_state = copy.deepcopy(state)
    second_images = set(images_used)
    second_lines = set(lines_used)
    bot.random.seed(71001)
    second = sim.select_with_production_recovery(bot, second_lines, second_images, second_state)

    assert first["quote"]["quote_hash"] == second["quote"]["quote_hash"]
    assert [row["basename"] for row in first["scored"]] == [row["basename"] for row in second["scored"]]
    assert [row["score"] for row in first["scored"]] == [row["score"] for row in second["scored"]]
    assert first["image"]["basename"] == second["image"]["basename"]
    assert first["selection_phase"] == second["selection_phase"]
    assert first["image"]["origin_quote_match"] == second["image"]["origin_quote_match"]
    assert first["image"]["origin_quote_boost"] == second["image"]["origin_quote_boost"]
    assert first["original_editorial_shadow"] is not None
    assert first["generated_identity_shadow"] is not None
    assert first["image"] in first["scored"]


def test_shadow_evaluation_does_not_consume_production_rng(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulator_snapshot: Path,
) -> None:
    state, images_used, lines_used = configure_real_selector(
        monkeypatch, tmp_path, simulator_snapshot,
    )
    bot.random.seed(991)
    selection = sim.select_with_production_recovery(bot, set(lines_used), set(images_used), copy.deepcopy(state))
    after_shadow = bot.random.getstate()

    bot.random.seed(991)
    with sim.capture_shadow_selection(bot):
        sim.historical_image_selection(bot).choose_regular_quote_image_pair(set(lines_used), set(images_used), copy.deepcopy(state))
    assert bot.random.getstate() == after_shadow
    assert selection["image"]["basename"]


def test_exact_candidate_capture_supports_active_identity_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulator_snapshot: Path,
) -> None:
    state, images_used, lines_used = configure_real_selector(
        monkeypatch, tmp_path, simulator_snapshot,
    )
    monkeypatch.setattr(sim.historical_image_selection(bot), "ENABLE_GENERATED_IDENTITY_POLICY_SCORING", True)
    monkeypatch.setattr(sim.historical_image_selection(bot), "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    bot.random.seed(8451)
    quote = bot.choose_unused_line_candidate(lines_used)
    result = sim.select_policy_image_with_recovery(
        bot, quote, images_used, state, "production",
    )
    assert result["image"] in result["scored"]
    assert result["selection_phase"] in {"normal", "forced_cycle_reset", "last_image_fallback"}


def test_shadow_capture_can_enable_comparison_after_normal_selector_skips_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulator_snapshot: Path,
) -> None:
    state, images_used, lines_used = configure_real_selector(
        monkeypatch, tmp_path, simulator_snapshot,
    )
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    quote = bot.choose_unused_line_candidate(lines_used)

    with sim.capture_shadow_selection(bot) as capture:
        chosen = bot.choose_matched_unused_image(images_used, quote, state)
        assert bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING is False

    assert bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING is True
    assert chosen is capture["chosen"]
    assert "original_editorial_adjustment" not in chosen
    assert capture["original_editorial_shadow"]["production_winner"] == chosen["basename"]
    assert "selection_applied" not in capture["original_editorial_shadow"]


def test_retained_historical_generated_spacing_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    generated = "tg_" + "a" * 64 + ".png"
    state = {"original_regular_posts_since_generated_image": 2}
    sim.historical_image_selection(bot).update_regular_generated_image_spacing_state(state, generated)
    assert state["original_regular_posts_since_generated_image"] == 0
    assert sim.historical_image_selection(bot).generated_images_allowed_by_spacing(state) is False
    sim.historical_image_selection(bot).update_regular_generated_image_spacing_state(state, "t01.jpg")
    assert state["original_regular_posts_since_generated_image"] == 1
    assert sim.historical_image_selection(bot).generated_images_allowed_by_spacing(state) is False
    sim.historical_image_selection(bot).update_regular_generated_image_spacing_state(state, "t02.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert sim.historical_image_selection(bot).generated_images_allowed_by_spacing(state) is True


def test_snapshotted_generated_settings_stay_with_offline_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_policy = sim.historical_image_selection(bot)
    monkeypatch.setattr(image_policy, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(image_policy, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 4)
    (tmp_path / "mrsMThatcher.local.json").write_text(
        json.dumps({"ENABLE_GENERATED_IMAGE_POOL": True, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 6}),
        encoding="utf-8",
    )

    sim.apply_snapshot_config(bot, tmp_path)

    assert image_policy.ENABLE_GENERATED_IMAGE_POOL is True
    assert image_policy.GENERATED_IMAGE_ORIGIN_QUOTE_BOOST == 6
    assert not hasattr(bot, "ENABLE_GENERATED_IMAGE_POOL")
    assert not hasattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST")
    assert not hasattr(bot, "generated_identity_policy_selection")


def test_historical_mixed_pool_keeps_generated_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, simulator_snapshot: Path,
) -> None:
    configure_real_selector(monkeypatch, tmp_path, simulator_snapshot)
    image_policy = sim.historical_image_selection(bot)
    historical_names = {Path(path).name for path in image_policy.current_image_paths()}
    generated_names = {name for name in historical_names if bot.generated_image_origin_quote_hash(name)}

    assert generated_names
    assert generated_names <= image_policy.load_image_analysis()["path_index"].keys()
    assert generated_names.isdisjoint(Path(path).name for path in bot.current_image_paths())
    assert generated_names.isdisjoint(bot.load_image_analysis()["path_index"])


def test_historical_mixed_pool_keeps_numeric_history_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_policy = sim.historical_image_selection(bot)
    monkeypatch.setattr(image_policy, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "image_corpus_verified_for_legacy_migration", lambda *_: True)
    history = {0, "t01.jpg"}

    assert image_policy.normalise_image_used_basenames(history, ["t01.jpg"]) == (history, False)


def test_future_policy_event_marks_policy_neutral_tie_without_changing_selection() -> None:
    image_policy = sim.historical_image_selection(bot)
    scored = [
        {"basename": name, "score": 31.8, "image_source": "original"}
        for name in ("t34.jpg", "t45.jpg")
    ]
    rows, eligible = image_policy.generated_identity_policy_selection(scored, audit_by_basename={})
    rng_state = random.Random(17).getstate()
    winner = image_policy._choice_with_random_state(eligible, rng_state)
    before = random.getstate()

    payload = image_policy.generated_identity_policy_applied_result(
        {"quote_hash": "a" * 64}, winner, scored, rows, len(eligible),
        selection_phase="normal", selection_rng_state=rng_state,
    )

    assert random.getstate() == before
    assert payload["baseline_winner_differs"] is False
    assert payload["winner_changed_by_policy"] is False
    assert payload["policy_effect"] == "none"
    assert payload["replacement_source_transition"] == "unchanged"


@pytest.mark.parametrize("basename", ["t01.jpg", "tg_" + "a" * 64 + ".png"])
def test_simulated_success_matches_production_receipt_selection_state(
    basename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(sim.historical_image_selection(bot), "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    epoch = 1_788_453_600
    initial = {
        "last_quote_post_epoch": epoch - 100,
        "last_meme_post_epoch": epoch - 200,
        "original_regular_posts_since_generated_image": 2,
    }
    selection = {
        "quote": {"quote_hash": "b" * 64},
        "image": {"basename": basename},
    }
    simulated_state = copy.deepcopy(initial)
    simulated_lines: set[str] = set()
    simulated_images: set[str] = set()
    bot.random.seed(3412)
    sim.apply_simulated_success(
        bot, selection, simulated_state, simulated_lines, simulated_images, epoch, "run_0000", 1
    )

    bot.random.seed(3412)
    delay = bot.random.randint(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX)
    quote_fields, _ = bot.next_quote_schedule_fields(epoch, delay=delay)
    production_state = copy.deepcopy(initial)
    production_lines: set[str] = set()
    production_images: set[str] = set()
    bot._main_post_assembly().recovery_operation().apply_regular(
        {
            "post_id": "sim-run_0000-000001",
            "quote_hash": "b" * 64,
            "image_basename": basename,
            "quote_post_epoch": epoch,
            "next_quote_post_epoch": quote_fields["next_quote_post_epoch"],
            "text": "",
        },
        production_lines,
        production_images,
        production_state,
    )
    selection_fields = {
        "last_main_post_id", "last_quote_post_epoch", "last_regular_image_filename",
        "next_quote_post_epoch",
        "next_quote_schedule_version",
    }
    expected_spacing = 0 if bot.generated_image_origin_quote_hash(basename) else 2
    assert simulated_state["original_regular_posts_since_generated_image"] == expected_spacing
    assert simulated_lines == production_lines
    assert simulated_images == production_images
    assert {key: simulated_state.get(key) for key in selection_fields} == {
        key: production_state.get(key) for key in selection_fields
    }


def test_rng_state_round_trip() -> None:
    rng = random.Random(1234)
    expected = [rng.random() for _ in range(3)]
    encoded = sim.rng_state_encode(random.Random(1234).getstate())
    restored = random.Random()
    restored.setstate(sim.rng_state_decode(encoded))
    assert [restored.random() for _ in range(3)] == expected


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        '{}',
        '{"schema_version":2,"state_version":3,"internal_state":[],"gauss_next":null}',
        '{"schema_version":1,"state_version":3,"internal_state":[true],"gauss_next":null}',
    ],
)
def test_rng_state_decoder_rejects_legacy_or_malformed_payloads(value: str) -> None:
    with pytest.raises(sim.SimulationSafetyError):
        sim.rng_state_decode(value)


def test_consistent_group_snapshot_reads_complete_files(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"a": 1}\n')
    b.write_text('["b"]\n')
    result = sim.read_consistent_group([a, b])
    assert result[a] == b'{"a": 1}\n'
    assert result[b] == b'["b"]\n'


def test_consistent_group_requires_two_matching_logical_snapshots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"version": 2}')
    sequence = iter(
        [
            {path: b'{"version": 1}'},
            {path: b'{"version": 2}'},
            {path: b'{"version": 2}'},
            {path: b'{"version": 2}'},
        ]
    )
    monkeypatch.setattr(sim, "read_stable_group_once", lambda paths: next(sequence))
    assert sim.read_consistent_group([path], retries=2, quiescence_seconds=0) == {path: b'{"version": 2}'}


def test_snapshot_relationship_validation_rejects_last_image_missing_from_history(tmp_path: Path) -> None:
    payload = {
        tmp_path / "bot_state.json": json.dumps(
            {"last_quote_post_epoch": 10, "next_quote_post_epoch": 20, "last_regular_image_filename": "t02.jpg"}
        ).encode(),
        tmp_path / "images_used.json": b'["t01.jpg"]',
        tmp_path / "lines_used.json": b'[]',
    }
    with pytest.raises(RuntimeError, match="last regular image"):
        sim.validate_mutable_snapshot(payload)


def test_interrupted_resume_matches_uninterrupted_after_record_append(
    tmp_path: Path,
    simulator_snapshot: Path,
) -> None:
    with isolated_simulator_bot(tmp_path, simulator_snapshot) as private_bot:
        uninterrupted = tmp_path / "uninterrupted"
        interrupted = tmp_path / "interrupted"
        run_private_future(private_bot, uninterrupted, simulator_snapshot, posts=100)

        def fail(stage: str, post_index: int) -> None:
            if stage == "after_record_append" and post_index == 37:
                raise RuntimeError("intentional interruption")

        with pytest.raises(RuntimeError, match="intentional interruption"):
            run_private_future(
                private_bot, interrupted, simulator_snapshot, posts=100, failure_hook=fail,
            )
        run_private_future(
            private_bot, interrupted, simulator_snapshot, posts=100, resume=True,
        )

    assert (uninterrupted / "runs/run_0000/selections.jsonl").read_bytes() == (
        interrupted / "runs/run_0000/selections.jsonl"
    ).read_bytes()
    assert json.loads((uninterrupted / "runs/run_0000/checkpoint.json").read_text()) == json.loads(
        (interrupted / "runs/run_0000/checkpoint.json").read_text()
    )


def test_interrupted_resume_matches_uninterrupted_after_checkpoint(
    tmp_path: Path,
    simulator_snapshot: Path,
) -> None:
    with isolated_simulator_bot(tmp_path, simulator_snapshot) as private_bot:
        uninterrupted = tmp_path / "uninterrupted"
        interrupted = tmp_path / "interrupted"
        run_private_future(private_bot, uninterrupted, simulator_snapshot, posts=40)

        def fail(stage: str, post_index: int) -> None:
            if stage == "after_checkpoint" and post_index == 17:
                raise RuntimeError("intentional interruption")

        with pytest.raises(RuntimeError, match="intentional interruption"):
            run_private_future(
                private_bot, interrupted, simulator_snapshot, posts=40, failure_hook=fail,
            )
        run_private_future(
            private_bot, interrupted, simulator_snapshot, posts=40, resume=True,
        )

    assert (uninterrupted / "runs/run_0000/selections.jsonl").read_bytes() == (
        interrupted / "runs/run_0000/selections.jsonl"
    ).read_bytes()
    indices = [json.loads(line)["post_index"] for line in (interrupted / "runs/run_0000/selections.jsonl").read_text().splitlines()]
    assert indices == list(range(1, 41))


def test_candidate_detail_modes_do_not_change_core_future(
    tmp_path: Path,
    simulator_snapshot: Path,
) -> None:
    outputs = {}
    with isolated_simulator_bot(tmp_path, simulator_snapshot) as private_bot:
        for detail in ("none", "top10", "full"):
            directory = tmp_path / detail
            run_private_future(
                private_bot, directory, simulator_snapshot, posts=20, detail=detail,
            )
            records = [json.loads(line) for line in (directory / "runs/run_0000/selections.jsonl").read_text().splitlines()]
            outputs[detail] = [
                {key: value for key, value in record.items() if key != "candidate_detail"}
                for record in records
            ]
    assert outputs["none"] == outputs["top10"] == outputs["full"]


def test_reconcile_jsonl_discards_record_ahead_of_checkpoint(tmp_path: Path) -> None:
    writer = sim.PrivateWriter(tmp_path)
    path = tmp_path / "selections.jsonl"
    path.write_text('\n'.join(json.dumps({"post_index": index}) for index in (1, 2, 3)) + '\n')
    records = sim.reconcile_jsonl_to_checkpoint(writer, path, 2)
    assert [record["post_index"] for record in records] == [1, 2]
    assert [json.loads(line)["post_index"] for line in path.read_text().splitlines()] == [1, 2]


def test_summary_uses_comparable_and_policy_relevant_denominators() -> None:
    base = {
        "run_id": "run_0000",
        "virtual_timestamp": 1,
        "production_source": "original",
        "production_image": "t01.jpg",
        "production_score": 10,
        "origin_quote_match": False,
        "selection_phase": "normal",
        "quote_cycle_reset": False,
        "image_cycle_reset": False,
        "candidate_detail": [],
        "diagnostic_candidate_present": False,
        "diagnostic_candidate_origin_match": False,
    }
    changed = {
        **base,
        "original_editorial_shadow": {
            "production_shadow_rank": 2,
            "winner_changed": True,
            "production_editorial_adjustment": 1,
            "cap_hit": False,
            "shadow_original_winner": "t02.jpg",
            "production_winner": "t01.jpg",
        },
        "generated_identity_shadow": {
            "small_penalty_count": 1,
            "strong_penalty_count": 0,
            "origin_quote_only_excluded_count": 0,
            "winner_changed": True,
            "shadow_winner_source": "original",
            "shadow_winner": "t02.jpg",
            "excluded_generated_basenames": [],
            "penalised_generated_basenames": ["tg_x.png"],
            "production_identity_action": "original_unchanged",
        },
    }
    irrelevant = {
        **base,
        "virtual_timestamp": 2,
        "production_source": "generated",
        "production_image": "tg_y.png",
        "original_editorial_shadow": {
            "production_shadow_rank": None,
            "winner_changed": False,
            "cap_hit": False,
            "shadow_original_winner": "t01.jpg",
            "production_winner": "tg_y.png",
        },
        "generated_identity_shadow": {
            "small_penalty_count": 0,
            "strong_penalty_count": 0,
            "origin_quote_only_excluded_count": 0,
            "winner_changed": False,
            "shadow_winner_source": "generated",
            "shadow_winner": "tg_y.png",
            "excluded_generated_basenames": [],
            "penalised_generated_basenames": [],
            "production_identity_action": "generated_cross_quote_unrestricted",
        },
    }
    summary = sim.summarize_records([changed, irrelevant], 1.0)
    assert summary["original_editorial_shadow"]["winner_change_rate"] == 1.0
    assert summary["generated_identity_shadow"]["winner_change_rate"] == 1.0
    assert summary["generated_identity_shadow"]["policy_relevant_observations"] == 1
    matched = summary["original_editorial_shadow"]["matched_comparable_distribution"]
    assert matched["denominator"] == 1
    assert matched["production_original"]["observations"] == 1
    assert matched["editorial_shadow_preference"]["observations"] == 1
    identity_matched = summary["generated_identity_shadow"]["matched_all_observation_distribution"]
    assert identity_matched["production"]["observations"] == identity_matched["identity_shadow"]["observations"] == 2


def test_repeated_observational_preference_resets_when_production_uses_image() -> None:
    def record(index: int, production: str, shadow: str, changed: bool) -> dict:
        source = "original"
        return {
            "run_id": "run_0000", "virtual_timestamp": index, "production_source": source,
            "production_image": production, "production_score": 1.0, "origin_quote_match": False,
            "selection_phase": "normal", "quote_cycle_reset": False, "image_cycle_reset": False,
            "diagnostic_candidate_present": False, "diagnostic_candidate_origin_match": False,
            "original_editorial_shadow": {
                "production_shadow_rank": 2 if changed else 1, "winner_changed": changed,
                "production_editorial_adjustment": 0.0, "cap_hit": False,
                "shadow_original_winner": shadow, "production_winner": production,
            },
            "generated_identity_shadow": None,
        }
    records = [
        record(1, "t01.jpg", "t10.jpg", True),
        record(2, "t02.jpg", "t10.jpg", True),
        record(3, "t10.jpg", "t10.jpg", False),
        record(4, "t03.jpg", "t10.jpg", True),
    ]
    editorial = sim.summarize_records(records, 1.0)["original_editorial_shadow"]
    assert editorial["repeated_observational_preference_count"] == 1
    assert editorial["repeated_observational_preference_by_image"] == [("t10.jpg", 1)]


def test_cli_help_has_explicit_offline_safety(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        sim.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "Never posts" in help_text
    assert "network APIs" in help_text
