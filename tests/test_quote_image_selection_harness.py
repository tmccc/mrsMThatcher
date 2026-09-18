from __future__ import annotations

import base64
import hashlib
import json
import socket
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

import quote_image_selection_harness as harness
from semantic_alignment.quote_image_semantic_veto import ShadowRuntime
from tests.helpers.historical_corpus import historical_corpus_root


ROOT = Path(__file__).resolve().parents[1]


class MinimalBot:
    BASE_DIR = ROOT

    def load_original_editorial_analysis(self):
        return {}

    def original_editorial_shadow_score(self, _quote, _editorial):
        return 0.0, {}


def load_veto() -> ShadowRuntime:
    path = (
        ROOT
        / "semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/"
        "material_veto_v3_shadow_manifest.json"
    )
    runtime = ShadowRuntime.load(
        ROOT,
        {
            "enabled": True,
            "mode": "shadow",
            "manifest_path": str(path),
            "fail_open": True,
            "record_best_allowed_alternative": True,
            "maximum_shadow_history": 100,
        },
        verify_source_hashes=False,
        enable_history=False,
    )
    assert runtime.available
    return runtime


def minimal_context(tmp_path: Path, quote_id: str, veto: ShadowRuntime) -> harness.HarnessContext:
    connection = harness.initialise_database(tmp_path / "simulation.sqlite3")
    return harness.HarnessContext(
        run_dir=tmp_path,
        snapshot=tmp_path,
        bot=MinimalBot(),
        veto=veto,
        packets={quote_id: {"intended_argument": "Meaning", "broader_principle": "Principle"}},
        quote_text={quote_id: "A quotation."},
        quote_line={quote_id: 0},
        connection=connection,
    )


def test_import_has_no_production_side_effects() -> None:
    paths = [ROOT / name for name in ("bot_state.json", "images_used.json", "lines_used.json")]
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        for path in paths
    }
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import quote_image_selection_harness; print('mrsMThatcher2' in sys.modules)"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        for path in paths
    }
    assert result.stdout.strip() == "False"
    assert after == before


def test_network_guard_blocks_connections_udp_and_dns() -> None:
    with harness.block_outbound_network():
        with pytest.raises(harness.IsolationError):
            socket.create_connection(("example.com", 443))
        with socket.socket() as stream:
            with pytest.raises(harness.IsolationError):
                stream.connect_ex(("127.0.0.1", 9))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
            with pytest.raises(harness.IsolationError):
                datagram.sendto(b"x", ("127.0.0.1", 9))
        with pytest.raises(harness.IsolationError):
            socket.getaddrinfo("example.com", 443)


def test_network_guard_blocks_child_process_and_shell_escape() -> None:
    with harness.block_outbound_network():
        with pytest.raises(harness.IsolationError):
            subprocess.run([sys.executable, "-c", "print('escape')"], check=True)
        with pytest.raises(harness.IsolationError):
            __import__("os").system("true")


def test_open_audit_blocks_production_write_and_allows_private_write(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path_identity = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:16]
    forbidden_path = ROOT / f".harness-write-test-{path_identity}"
    audit = harness.OpenAudit(run_dir)
    with audit:
        (run_dir / "ok.json").write_text("{}", encoding="utf-8")
        with pytest.raises(harness.IsolationError):
            forbidden_path.write_text("bad", encoding="utf-8")
        assert (ROOT / "quote_analysis.json").read_text(encoding="utf-8")
    summary = audit.summary()
    assert summary["forbidden_write_attempt_count"] == 1
    assert summary["production_read_open_count"] >= 1
    assert not forbidden_path.exists()


def test_stable_file_copy_records_hash_without_source_write(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"stable":true}', encoding="utf-8")
    destination = tmp_path / "run" / "copy.json"
    record = harness.stable_file_record(source, destination)
    assert record["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert destination.read_bytes() == source.read_bytes()
    assert record["stable_read_attempts"] == 1


def test_stable_json_object_records_source_identity(tmp_path: Path) -> None:
    source = tmp_path / "config.json"
    source.write_text('{"selection":1,"secret":"excluded later"}', encoding="utf-8")
    value, record = harness.stable_json_object(source)
    assert value["selection"] == 1
    assert record["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert record["stable_read_attempts"] == 1


def test_immutable_verification_does_not_compare_sanitised_config_to_full_source(tmp_path: Path) -> None:
    source = tmp_path / "full.local.json"
    source.write_text('{"selection":1,"secret":"not-copied"}', encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "source_snapshot_manifest.json").write_text(
        json.dumps({
            "files": {
                "local_config": {
                    "source_path": str(source),
                    "sha256": hashlib.sha256(b'{"selection":1}').hexdigest(),
                    "sanitised_selection_keys_only": True,
                    "mutable_source": False,
                }
            }
        }),
        encoding="utf-8",
    )
    assert harness.verify_immutable_sources(run_dir) == {"checked": 0, "changed": [], "passed": True}


def test_canonical_completed_corpus_is_exact_partition(
    tmp_path: Path, historical_corpus_root: Path,
) -> None:
    """The frozen selection experiment retains its original corpus partition."""
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "research_packets.json").symlink_to(
        historical_corpus_root / "semantic_alignment_research/quote_research_full_001/research_packets.json"
    )
    (snapshot / "final_research_status.json").symlink_to(
        historical_corpus_root / "semantic_alignment_research/quote_research_full_001/final_unresolved/final_research_status.json"
    )
    (snapshot / "mrsMThatcher.txt").symlink_to(historical_corpus_root / "mrsMThatcher.txt")
    lines, unresolved = harness.write_eligible_quotes(snapshot)
    quote_ids = {hashlib.sha256(line.encode()).hexdigest() for line in lines}
    assert len(lines) == len(quote_ids) == 611
    assert len(unresolved) == 5
    assert not (quote_ids & unresolved)


def test_sweep_iterates_active_source_not_historical_packet_archive() -> None:
    source = (ROOT / "quote_image_selection_harness.py").read_text(encoding="utf-8")
    assert "quote_ids = sorted(ctx.quote_text)" in source
    assert "quote_ids = sorted(ctx.packets)" not in source
    assert "completed_research_hash_cache = frozenset(bot.completed_research_quote_hashes())" in source


def test_europe_london_leap_day_and_dst_boundaries() -> None:
    assert any(day.month == 2 and day.day == 29 for day in harness.date_range(2028))
    before = datetime(2026, 3, 29, 0, 59, tzinfo=harness.TZ)
    after = datetime.fromtimestamp(before.timestamp() + 120, harness.TZ)
    assert before.utcoffset().total_seconds() == 0
    assert after.hour == 2
    assert after.utcoffset().total_seconds() == 3600
    observations = harness.dst_transition_observations(2026)
    assert len(observations) == 6
    assert {value.utcoffset().total_seconds() for value in observations} == {0, 3600}


def test_seasonal_calendar_report_uses_current_map_schema() -> None:
    assert harness.seasonal_calendar_summary({
        "years": [2026, 2028, 2029],
        "calendar_state_count": 4384,
        "unique_seasonal_state_count": 13,
        "boundaries": [{}, {}],
    }) == {
        "years": [2026, 2028, 2029],
        "observation_count": 4384,
        "unique_state_count": 13,
        "boundary_count": 2,
    }


def test_runtime_resource_report_uses_sqlite_peak_rss_column() -> None:
    summary = harness.runtime_resource_summary([
        {"status": "completed", "wall_seconds": 2.0, "cpu_seconds": 1.5, "peak_rss_kib": 1234},
        {"status": "interrupted", "wall_seconds": None, "cpu_seconds": None, "peak_rss_kib": None},
    ])
    assert summary["completed_command_count"] == 1
    assert summary["peak_rss_kib_max"] == 1234


def test_veto_lookup_is_hash_based_and_generated_is_out_of_scope() -> None:
    runtime = load_veto()
    pair_key, pair = next(iter(runtime.pairs.items()))
    quote_id, image_hash = pair_key.split(":")
    ctx = harness.HarnessContext(Path("."), Path("."), MinimalBot(), runtime, {}, {}, {}, None)  # type: ignore[arg-type]
    status, found = harness.candidate_veto_status(
        ctx, quote_id, {"basename": "renamed.jpg", "image_hash": image_hash, "image_source": "original"}
    )
    assert status == pair["decision"]
    assert found == pair
    generated, generated_pair = harness.candidate_veto_status(
        ctx, quote_id, {"basename": "tg_x.png", "image_hash": image_hash, "image_source": "generated"}
    )
    assert generated == "out_of_scope_generated"
    assert generated_pair is None


def test_unknown_pair_is_not_treated_as_allowed() -> None:
    runtime = load_veto()
    quote_id = next(iter(runtime.quote_flags))
    ctx = harness.HarnessContext(Path("."), Path("."), MinimalBot(), runtime, {}, {}, {}, None)  # type: ignore[arg-type]
    status, pair = harness.candidate_veto_status(
        ctx, quote_id, {"image_hash": "f" * 64, "image_source": "original"}
    )
    assert status == "unknown_unjudged"
    assert pair is None


def test_incomplete_coverage_policy_does_not_select_vetoed_or_unknown(tmp_path: Path) -> None:
    runtime = load_veto()
    quote_id = next(
        key
        for key, has_allowed in runtime.quote_flags.items()
        if has_allowed is None
        and any(
            value["quote_id"] == key and value["decision"] == "veto"
            for value in runtime.pairs.values()
        )
    )
    pair = next(value for value in runtime.pairs.values() if value["quote_id"] == quote_id and value["decision"] == "veto")
    ctx = minimal_context(tmp_path, quote_id, runtime)
    quote = {"quote_hash": quote_id, "text": "A quotation.", "analysis": {}}
    production = {
        "basename": "vetoed.jpg", "image_hash": pair["image_hash"], "image_source": "original", "score": 10.0,
    }
    unknown = {"basename": "unknown.jpg", "image_hash": "f" * 64, "image_source": "original", "score": 20.0}
    policies = harness.evaluate_policies(ctx, quote, production, [production, unknown])
    assert policies["semantic_winner"] is None
    assert policies["combined_winner"] is None
    assert policies["veto_event"]["quote_has_no_allowed_candidate_globally"] is False
    assert policies["veto_event"]["quote_has_incomplete_pair_coverage"] is True
    ctx.connection.close()


def test_sqlite_event_storage_and_candidate_compression(tmp_path: Path) -> None:
    runtime = load_veto()
    pair = next(value for value in runtime.pairs.values() if value["decision"] == "allow")
    quote_id = pair["quote_id"]
    ctx = minimal_context(tmp_path, quote_id, runtime)
    candidate = {
        "basename": "candidate.jpg", "image_hash": pair["image_hash"], "image_source": "original",
        "score": 12.0, "components": {"topic": 12.0}, "veto_status": "allow", "veto_reason_codes": [],
    }
    quote = {"quote_hash": quote_id, "text": "A quotation.", "weight": 1.0, "season_status": {}}
    selection = {"image": candidate, "scored": [candidate], "selection_phase": "normal"}
    policies = {
        "editorial_winner": None, "production_editorial": None,
        "veto_event": {"shadow_status": "allow", "veto_reason_codes": [], "alternative_image_basename": None},
        "semantic_winner": candidate, "combined_winner": candidate,
        "classified_candidates": [candidate], "semantic_score_loss": 0.0, "combined_score_loss": 0.0,
    }
    season = {"seasonal_state_signature": "s", "active_seasonal_rules": []}
    harness.insert_event(
        ctx, event_key="event", mode="test", when=datetime(2026, 1, 1, tzinfo=harness.TZ),
        year=2026, seed=1, event_index=1, profile="fresh", state_hash="h",
        season=season, quote=quote, selection=selection, policies=policies,
        generated_spacing_allowed=True, full_candidates=True,
    )
    ctx.connection.commit()
    assert ctx.connection.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0] == 1
    decoded = harness.decode_candidate_set(ctx.connection, "event")
    assert decoded[0]["basename"] == "candidate.jpg"
    summary = harness.summarise_mode(ctx.connection, "test")
    assert summary["total_simulations"] == 1
    assert summary["semantic_veto_status_counts"] == {"allow": 1}
    seasonal = harness.seasonal_breakdown(ctx.connection, "test")
    assert seasonal["states"][0]["selection_count"] == 1
    assert seasonal["non_seasonal_control_signature"] == "s"
    ctx.connection.close()


def test_failed_event_can_be_removed_for_deterministic_resume(tmp_path: Path) -> None:
    connection = harness.initialise_database(tmp_path / "simulation.sqlite3")
    connection.execute(
        "INSERT INTO simulation_events(event_key,mode,simulated_timestamp,state_hash,seasonal_signature,"
        "active_rules_json,reconstruction_complete,detail_json) VALUES (?,?,?,?,?,?,?,?)",
        ("failed", "pilot_sweep", "2026-01-01T00:01:00+00:00", "h", "s", "[]", 0, "{}"),
    )
    prior = connection.execute(
        "SELECT reconstruction_complete FROM simulation_events WHERE event_key='failed'"
    ).fetchone()
    assert prior["reconstruction_complete"] == 0
    connection.execute("DELETE FROM simulation_events WHERE event_key='failed'")
    assert connection.execute("SELECT 1 FROM simulation_events WHERE event_key='failed'").fetchone() is None
    connection.close()


def test_run_timer_persists_a_completed_run(tmp_path: Path) -> None:
    connection = harness.initialise_database(tmp_path / "simulation.sqlite3")
    timer = harness.run_timed(connection, "run-1", "test", {"case": 1})
    timer.finish()
    row = connection.execute("SELECT * FROM runs WHERE run_id='run-1'").fetchone()
    assert row["status"] == "completed"
    assert row["wall_seconds"] >= 0
    connection.close()


def test_semantic_alternative_uses_runtime_production_tie_breaking(tmp_path: Path) -> None:
    class FakeVeto:
        def canonical_quote_id(self, quote_id):
            return quote_id

        def pair(self, _quote_id, image_hash):
            return {
                "selected": {"decision": "veto", "veto_reason_codes": ["wrong_action"]},
                "allowed-a": {"decision": "allow"},
                "allowed-z": {"decision": "allow"},
            }.get(image_hash)

        def evaluate(self, **_kwargs):
            return {
                "shadow_status": "veto",
                "veto_reason_codes": ["wrong_action"],
                "alternative_image_basename": "z.jpg",
                "quote_has_no_allowed_candidate_globally": False,
            }

    connection = harness.initialise_database(tmp_path / "simulation.sqlite3")
    ctx = harness.HarnessContext(
        run_dir=tmp_path,
        snapshot=tmp_path,
        bot=MinimalBot(),
        veto=FakeVeto(),  # type: ignore[arg-type]
        packets={"quote": {"intended_argument": "Meaning", "broader_principle": "Principle"}},
        quote_text={"quote": "A quotation."},
        quote_line={"quote": 0},
        connection=connection,
    )
    candidates = [
        {"basename": "selected.jpg", "image_hash": "selected", "image_source": "original", "score": 20.0},
        {"basename": "a.jpg", "image_hash": "allowed-a", "image_source": "original", "score": 10.0},
        {"basename": "z.jpg", "image_hash": "allowed-z", "image_source": "original", "score": 10.0},
    ]
    policies = harness.evaluate_policies(
        ctx,
        {"quote_hash": "quote", "text": "A quotation.", "analysis": {}},
        candidates[0],
        candidates,
        tie_state=__import__("random").Random(1).getstate(),
    )
    assert policies["semantic_winner"]["basename"] == "z.jpg"
    connection.close()


def test_observer_evaluation_does_not_consume_rng(tmp_path: Path) -> None:
    runtime = load_veto()
    pair = next(value for value in runtime.pairs.values() if value["decision"] == "allow")
    ctx = minimal_context(tmp_path, pair["quote_id"], runtime)
    candidate = {
        "basename": "candidate.jpg",
        "image_hash": pair["image_hash"],
        "image_source": "original",
        "score": 12.0,
    }
    ctx.bot.random = __import__("random").Random(112)
    before = ctx.bot.random.getstate()
    harness.evaluate_policies(
        ctx,
        {"quote_hash": pair["quote_id"], "text": "A quotation.", "analysis": {}},
        candidate,
        [candidate],
        tie_state=before,
    )
    assert ctx.bot.random.getstate() == before
    ctx.connection.close()


@pytest.mark.parametrize("offline_images", [False, True])
def test_immutable_score_cache_reuses_production_result_without_aliasing_components(offline_images: bool) -> None:
    class CacheBot:
        def __init__(self):
            self.calls = Counter()

        def current_image_sha256(self, path):
            self.calls["sha"] += 1
            return f"hash:{path}"

        def build_image_topic_idf(self, analysis):
            self.calls["idf"] += 1
            return {"size": len(analysis)}

        def score_image_for_quote(self, quote, image, _idf):
            self.calls["score"] += 1
            return 7.5, {"topic": len(quote) + len(image)}, True

        def load_quote_analysis(self):
            self.calls["quote_analysis"] += 1
            return {"kind": "quotes"}

        def load_image_analysis(self):
            self.calls["image_analysis"] += 1
            return {"kind": "images"}

    cache_bot = CacheBot()
    live_loader = cache_bot.load_image_analysis
    image_policy = CacheBot() if offline_images else None
    image_owner = image_policy or cache_bot
    harness.install_immutable_score_caches(cache_bot, image_policy=image_policy)
    quote = {"q": 1}
    image = {"i": 1}
    image_corpus = {"x": 1}
    idf = cache_bot.build_image_topic_idf(image_corpus)
    assert cache_bot.build_image_topic_idf(image_corpus) is idf
    first = cache_bot.score_image_for_quote(quote, image, idf)
    first[1]["topic"] = -1
    second = cache_bot.score_image_for_quote(quote, image, idf)
    assert second == (7.5, {"topic": 2}, True)
    assert cache_bot.current_image_sha256("a") == cache_bot.current_image_sha256("a")
    assert cache_bot.load_quote_analysis() is cache_bot.load_quote_analysis()
    assert image_owner.load_image_analysis() is image_owner.load_image_analysis()
    if offline_images:
        assert cache_bot.load_image_analysis == live_loader
    assert cache_bot.calls["score"] == 1
    assert cache_bot.calls["sha"] == 1
    assert cache_bot.calls["idf"] == 1
    assert cache_bot.calls["quote_analysis"] == 1
    assert image_owner.calls["image_analysis"] == 1


def test_production_whitespace_hash_maps_only_to_proven_canonical_packet(tmp_path: Path) -> None:
    ctx = harness.HarnessContext(
        run_dir=tmp_path,
        snapshot=tmp_path,
        bot=MinimalBot(),
        veto=None,  # type: ignore[arg-type]
        packets={"canonical": {}},
        quote_text={"canonical": "Text  with spaces"},
        quote_line={"canonical": 0},
        connection=None,  # type: ignore[arg-type]
        analysis_aliases={"canonical": "normalised"},
    )
    mapped = harness.canonicalise_production_quote(
        ctx, {"quote_hash": "normalised", "text": "Text  with spaces", "analysis": {}},
    )
    assert mapped["quote_hash"] == "canonical"
    assert mapped["production_quote_hash"] == "normalised"
    with pytest.raises(harness.HarnessError):
        harness.canonicalise_production_quote(ctx, {"quote_hash": "unmapped"})


def test_production_history_id_set_uses_runtime_alias_not_canonical_id(tmp_path: Path) -> None:
    ctx = harness.HarnessContext(
        run_dir=tmp_path,
        snapshot=tmp_path,
        bot=MinimalBot(),
        veto=None,  # type: ignore[arg-type]
        packets={"canonical": {}, "exact": {}},
        quote_text={"canonical": "Aliased", "exact": "Exact"},
        quote_line={"canonical": 0, "exact": 1},
        connection=None,  # type: ignore[arg-type]
        analysis_aliases={"canonical": "runtime"},
    )
    assert harness.production_quote_ids(ctx) == {"runtime", "exact"}


def test_current_snapshot_profile_preserves_used_runtime_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = harness.HarnessContext(
        run_dir=tmp_path,
        snapshot=tmp_path,
        bot=MinimalBot(),
        veto=None,  # type: ignore[arg-type]
        packets={"canonical": {}, "exact": {}},
        quote_text={"canonical": "Aliased", "exact": "Exact"},
        quote_line={"canonical": 0, "exact": 1},
        connection=None,  # type: ignore[arg-type]
        analysis_aliases={"canonical": "runtime"},
    )
    monkeypatch.setattr(
        harness.production_sim.historical_image_selection(ctx.bot),
        "current_image_paths", lambda: [],
    )
    monkeypatch.setattr(
        harness.production_sim,
        "load_private_state",
        lambda _snapshot: ({}, set(), {"runtime", "unrelated"}),
    )
    _state, _images, lines = harness.filtered_snapshot_state(ctx)
    assert lines == {"runtime"}


def test_checkpoint_round_trip_is_deterministic() -> None:
    rng_state = random_state = __import__("random").Random(7).getstate()
    payload = harness.simulation_checkpoint(
        event_index=4, virtual_epoch=100, state={"x": 1}, images_used={"a"}, lines_used={"b"}, rng_state=rng_state,
    )
    restored = harness.restore_simulation_checkpoint(payload)
    assert restored[:5] == (4, 100, {"x": 1}, {"a"}, {"b"})
    assert restored[5] == random_state


def test_checkpoint_rejects_legacy_executable_pickle_without_running_it(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-ran"
    legacy_pickle = f"cos\nsystem\n(S'touch {marker}'\ntR.".encode("utf-8")
    payload = harness.simulation_checkpoint(
        event_index=4,
        virtual_epoch=100,
        state={"x": 1},
        images_used={"a"},
        lines_used={"b"},
        rng_state=__import__("random").Random(7).getstate(),
    )
    payload["rng_state"] = base64.b64encode(legacy_pickle).decode("ascii")
    with pytest.raises(harness.HarnessError, match="checkpoint rejected"):
        harness.restore_simulation_checkpoint(payload)
    assert not marker.exists()


def test_current_semantic_veto_replay_observations_are_not_dropped() -> None:
    observations = [{"quote_id": "q", "image_basename": "t01.jpg"}]
    assert harness.replay_records({"observations": observations}) == observations
    assert harness.replay_records({"records": observations}) == observations
    assert harness.replay_records({"events": observations}) == observations
    assert harness.replay_records({"observations": "malformed"}) == []


def test_replay_dependencies_are_stable_copied_and_hash_checked(tmp_path: Path) -> None:
    source = ROOT / "README.md"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = {
        "source_file_hashes": {
            "readme": {"path": "README.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        }
    }
    (snapshot / "material_veto_v3_shadow_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    ctx = type("Context", (), {"snapshot": snapshot, "run_dir": tmp_path})()
    replay_root = tmp_path / "replay"
    result = harness.snapshot_replay_manifest_sources(ctx, replay_root)
    assert (replay_root / "README.md").read_bytes() == source.read_bytes()
    assert result["records"]["readme"]["sha256"] == manifest["source_file_hashes"]["readme"]["sha256"]


def test_review_interface_is_local_responsive_and_has_no_hotlinks() -> None:
    page = harness.review_html()
    assert "@media(max-width:820px)" in page
    assert "http://" not in page and "https://" not in page
    assert "/image/" in page
    for control in ("season", "year", "month", "vetoAlt", "noSafe", "unknown", "generated", "loss"):
        assert f'id="{control}"' in page
    args = harness.build_parser().parse_args(["reproduce-event", "--event-key", "event"])
    assert args.command == "reproduce-event" and args.event_key == "event"


def test_run_directory_is_confined_to_research_tree(tmp_path: Path) -> None:
    with pytest.raises(harness.IsolationError):
        harness.validate_run_dir(tmp_path / "outside")
    accepted = harness.validate_run_dir(ROOT / "semantic_alignment_research" / "safe-harness-test")
    assert accepted.name == "safe-harness-test"
