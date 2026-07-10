from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_simulate_regular_post_futures import SNAPSHOT, isolated_simulator_bot
from tests.test_unit_helpers import bot
from tools import simulate_regular_post_futures as sim


ROOT = Path(__file__).resolve().parents[1]


def run_counterfactual(
    private_bot,
    directory: Path,
    *,
    posts: int,
    detail: str = "none",
    resume: bool = False,
    failure_hook=None,
    stop_after_post: int | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    sim.run_counterfactual_future(
        private_bot,
        sim.PrivateWriter(directory),
        directory,
        SNAPSHOT,
        "counterfactual-equivalence",
        0,
        1000,
        posts,
        1_783_702_800,
        detail,
        resume,
        stop_after_post=stop_after_post,
        failure_hook=failure_hook,
    )


def records(directory: Path, name: str) -> list[dict]:
    path = directory / "counterfactual" / "runs" / "run_0000" / name
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_branch_seed_derivation_is_stable_and_separate() -> None:
    assert sim.derived_branch_seed(1000, "editorial") == sim.derived_branch_seed(1000, "editorial")
    assert sim.derived_branch_seed(1000, "editorial") != sim.derived_branch_seed(1000, "identity")


def test_policy_rows_apply_editorial_only_to_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    original = {"basename": "t01.jpg", "image_source": "original", "score": 10.0}
    generated = {"basename": "tg_" + "a" * 64 + ".png", "image_source": "generated", "score": 11.0}
    monkeypatch.setattr(bot, "load_original_editorial_analysis", lambda: {"t01.jpg": {"ok": True}})
    monkeypatch.setattr(bot, "original_editorial_shadow_score", lambda quote, image: (2.5, {"cap_hit": False}))
    rows = sim.policy_candidate_rows(bot, {"analysis": {}}, [original, generated], "editorial")
    assert rows[0]["policy_score"] == 12.5
    assert rows[0]["editorial_adjustment"] == 2.5
    assert rows[1]["policy_score"] == 11.0
    assert rows[1]["editorial_adjustment"] == 0.0


@pytest.mark.parametrize(
    ("policy", "shadow_score", "excluded"),
    [("unrestricted", 20.0, False), ("small_penalty", 14.0, False), ("strong_penalty", 5.0, False), ("origin_quote_only", None, True)],
)
def test_identity_policy_rows_use_exact_audit_result(
    policy: str,
    shadow_score: float | None,
    excluded: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {"basename": "tg_" + "a" * 64 + ".png", "image_source": "generated", "score": 20.0}
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: {})
    monkeypatch.setattr(
        bot,
        "generated_identity_candidate_shadow_row",
        lambda candidate, audit: {
            "identity_policy": policy,
            "identity_action": policy,
            "identity_adjustment": None if shadow_score is None else shadow_score - 20.0,
            "identity_shadow_score": shadow_score,
        },
    )
    row = sim.policy_candidate_rows(bot, {}, [candidate], "identity")[0]
    assert row["policy_score"] == shadow_score
    assert row["policy_excluded"] is excluded


def test_identity_origin_quote_score_remains_unrestricted_with_real_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    basename = "tg_" + "a" * 64 + ".png"
    candidate = {
        "basename": basename,
        "image_source": "generated",
        "score": 24.0,
        "origin_quote_match": True,
    }
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: {basename: {"recommended_cross_quote_policy": "origin_quote_only"}})
    row = sim.policy_candidate_rows(bot, {}, [candidate], "identity")[0]
    assert row["policy_score"] == 24.0
    assert row["policy_excluded"] is False


def test_policy_tie_uses_branch_rng_and_is_reproducible() -> None:
    rows = [
        {"basename": "a", "policy_score": 1.0, "policy_excluded": False},
        {"basename": "b", "policy_score": 1.0, "policy_excluded": False},
    ]
    bot.random.seed(1)
    first = sim.choose_policy_winner(bot, rows)["basename"]
    bot.random.seed(1)
    assert sim.choose_policy_winner(bot, rows)["basename"] == first
    bot.random.seed(5)
    assert sim.choose_policy_winner(bot, rows)["basename"] != first


def test_identity_policy_exhaustion_advances_to_last_image_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fallback = {
        "basename": "t01.jpg", "image_source": "original", "score": 1.0,
        "baseline_score": 1.0, "policy_score": 1.0, "policy_excluded": False,
    }

    def fake_phase(bot_arg, quote, images_used, state, branch, *, phase, cycle_boundary_exclusions=None):
        calls.append(phase)
        if phase == "normal":
            raise sim.CounterfactualPolicyExhausted("excluded")
        if phase == "forced_cycle_reset":
            cycle_boundary_exclusions.add("t-last.jpg")
            raise sim.CounterfactualPolicyExhausted("excluded after reset")
        return fallback, [fallback]

    monkeypatch.setattr(sim, "score_exact_quote_phase", fake_phase)
    result = sim.select_policy_image_with_recovery(bot, {}, set(), {}, "identity")
    assert calls == ["normal", "forced_cycle_reset", "last_image_fallback"]
    assert result["image"]["basename"] == "t01.jpg"
    assert result["selection_phase"] == "last_image_fallback"


def test_counterfactual_checkpoint_restores_deeply_isolated_branches() -> None:
    shared = {"nested": {"value": 1}}
    branches = {
        name: {"state": copy.deepcopy(shared), "images_used": {"t01.jpg"}, "lines_used": {"q"}, "rng_state": bot.random.getstate()}
        for name in sim.BRANCHES
    }
    checkpoint = sim.counterfactual_checkpoint_payload("run_0000", 1, 0, 10, branches)
    restored = sim.restore_counterfactual_branches(checkpoint)
    restored["editorial"]["state"]["nested"]["value"] = 2
    restored["editorial"]["images_used"].add("t02.jpg")
    assert restored["production"]["state"]["nested"]["value"] == 1
    assert "t02.jpg" not in restored["production"]["images_used"]
    assert restored["identity"]["state"] is not restored["production"]["state"]


def test_generated_spacing_evolves_independently_by_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    states = {name: {"original_regular_posts_since_generated_image": 2} for name in sim.BRANCHES}
    bot.update_regular_generated_image_spacing_state(states["production"], "tg_" + "a" * 64 + ".png")
    bot.update_regular_generated_image_spacing_state(states["editorial"], "t01.jpg")
    assert states["production"]["original_regular_posts_since_generated_image"] == 0
    assert states["editorial"]["original_regular_posts_since_generated_image"] == 2
    assert states["identity"]["original_regular_posts_since_generated_image"] == 2
    assert bot.generated_images_allowed_by_spacing(states["production"]) is False
    assert bot.generated_images_allowed_by_spacing(states["editorial"]) is True


def test_production_branch_and_shared_quotes_match_observational_control(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        run_counterfactual(private_bot, tmp_path / "counter", posts=20)
    production = records(tmp_path / "counter", "production/selections.jsonl")
    shared = records(tmp_path / "counter", "shared_quotes.jsonl")
    control = [json.loads(line) for line in (ROOT / "simulation_runs/audit_smoke_1x20_20260710/runs/run_0000/selections.jsonl").read_text().splitlines()]
    assert [row["winner"] for row in production] == [row["production_image"] for row in control]
    assert [row["quote_hash"] for row in shared] == [row["quote_hash"] for row in control]
    assert [row["virtual_timestamp"] for row in shared] == [row["virtual_timestamp"] for row in control]


def test_shared_quote_branches_diverge_and_then_use_different_candidate_sets(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        run_counterfactual(private_bot, tmp_path / "counter", posts=20, detail="full")
    comparisons = records(tmp_path / "counter", "branch_comparison.jsonl")
    shared = records(tmp_path / "counter", "shared_quotes.jsonl")
    assert len({row["quote_hash"] for row in shared}) == 20
    first = next(row for row in comparisons if not row["production_editorial_same"])
    production = records(tmp_path / "counter", "production/selections.jsonl")
    editorial = records(tmp_path / "counter", "editorial/selections.jsonl")
    assert first["post_index"] == 9
    assert production[8]["winner"] != editorial[8]["winner"]
    assert {row["basename"] for row in production[9]["candidate_detail"]} != {
        row["basename"] for row in editorial[9]["candidate_detail"]
    }
    checkpoint = json.loads((tmp_path / "counter/counterfactual/runs/run_0000/counterfactual_checkpoint.json").read_text())
    assert set(checkpoint["branches"]["production"]["images_used"]) != set(checkpoint["branches"]["editorial"]["images_used"])


def test_branch_marks_only_its_own_divergent_winner_used(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        run_counterfactual(private_bot, tmp_path / "counter", posts=20, stop_after_post=9)
    comparison = records(tmp_path / "counter", "branch_comparison.jsonl")[-1]
    assert comparison["production_editorial_same"] is False
    checkpoint = json.loads((tmp_path / "counter/counterfactual/runs/run_0000/counterfactual_checkpoint.json").read_text())
    production_winner = comparison["winners"]["production"]
    editorial_winner = comparison["winners"]["editorial"]
    assert production_winner in checkpoint["branches"]["production"]["images_used"]
    assert editorial_winner in checkpoint["branches"]["editorial"]["images_used"]
    assert editorial_winner not in checkpoint["branches"]["production"]["images_used"]
    assert production_winner not in checkpoint["branches"]["editorial"]["images_used"]


def test_counterfactual_candidate_detail_modes_do_not_change_future(tmp_path: Path) -> None:
    outputs = {}
    with isolated_simulator_bot(tmp_path) as private_bot:
        for detail in ("none", "top10", "full"):
            directory = tmp_path / detail
            run_counterfactual(private_bot, directory, posts=10, detail=detail)
            outputs[detail] = {
                branch: [
                    {key: value for key, value in row.items() if key != "candidate_detail"}
                    for row in records(directory, f"{branch}/selections.jsonl")
                ]
                for branch in sim.BRANCHES
            }
    assert outputs["none"] == outputs["top10"] == outputs["full"]


@pytest.mark.parametrize("stage", ["after_editorial_selection", "after_comparison_append", "after_counterfactual_checkpoint"])
def test_counterfactual_resume_matches_uninterrupted(stage: str, tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        uninterrupted = tmp_path / "uninterrupted"
        interrupted = tmp_path / "interrupted"
        run_counterfactual(private_bot, uninterrupted, posts=100)

        def fail(current_stage: str, post_index: int) -> None:
            if current_stage == stage and post_index == 37:
                raise RuntimeError("intentional counterfactual interruption")

        with pytest.raises(RuntimeError, match="intentional counterfactual interruption"):
            run_counterfactual(private_bot, interrupted, posts=100, failure_hook=fail)
        run_counterfactual(private_bot, interrupted, posts=100, resume=True)

    for name in ("shared_quotes.jsonl", "branch_comparison.jsonl", *(f"{branch}/selections.jsonl" for branch in sim.BRANCHES)):
        assert (uninterrupted / "counterfactual/runs/run_0000" / name).read_bytes() == (
            interrupted / "counterfactual/runs/run_0000" / name
        ).read_bytes()
    assert json.loads((uninterrupted / "counterfactual/runs/run_0000/counterfactual_checkpoint.json").read_text()) == json.loads(
        (interrupted / "counterfactual/runs/run_0000/counterfactual_checkpoint.json").read_text()
    )


def test_counterfactual_summary_uses_actual_branch_winners(tmp_path: Path) -> None:
    with isolated_simulator_bot(tmp_path) as private_bot:
        run_counterfactual(private_bot, tmp_path / "counter", posts=20)
    branch_records, comparisons = sim.load_counterfactual_records(tmp_path / "counter")
    summary = sim.summarize_counterfactual(branch_records, comparisons, 1.0)
    assert summary["total_post_indices"] == 20
    assert summary["total_branch_selections"] == 60
    assert summary["editorial"]["divergences_from_production"] == 3
    assert summary["branches"]["editorial"]["diversity"]["unique_images"] == 20
