from __future__ import annotations

import copy
import json
import random
import socket
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_unit_helpers import bot
from tools import simulate_regular_post_futures as sim


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "simulation_runs" / "smoke_1x20_20260710" / "input_snapshot"


@contextmanager
def isolated_simulator_bot(tmp_path: Path):
    touched = set(bot.LOCAL_CONFIG_ALLOWED_KEYS) | {
        "LINES_FILE", "QUOTE_ANALYSIS_FILE", "IMAGE_ANALYSIS_FILE", "GENERATED_IMAGE_ANALYSIS_FILE",
        "IMAGE_GLOB", "GENERATED_IMAGE_DIR", "GENERATED_IMAGE_GLOB", "ORIGINAL_EDITORIAL_ANALYSIS_FILE",
        "GENERATED_IDENTITY_AUDIT_FILE", "STATE_FILE", "IMAGES_USED_FILE", "LINES_USED_FILE",
        "REGULAR_POST_RECEIPT_FILE", "MEME_POST_RECEIPT_FILE", "CONFIRMED_REPLY_RECEIPT_FILE", "LOCK_FILE",
        "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", "_GENERATED_IDENTITY_AUDIT_CACHE", "load_quote_analysis",
        "load_image_analysis", "current_image_sha256", "now_epoch", "upload_media", "create_post",
        "ask_grok_for_reply", "acquire_instance_lock", "post_random_quote", "post_next_meme",
        "maybe_reply_to_mentions", "maybe_reply_to_quote_tweets", "write_regular_post_receipt",
        "write_meme_post_receipt", "write_confirmed_reply_receipt", "remove_regular_post_receipt",
        "remove_meme_post_receipt", "remove_confirmed_reply_receipt", "atomic_write_json", "save_used_set",
        "save_quote_used_hashes", "save_image_used_basenames", "save_state",
    }
    original = {name: getattr(bot, name) for name in touched}
    request_names = ("request", "get", "post", "put", "patch", "delete")
    original_requests = {name: getattr(bot.requests, name) for name in request_names}
    rng_state = bot.random.getstate()
    try:
        sim.apply_snapshot_config(bot, SNAPSHOT)
        yield bot
    finally:
        for name, value in original.items():
            setattr(bot, name, value)
        for name, value in original_requests.items():
            setattr(bot.requests, name, value)
        bot.random.setstate(rng_state)


def run_private_future(
    private_bot,
    directory: Path,
    *,
    posts: int,
    detail: str = "none",
    resume: bool = False,
    failure_hook=None,
) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    writer = sim.PrivateWriter(directory)
    return sim.run_future(
        private_bot,
        writer,
        directory,
        SNAPSHOT,
        "equivalence-session",
        0,
        77123,
        posts,
        1_788_453_600,
        detail,
        resume,
        failure_hook=failure_hook,
    )


def configure_real_selector(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[dict, set[str], set[str]]:
    deterministic_flags = {
        "ENABLE_GENERATED_IMAGE_POOL": True,
        "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 6,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
        "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": True,
        "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32,
        "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0,
        "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING": True,
        "ENABLE_GENERATED_IDENTITY_POLICY_SCORING": False,
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 6.0,
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 15.0,
    }
    for key, value in deterministic_flags.items():
        monkeypatch.setattr(bot, key, value)
    monkeypatch.setattr(bot, "LINES_FILE", ROOT / "mrsMThatcher.txt")
    monkeypatch.setattr(bot, "QUOTE_ANALYSIS_FILE", ROOT / "quote_analysis.json")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", ROOT / "image_analysis.json")
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(ROOT / "generated_image_analysis.json"))
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(ROOT / "images" / "t*"))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(ROOT / "generated_review_approved_images"))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "ORIGINAL_EDITORIAL_ANALYSIS_FILE", str(ROOT / "original_image_editorial_analysis_experiment_v1.json"))
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", str(ROOT / "generated_image_identity_dependence_audit.json"))
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", {})
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", {})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_788_453_600)  # 2026-09-01 12:00 local-ish
    original_sha = bot.current_image_sha256
    sha_cache: dict[str, str] = {}

    def cached_sha(path: str) -> str:
        return sha_cache.setdefault(path, original_sha(path))

    monkeypatch.setattr(bot, "current_image_sha256", cached_sha)
    state = json.loads((ROOT / "bot_state.json").read_text(encoding="utf-8"))
    state["original_regular_posts_since_generated_image"] = bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN
    images_used = set(json.loads((ROOT / "images_used.json").read_text(encoding="utf-8")))
    lines_used = set(json.loads((ROOT / "lines_used.json").read_text(encoding="utf-8")))
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
        "ask_grok_for_reply",
        "acquire_instance_lock",
        "post_random_quote",
        "post_next_meme",
        "maybe_reply_to_mentions",
        "maybe_reply_to_quote_tweets",
        "write_regular_post_receipt",
        "write_meme_post_receipt",
        "write_confirmed_reply_receipt",
        "remove_regular_post_receipt",
        "remove_meme_post_receipt",
        "remove_confirmed_reply_receipt",
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
            "upload_media", "create_post", "ask_grok_for_reply", "acquire_instance_lock",
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
) -> None:
    state, images_used, lines_used = configure_real_selector(monkeypatch, tmp_path)
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
) -> None:
    state, images_used, lines_used = configure_real_selector(monkeypatch, tmp_path)
    bot.random.seed(991)
    selection = sim.select_with_production_recovery(bot, set(lines_used), set(images_used), copy.deepcopy(state))
    after_shadow = bot.random.getstate()

    bot.random.seed(991)
    with sim.capture_shadow_selection(bot):
        bot.choose_regular_quote_image_pair(set(lines_used), set(images_used), copy.deepcopy(state))
    assert bot.random.getstate() == after_shadow
    assert selection["image"]["basename"]


def test_generated_spacing_transitions_match_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    generated = "tg_" + "a" * 64 + ".png"
    state = {"original_regular_posts_since_generated_image": 2}
    bot.update_regular_generated_image_spacing_state(state, generated)
    assert state["original_regular_posts_since_generated_image"] == 0
    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t01.jpg")
    assert state["original_regular_posts_since_generated_image"] == 1
    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t02.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert bot.generated_images_allowed_by_spacing(state) is True


@pytest.mark.parametrize("basename", ["t01.jpg", "tg_" + "a" * 64 + ".png"])
def test_simulated_success_matches_production_receipt_selection_state(
    basename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
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
    bot.apply_regular_post_receipt(
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
        "original_regular_posts_since_generated_image", "next_quote_post_epoch",
        "next_quote_schedule_version",
    }
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


def test_interrupted_resume_matches_uninterrupted_after_record_append(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        uninterrupted = tmp_path / "uninterrupted"
        interrupted = tmp_path / "interrupted"
        run_private_future(private_bot, uninterrupted, posts=100)

        def fail(stage: str, post_index: int) -> None:
            if stage == "after_record_append" and post_index == 37:
                raise RuntimeError("intentional interruption")

        with pytest.raises(RuntimeError, match="intentional interruption"):
            run_private_future(private_bot, interrupted, posts=100, failure_hook=fail)
        run_private_future(private_bot, interrupted, posts=100, resume=True)

    assert (uninterrupted / "runs/run_0000/selections.jsonl").read_bytes() == (
        interrupted / "runs/run_0000/selections.jsonl"
    ).read_bytes()
    assert json.loads((uninterrupted / "runs/run_0000/checkpoint.json").read_text()) == json.loads(
        (interrupted / "runs/run_0000/checkpoint.json").read_text()
    )


def test_interrupted_resume_matches_uninterrupted_after_checkpoint(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        uninterrupted = tmp_path / "uninterrupted"
        interrupted = tmp_path / "interrupted"
        run_private_future(private_bot, uninterrupted, posts=40)

        def fail(stage: str, post_index: int) -> None:
            if stage == "after_checkpoint" and post_index == 17:
                raise RuntimeError("intentional interruption")

        with pytest.raises(RuntimeError, match="intentional interruption"):
            run_private_future(private_bot, interrupted, posts=40, failure_hook=fail)
        run_private_future(private_bot, interrupted, posts=40, resume=True)

    assert (uninterrupted / "runs/run_0000/selections.jsonl").read_bytes() == (
        interrupted / "runs/run_0000/selections.jsonl"
    ).read_bytes()
    indices = [json.loads(line)["post_index"] for line in (interrupted / "runs/run_0000/selections.jsonl").read_text().splitlines()]
    assert indices == list(range(1, 41))


def test_candidate_detail_modes_do_not_change_core_future(tmp_path: Path) -> None:
    outputs = {}
    with isolated_simulator_bot(tmp_path) as private_bot:
        for detail in ("none", "top10", "full"):
            directory = tmp_path / detail
            run_private_future(private_bot, directory, posts=20, detail=detail)
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
