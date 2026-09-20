"""Current meme inventory and historical exhaustion reporting regressions."""

from copy import deepcopy
from datetime import datetime
import json

import pytest

import mrs_log_digest as digest
from tests.helpers.digest_records import record


def queue_snapshot(project, *, history=None, config=None, state_status="available", config_status="available"):
    """Inspect one synthetic project with the production read-only reader."""
    return digest.meme_queue_health_snapshot(
        project,
        runtime_state={"posted_meme_filenames": history if history is not None else []},
        runtime_state_status=state_status,
        runtime_config=config if config is not None else {
            "ENABLE_DAILY_MEME_POSTS": True,
            "RESET_MEME_CYCLE_WHEN_ALL_POSTED": False,
        },
        runtime_config_status=config_status,
        observed_at=datetime(2026, 9, 20, 17),
        state_observed_at=datetime(2026, 9, 20, 16, 59),
        read_snapshot=digest.read_stable_regular_snapshot,
    )


def create_queue(project, names=("first.png", "second.JPG")):
    """Create candidate files plus entries the bot does not select."""
    directory = project / "final_posting_queue_top90_as_is" / "images"
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).write_bytes(b"image fixture")
    (directory / "notes.txt").write_text("not a meme")
    (directory / "directory.png").mkdir()
    return directory


@pytest.mark.parametrize("history,reset,status,posted,unposted,selectable", [
    ([], False, "available", 0, 2, 2),
    (["first.png", "old-removed.png"], False, "available", 1, 1, 1),
    (["first.png", "second.JPG", "first.png"], False, "exhausted", 2, 0, 0),
    (["first.png", "second.JPG"], True, "recycling_available", 2, 0, 2),
])
def test_current_queue_counts_real_candidates_and_cycle_history(
    tmp_path, history, reset, status, posted, unposted, selectable,
):
    directory = create_queue(tmp_path)
    original_history = deepcopy(history)
    before = {path.name: path.stat().st_mtime_ns for path in directory.iterdir()}
    result = queue_snapshot(tmp_path, history=history, config={
        "ENABLE_DAILY_MEME_POSTS": True,
        "RESET_MEME_CYCLE_WHEN_ALL_POSTED": reset,
    })
    assert result["status"] == status
    assert result["available"] is True
    assert result["candidate_count"] == 2
    assert result["posted_count"] == posted
    assert result["unposted_count"] == unposted
    assert result["available_to_select_count"] == selectable
    assert history == original_history
    assert {path.name: path.stat().st_mtime_ns for path in directory.iterdir()} == before


def test_current_queue_reads_source_defaults_and_overlays_boolean_strings(tmp_path):
    create_queue(tmp_path)
    (tmp_path / "mrsMThatcher2.py").write_text(
        "raise RuntimeError('source must never execute')\n"
        "ENABLE_DAILY_MEME_POSTS = True\n"
        "RESET_MEME_CYCLE_WHEN_ALL_POSTED = False\n"
    )
    exhausted = queue_snapshot(tmp_path, history=["first.png", "second.JPG"], config={}, config_status="absent")
    assert exhausted["status"] == "exhausted"
    assert set(exhausted["configuration_sources"].values()) == {"mrsMThatcher2.py default"}
    recycled = queue_snapshot(tmp_path, history=["first.png", "second.JPG"], config={
        "RESET_MEME_CYCLE_WHEN_ALL_POSTED": "on",
    })
    assert recycled["status"] == "recycling_available"
    assert recycled["configuration_sources"]["RESET_MEME_CYCLE_WHEN_ALL_POSTED"] == "mrsMThatcher.local.json override"


@pytest.mark.parametrize("condition,status", [
    ("missing_directory", "missing"),
    ("empty_directory", "empty"),
    ("absent_state", "unknown"),
    ("malformed_history", "unknown"),
    ("malformed_config", "unknown"),
    ("unknown_defaults", "unknown"),
    ("invalid_boolean", "unknown"),
    ("symlink_image", "unknown"),
])
def test_unavailable_inputs_never_claim_a_usable_queue(tmp_path, condition, status):
    kwargs = {}
    if condition != "missing_directory":
        directory = create_queue(tmp_path, names=() if condition == "empty_directory" else ("first.png",))
    if condition == "absent_state":
        kwargs["state_status"] = "absent"
    elif condition == "malformed_history":
        kwargs["history"] = {"first.png": True}
    elif condition == "malformed_config":
        kwargs["config_status"] = "malformed: invalid JSON"
    elif condition == "unknown_defaults":
        kwargs["config"] = {}
    elif condition == "invalid_boolean":
        kwargs["config"] = {"ENABLE_DAILY_MEME_POSTS": True, "RESET_MEME_CYCLE_WHEN_ALL_POSTED": 1}
    elif condition == "symlink_image":
        (directory / "linked.png").symlink_to(directory / "first.png")
    result = queue_snapshot(tmp_path, **kwargs)
    assert result["status"] == status
    assert result["available_to_select_count"] in {None, 0}
    assert result["reason"]


def test_historical_exhaustion_and_recycling_are_observations_not_operational_failures():
    report = digest.analyse([
        record(0, "INFO", "choose_next_meme", "All meme candidates have already been posted"),
        record(1, "INFO", "post_next_meme", "No meme available to post"),
        record(2, "INFO", "choose_next_meme", "RESET_MEME_CYCLE_WHEN_ALL_POSTED=True, clearing meme history"),
    ])
    assert report["summary"]["stats"]["meme_unavailable"] == 1
    assert report["summary"]["stats"]["meme_cycle_exhausted"] == 1
    assert report["summary"]["stats"]["meme_cycle_recycled"] == 1
    assert {item["kind"] for item in report["asset_health"]} == {"meme_unavailable", "meme_cycle_exhausted"}
    assert report["error_health"]["current_independent_incident_count"] == 0
    assert "1 meme selection without an available image in window" in report["summary"]["headline"]


@pytest.mark.parametrize("reset,expected", [(False, "exhausted"), (True, "recycling_available")])
def test_cli_reports_current_queue_even_when_selected_log_window_is_quiet(tmp_path, monkeypatch, reset, expected):
    project = tmp_path / "project"
    project.mkdir()
    directory = create_queue(project)
    source = project / "mrsMThatcher2.py"
    source.write_text("ENABLE_DAILY_MEME_POSTS = True\nRESET_MEME_CYCLE_WHEN_ALL_POSTED = False\n")
    state_path = project / "bot_state.json"
    state_path.write_text(json.dumps({"daily_reply_count": 0, "posted_meme_filenames": ["first.png", "second.JPG"]}))
    config_path = project / "mrsMThatcher.local.json"
    config_path.write_text(json.dumps({"RESET_MEME_CYCLE_WHEN_ALL_POSTED": reset}))
    log = project / "mrsMThatcher.log"
    log.write_text(
        "2026-09-20 12:00:00 INFO choose_next_meme:1 - All meme candidates have already been posted\n"
        "2026-09-20 12:00:01 INFO post_next_meme:1 - No meme available to post\n"
        "2026-09-20 17:00:00 INFO main:1 - Sleeping\n"
    )
    original_state = state_path.read_bytes()
    original_config = config_path.read_bytes()
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    assert digest.main([
        "--project-dir", str(project), "--since", "2026-09-20 16:00:00",
        "--until", "2026-09-20 18:00:00", "--no-state", "--json",
        "--output", str(output), "--markdown-output", str(markdown), str(log),
    ]) == 0
    report = json.loads(output.read_text())
    assert report["meme_queue_health"]["status"] == expected
    assert report["meme_queue_health"]["directory"] == str(directory)
    assert report["meme_queue_health"]["unposted_count"] == 0
    assert report["latest_config"]["RESET_MEME_CYCLE_WHEN_ALL_POSTED"] is reset
    assert report["asset_health"] == []
    assert report["summary"]["stats"].get("daily_meme_posted", 0) == 0
    expected_claim = "automatic recycling available" if reset else "automatic recycling disabled"
    assert expected_claim in report["summary"]["headline"]
    text = markdown.read_text()
    assert "## Current meme queue" in text
    assert f"Status: **{expected}**" in text
    assert "counts are independent of the selected log window" in text
    assert state_path.read_bytes() == original_state
    assert config_path.read_bytes() == original_config
    assert not (project / ".mrs_log_digest_state.json").exists()


@pytest.mark.parametrize("create_images", [False, True])
def test_disabled_posting_does_not_raise_an_actionable_current_queue_headline(tmp_path, create_images):
    if create_images:
        create_queue(tmp_path, names=())
    queue = queue_snapshot(tmp_path, config={"ENABLE_DAILY_MEME_POSTS": False, "RESET_MEME_CYCLE_WHEN_ALL_POSTED": False})
    report = digest.analyse([])
    report["runtime_state_status"] = {"status": "absent"}
    report["meme_queue_health"] = queue
    digest.refresh_current_health_headline(report)
    assert "daily meme posting disabled" in report["summary"]["headline"]
    assert "current meme queue empty" not in report["summary"]["headline"]
    assert "current meme directory missing" not in report["summary"]["headline"]


def test_current_queue_headline_refresh_replaces_exhaustion_without_duplicates():
    report = digest.analyse([])
    report["runtime_state_status"] = {"status": "absent"}
    report["meme_queue_health"] = {"status": "exhausted", "posting_enabled": True}
    digest.refresh_current_health_headline(report)
    first = report["summary"]["headline"]
    digest.refresh_current_health_headline(report)
    assert report["summary"]["headline"] == first
    report["meme_queue_health"]["status"] = "recycling_available"
    digest.refresh_current_health_headline(report)
    assert "automatic recycling disabled" not in report["summary"]["headline"]
    assert report["summary"]["headline"].count("automatic recycling available") == 1
