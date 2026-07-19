from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pytest

import mrsMThatcher2 as bot
import mrs_log_digest as digest
from reply_strategy import retrieve_research_packets
import semantic_alignment.hybrid_reply_retrieval as hybrid
from semantic_alignment.hybrid_reply_retrieval import (
    DOCUMENT_TEMPLATE_VERSION,
    MODEL_ID,
    MODEL_REVISION,
    ShadowHistoryWriter,
    ShadowWorker,
    build_index,
    build_query,
    build_review_sample,
    browser_review_payload,
    build_retrieval_document,
    audit_review_context,
    exact_cosine_search,
    fuse_results,
    model_preflight,
    load_local_conversation_catalog,
    read_shadow_records,
    replay_context_snapshot,
    save_review,
    shadow_summary,
    substantive_query,
    submit_shadow_comparison,
    validate_corpus_invariants,
    validate_shadow_config,
)

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")


def test_corpus_invariants_and_unresolved_exclusion():
    packets, unresolved, metadata = validate_corpus_invariants(RESEARCH)
    assert len(packets) == 626
    assert len(unresolved) == 6
    assert set(packets).isdisjoint(unresolved)
    assert metadata["research_packets_sha256"] == "307b01f0c854ad8e16a50ed399bfa0cbd5f4b8c3d00709bfe02100a289143611"


def test_retrieval_document_is_deterministic_bounded_and_excludes_sources():
    packets, _, _ = validate_corpus_invariants(RESEARCH)
    quote_id = sorted(packets)[0]
    first = build_retrieval_document(quote_id, packets[quote_id])
    second = build_retrieval_document(quote_id, packets[quote_id])
    assert first == second
    assert first["document_template_version"] == DOCUMENT_TEMPLATE_VERSION
    assert "http" not in first["text"].lower()
    assert "sources:" not in first["text"].lower()
    assert len(first["text"]) < 6000


@pytest.mark.parametrize("value", ["Yep", "Exactly", "This", "👏", "@MrsMThatcher yes", "https://example.com", "Goedemorgen", "Amen. Fijne zondag.", "I love this photo"])
def test_substantive_gate_rejects_parent_only_reactions(value: str):
    assert substantive_query(value)[0] is False
    query = build_query(value, "A long parent quotation about free enterprise and liberty")
    assert query["substantive_query"] is False


def test_query_normalisation_preserves_multilingual_text_and_removes_links_handles():
    query = build_query("@MrsMThatcher Sí, la libertad económica importa https://example.com")
    assert query["substantive_query"] is True
    assert "libertad económica" in query["incoming_text"]
    assert "http" not in query["incoming_text"] and "@Mrs" not in query["incoming_text"]


def test_exact_cosine_search_has_stable_quote_id_tie_break():
    matrix = np.asarray([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    result = exact_cosine_search(matrix, np.asarray([1, 0], dtype=np.float32), ["b", "a", "c"], 3)
    assert result[:2] == [("a", 1.0), ("b", 1.0)]


def test_metadata_cannot_rescue_irrelevant_candidate():
    thresholds = {
        "rrf_k": 60, "lexical_weight": 1.0, "semantic_weight": 0.8, "metadata_weight": 10.0,
        "minimum_lexical_score": 1.0, "minimum_semantic_similarity": 0.75,
        "minimum_fused_score": 0.001, "minimum_top_margin": 0.0,
        "minimum_packet_confidence": "medium",
    }
    documents = {"q": {"quote_id": "q", "research_confidence": "high", "policy_topics": ["liberty"], "entities": []}}
    rows = fuse_results([], [("q", 0.2)], documents, "liberty", thresholds)
    assert rows[0]["metadata_signals"] and rows[0]["accepted"] is False


def test_semantic_only_candidate_can_pass_a_strong_absolute_floor():
    thresholds = {
        "rrf_k": 60, "lexical_weight": 1.0, "semantic_weight": 0.8, "metadata_weight": 0.15,
        "minimum_lexical_score": 3.0, "minimum_semantic_similarity": 0.88,
        "minimum_fused_score": 0.012, "minimum_top_margin": 0.0,
        "minimum_packet_confidence": "medium",
    }
    documents = {"q": {"quote_id": "q", "research_confidence": "high", "policy_topics": [], "entities": []}}
    rows = fuse_results([], [("q", 0.91)], documents, "libertad económica", thresholds)
    assert rows[0]["accepted"] is True


def test_insufficient_top_margin_rejects_ambiguous_set():
    thresholds = {
        "rrf_k": 60, "lexical_weight": 1.0, "semantic_weight": 0.8, "metadata_weight": 0.0,
        "minimum_lexical_score": 3.0, "minimum_semantic_similarity": 0.84,
        "minimum_fused_score": 0.01, "minimum_top_margin": 0.1,
        "minimum_packet_confidence": "medium",
    }
    documents = {qid: {"quote_id": qid, "research_confidence": "high", "policy_topics": [], "entities": []} for qid in ("a", "b")}
    rows = fuse_results([("a", 5), ("b", 5)], [("a", .9), ("b", .9)], documents, "tax policy", thresholds)
    assert not any(row["accepted"] for row in rows)


def test_shadow_configuration_is_source_disabled_and_shadow_only():
    config = bot.reply_strategy["hybrid_retrieval"]
    assert config["enabled"] is False and config["mode"] == "shadow" and config["fail_open"] is True
    assert validate_shadow_config(config) == []
    assert validate_shadow_config({**config, "mode": "active"})


def test_model_preflight_is_pinned_local_only_and_never_downloads(tmp_path: Path):
    preflight = model_preflight(tmp_path / "missing")
    assert preflight["model_id"] == MODEL_ID
    assert preflight["exact_revision"] == MODEL_REVISION
    assert preflight["trust_remote_code"] is False
    assert preflight["normal_runtime_local_files_only"] is True
    assert preflight["download_complete"] is False
    assert not (tmp_path / "missing").exists()


class FakeRetriever:
    manifest = {"index_schema_version": 1, "embeddings_sha256": "a" * 64, "model_revision": MODEL_REVISION}

    def retrieve(self, incoming_text, **kwargs):
        lexical = kwargs.get("production_lexical") or []
        return {
            "query": build_query(incoming_text),
            "lexical": [(item.quote_id, item.score) for item in lexical],
            "semantic": [], "hybrid": [],
            "production_lexical_quote_ids": [item.quote_id for item in lexical],
            "shadow_hybrid_quote_ids": [],
            "timings_ms": {"total": 1.0},
        }


def test_shadow_worker_is_async_bounded_and_persists_no_text(tmp_path: Path):
    worker = ShadowWorker(
        project_dir=tmp_path, retrieval_dir=tmp_path / "index", research_run=RESEARCH,
        timeout_ms=1000, maximum_history=100, retriever_factory=FakeRetriever,
    )
    assert worker.submit({
        "event_id": "e", "lane": "mention", "target_id": "123",
        "incoming_text": "A substantive question about tax", "parent_context": "", "thread_context": "",
        "production_lexical": [],
    })
    assert worker.drain()
    rows = read_shadow_records(tmp_path / "hybrid_reply_retrieval_runtime")
    assert rows[0]["status"] == "completed"
    assert "incoming_text" not in rows[0]
    assert shadow_summary(rows)["events"] == 1


def test_shadow_worker_timeout_and_model_failure_fail_open(tmp_path: Path):
    class SlowRetriever(FakeRetriever):
        def retrieve(self, incoming_text, **kwargs):
            time.sleep(0.02)
            return super().retrieve(incoming_text, **kwargs)

    slow = ShadowWorker(
        project_dir=tmp_path / "slow", retrieval_dir=tmp_path, research_run=RESEARCH,
        timeout_ms=1, maximum_history=100, retriever_factory=SlowRetriever,
    )
    slow.submit({"event_id": "slow", "lane": "mention", "target_id": "1", "incoming_text": "tax policy", "production_lexical": []})
    assert slow.drain()
    assert read_shadow_records(tmp_path / "slow" / "hybrid_reply_retrieval_runtime")[0]["status"] == "timeout"

    def broken():
        raise RuntimeError("shadow index corpus hash mismatch")

    failed = ShadowWorker(
        project_dir=tmp_path / "failed", retrieval_dir=tmp_path, research_run=RESEARCH,
        timeout_ms=1000, maximum_history=100, retriever_factory=broken,
    )
    failed.submit({"event_id": "failed", "lane": "mention", "target_id": "2", "incoming_text": "tax policy", "production_lexical": []})
    assert failed.drain()
    assert read_shadow_records(tmp_path / "failed" / "hybrid_reply_retrieval_runtime")[0]["status"] == "index_mismatch"


def test_shadow_history_deduplicates_repeated_event_ids(tmp_path: Path):
    worker = ShadowWorker(
        project_dir=tmp_path, retrieval_dir=tmp_path, research_run=RESEARCH,
        timeout_ms=1000, maximum_history=100, retriever_factory=FakeRetriever,
    )
    job = {"event_id": "same", "lane": "mention", "target_id": "1", "incoming_text": "tax policy", "production_lexical": []}
    worker.submit(job)
    worker.submit(job)
    assert worker.drain()
    assert len(read_shadow_records(tmp_path / "hybrid_reply_retrieval_runtime")) == 1


def test_shadow_history_rotates_at_bound_without_losing_audit_records(tmp_path: Path):
    writer = ShadowHistoryWriter(tmp_path, maximum_records=100)
    for index in range(101):
        writer.append({"event_id": str(index), "status": "completed", "latency_ms": 1, "production_lexical_quote_ids": [], "shadow_hybrid_quote_ids": []})
    assert len(read_shadow_records(tmp_path)) == 1
    archives = list(tmp_path.glob("shadow_history.*.jsonl"))
    assert len(archives) == 1 and len(archives[0].read_text().splitlines()) == 100


def test_reaction_only_shadow_does_not_load_model_and_records_no_substantive_query(tmp_path: Path):
    def forbidden_factory():
        raise AssertionError("reaction-only query must not load model")

    worker = ShadowWorker(
        project_dir=tmp_path, retrieval_dir=tmp_path, research_run=RESEARCH,
        timeout_ms=1000, maximum_history=100, retriever_factory=forbidden_factory,
    )
    worker.submit({"event_id": "reaction", "lane": "mention", "target_id": "1", "incoming_text": "Yep", "parent_context": "Liberty and tax", "production_lexical": []})
    assert worker.drain()
    row = read_shadow_records(tmp_path / "hybrid_reply_retrieval_runtime")[0]
    assert row["status"] == "completed" and row["reason"] == "no_substantive_query"
    assert row["hybrid_no_evidence"] is True


def test_shadow_submit_disabled_has_no_side_effect(tmp_path: Path):
    config = {**bot.reply_strategy["hybrid_retrieval"], "enabled": False}
    result = submit_shadow_comparison(
        config=config, project_dir=tmp_path, research_run=RESEARCH,
        incoming_text="Tax policy", parent_context="", thread_context="", lane="mention", target_id="1",
        production_lexical=[],
    )
    assert result is None
    assert not (tmp_path / "hybrid_reply_retrieval_runtime").exists()


def test_shadow_event_identity_changes_when_bounded_context_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    submitted = []

    class CapturingWorker:
        def __init__(self, **kwargs):
            pass

        def submit(self, job):
            submitted.append(job)
            return True

    monkeypatch.setattr(hybrid, "ShadowWorker", CapturingWorker)
    hybrid._WORKERS.clear()
    config = {**bot.reply_strategy["hybrid_retrieval"], "enabled": True}
    common = {
        "config": config,
        "project_dir": tmp_path,
        "research_run": RESEARCH,
        "incoming_text": "What did Thatcher mean by economic freedom?",
        "thread_context": "",
        "lane": "mention",
        "target_id": "1",
        "production_lexical": [],
    }
    submit_shadow_comparison(parent_context="Parent version one", **common)
    submit_shadow_comparison(parent_context="Parent version two", **common)
    hybrid._WORKERS.clear()

    assert len(submitted) == 2
    assert submitted[0]["event_id"] != submitted[1]["event_id"]


def test_existing_lexical_retrieval_is_unchanged_and_deterministic():
    first = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    second = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    assert [(item.quote_id, item.score) for item in first] == [(item.quote_id, item.score) for item in second]


def test_final_offline_evaluation_includes_multilingual_and_hard_negatives():
    path = Path("semantic_alignment_research/hybrid_reply_retrieval_001/automated_evaluation.json")
    if not path.exists():
        pytest.skip("offline evaluation artifact has not been generated")
    data = json.loads(path.read_text())
    assert data["hard_negative_no_evidence_accuracy"] >= 0.9
    assert data["multilingual"]["queries"] == 5
    assert data["multilingual"]["overall_topic_match_rate"] > 0


def test_shadow_mode_cannot_change_prompt_reply_or_add_network_call(monkeypatch: pytest.MonkeyPatch):
    import reply_strategy as strategy

    submitted = []
    monkeypatch.setattr(hybrid, "submit_shadow_comparison", lambda **kwargs: submitted.append(kwargs))
    evidence = strategy.RetrievedEvidence("a" * 64, 4.0, "exact", "Evidence", {
        "verified_text": "Exact evidence.", "research_confidence": "high", "verification_status": "exact",
    })
    monkeypatch.setattr(strategy, "retrieve_research_packets", lambda *args, **kwargs: [evidence])
    calls = []
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({"choices": [{"message": {"content": json.dumps({
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "A concise reply.", "no_reply_reason": "",
        "topical_basis": "",
    })}}]}).encode()
    monkeypatch.setattr(bot.requests, "post", lambda *args, **kwargs: calls.append(kwargs["json"]) or response)
    monkeypatch.setattr(bot, "reply_strategy", {
        **bot.reply_strategy, "enabled": True,
        "hybrid_retrieval": {**bot.reply_strategy["hybrid_retrieval"], "enabled": True},
    })
    result = bot.ask_grok_for_reply(
        "assembled production context", {"lane": "mention", "target_id": "123"},
        shadow_incoming_text="incoming contribution", shadow_parent_context="assembled production context",
    )
    assert result == "A concise reply."
    assert len(calls) == 1 and len(submitted) == 1
    assert submitted[0]["production_lexical"] == [evidence]
    prompt_text = json.dumps(calls[0]["messages"][1]["content"])
    assert "Exact evidence." in prompt_text
    assert "shadow_hybrid" not in prompt_text and "hybrid_results" not in prompt_text
    assert result.strategy_metadata["retrieved_quote_ids"] == []


def test_hybrid_top20_lexical_uses_assembled_production_context(monkeypatch: pytest.MonkeyPatch):
    seen = []
    monkeypatch.setattr(hybrid, "retrieve_research_packets", lambda text, path, maximum: seen.append((text, maximum)) or [])
    retriever = object.__new__(hybrid.HybridRetriever)
    retriever.research_run = RESEARCH
    retriever.thresholds = {**hybrid.DEFAULT_THRESHOLDS, "maximum_results": 5}
    retriever.documents = {"q": {"quote_id": "q", "quote_text": "q", "research_confidence": "high", "policy_topics": [], "entities": []}}
    retriever.quote_ids = ["q"]
    retriever.eligible_quote_ids = {"q"}
    retriever.matrix = np.asarray([[1.0] + [0.0] * 383], dtype=np.float32)
    retriever._query_embedding = lambda query: np.asarray([1.0] + [0.0] * 383, dtype=np.float32)
    retriever.retrieve("incoming", parent_context="assembled context", production_lexical=[])
    assert seen == [("assembled context", 20)]


def test_hybrid_deduplicates_near_identical_quote_family_members(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid, "retrieve_research_packets", lambda *args, **kwargs: [])
    retriever = object.__new__(hybrid.HybridRetriever)
    retriever.research_run = RESEARCH
    retriever.thresholds = {**hybrid.DEFAULT_THRESHOLDS, "maximum_results": 5, "minimum_semantic_similarity": 0.8}
    retriever.quote_ids = ["a", "b", "c"]
    retriever.eligible_quote_ids = {"a", "b", "c"}
    retriever.documents = {
        "a": {"quote_id": "a", "quote_text": "Freedom requires responsibility under the law", "research_confidence": "high", "policy_topics": [], "entities": []},
        "b": {"quote_id": "b", "quote_text": "Freedom requires responsibility under the law.", "research_confidence": "high", "policy_topics": [], "entities": []},
        "c": {"quote_id": "c", "quote_text": "Enterprise creates prosperity for families", "research_confidence": "high", "policy_topics": [], "entities": []},
    }
    retriever.matrix = np.asarray([[1.0] + [0.0] * 383] * 3, dtype=np.float32)
    retriever._query_embedding = lambda query: np.asarray([1.0] + [0.0] * 383, dtype=np.float32)
    result = retriever.retrieve("freedom and enterprise")
    assert result["shadow_hybrid_quote_ids"] == ["a", "c"]
    assert [row["rank"] for row in result["hybrid"]] == [1, 2]


def test_hybrid_semantic_candidates_exclude_attribution_ineligible_packets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hybrid, "retrieve_research_packets", lambda *args, **kwargs: [])
    retriever = object.__new__(hybrid.HybridRetriever)
    retriever.research_run = RESEARCH
    retriever.thresholds = {
        **hybrid.DEFAULT_THRESHOLDS,
        "maximum_results": 5,
        "minimum_semantic_similarity": 0.8,
    }
    retriever.quote_ids = ["eligible", "excluded"]
    retriever.eligible_quote_ids = {"eligible"}
    retriever.documents = {
        quote_id: {
            "quote_id": quote_id,
            "quote_text": quote_id,
            "research_confidence": "high",
            "policy_topics": [],
            "entities": [],
        }
        for quote_id in retriever.quote_ids
    }
    retriever.matrix = np.asarray([[1.0] + [0.0] * 383] * 2, dtype=np.float32)
    retriever._query_embedding = lambda query: np.asarray([1.0] + [0.0] * 383, dtype=np.float32)

    result = retriever.retrieve("eligible excluded")

    assert result["shadow_hybrid_quote_ids"] == ["eligible"]


def test_hybrid_refills_semantic_candidates_after_attribution_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hybrid, "retrieve_research_packets", lambda *args, **kwargs: [])
    requested_limits: list[int] = []

    def fake_search(_matrix, _query, _quote_ids, maximum):
        requested_limits.append(maximum)
        return [("excluded", 1.0), ("eligible", 0.9)][:maximum]

    monkeypatch.setattr(hybrid, "exact_cosine_search", fake_search)
    retriever = object.__new__(hybrid.HybridRetriever)
    retriever.research_run = RESEARCH
    retriever.thresholds = {
        **hybrid.DEFAULT_THRESHOLDS,
        "maximum_results": 1,
        "semantic_candidate_count": 1,
        "minimum_semantic_similarity": 0.8,
    }
    retriever.quote_ids = ["excluded", "eligible"]
    retriever.eligible_quote_ids = {"eligible"}
    retriever.documents = {
        quote_id: {
            "quote_id": quote_id, "quote_text": quote_id, "research_confidence": "high",
            "policy_topics": [], "entities": [],
        }
        for quote_id in retriever.quote_ids
    }
    retriever.matrix = np.asarray([[1.0], [0.9]], dtype=np.float32)
    retriever._query_embedding = lambda query: np.asarray([1.0], dtype=np.float32)

    result = retriever.retrieve("eligible")

    assert requested_limits == [2]
    assert [row[0] for row in result["semantic"]] == ["eligible"]


def test_review_save_is_idempotent_and_audited(tmp_path: Path):
    (tmp_path / "manual_review").mkdir()
    (tmp_path / "review_sample_100.json").write_text(json.dumps({"items": [{"case_id": "c1"}]}))
    (tmp_path / "manual_review" / "human_reviews.json").write_text(json.dumps({"schema_version": 1, "items": {}}))
    payload = {"case_id": "c1", "choice": "A_better", "intervention": "humour_preferable", "packet_ratings": {}, "note": "", "reason_tags": []}
    first = save_review(tmp_path, payload)
    second = save_review(tmp_path, payload)
    assert first == second and first["revision"] == 1
    assert len((tmp_path / "manual_review" / "human_review_audit.jsonl").read_text().splitlines()) == 1


def _review_context_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    retrieval = tmp_path / "retrieval"
    project.mkdir()
    (retrieval / "replay_30d").mkdir(parents=True)
    (retrieval / "manual_review").mkdir()
    parent = {
        "id": "parent-1", "author_id": "owner", "text": "Parent &amp; context\nsecond line",
        "created_at": "2026-07-01T10:00:00Z", "referenced_tweets": [{"type": "replied_to", "id": "root-1"}],
    }
    root = {"id": "root-1", "author_id": "owner", "text": "Earlier context", "created_at": "2026-07-01T09:00:00Z"}
    state = {
        "tweet_cache": {
            "old": {
                "id": "old", "author_id": "user-1", "text": "@MrsMThatcher Social programs...",
                "created_at": "2026-07-01T11:00:00Z", "post_type": "hot_post_reply",
                "referenced_tweets": [{"type": "replied_to", "id": "parent-1"}],
            },
            "replacement": {
                "id": "replacement", "author_id": "user-2",
                "text": "@MrsMThatcher Полный многострочный текст\nвторая строка &amp; detail",
                "created_at": "2026-07-02T11:00:00Z",
                "referenced_tweets": [{"type": "replied_to", "id": "parent-1"}],
            },
            "quote": {
                "id": "quote", "author_id": "user-3", "text": "Comentario español",
                "created_at": "2026-07-03T11:00:00Z", "post_type": "quote_tweet",
                "referenced_tweets": [{"type": "quoted", "id": "parent-1"}],
            },
            "parent-1": parent,
            "root-1": root,
        },
        "skipped_hot_reply_ids": ["old"],
        "skipped_quote_post_ids": ["quote"],
    }
    (project / "bot_state.json").write_text(json.dumps(state), encoding="utf-8")
    rows = []
    for target, incoming, parent_text, lane, disagreement in (
        ("old", "@MrsMThatcher Social programs...", parent["text"], "mention", "lexical_only_evidence"),
        ("replacement", state["tweet_cache"]["replacement"]["text"], parent["text"], "mention", "lexical_only_evidence"),
        ("quote", "Comentario español", parent["text"], "quote_tweet", "different_evidence_set"),
    ):
        query = build_query(incoming, parent_text)
        rows.append({
            "target_id": target, "lane": lane, "incoming_text": incoming, "parent_context": parent_text,
            "query_text_hash": query["query_text_hash"], "substantive_query": query["substantive_query"],
            "query_language": "test", "disagreement_class": disagreement, "historical_decision": None,
            "lexical_results": [{"quote_id": "q1", "rank": 1, "score": 4.0}],
            "production_lexical_quote_ids": ["q1"], "shadow_hybrid_quote_ids": ["q2"],
        })
    (retrieval / "replay_30d" / "replay_results.json").write_text(json.dumps({"items": rows}), encoding="utf-8")
    documents = [
        {"quote_id": "q1", "quote_text": "Quote one", "verification_status": "exact", "research_confidence": "high", "source_event": "Speech", "intended_argument": "Argument one", "broader_principle": "Principle one"},
        {"quote_id": "q2", "quote_text": "Quote two", "verification_status": "normalised", "research_confidence": "medium", "source_event": "Interview", "intended_argument": "Argument two", "broader_principle": "Principle two"},
    ]
    (retrieval / "retrieval_documents.jsonl").write_text("".join(json.dumps(row) + "\n" for row in documents), encoding="utf-8")
    old_case = {
        "case_id": "shadow-review-001-old", "target_id": "old", "lane": "mention",
        "incoming_post": "@MrsMThatcher Social programs...", "thread_context": parent["text"],
        "substantive_query": True, "disagreement_class": "lexical_only_evidence",
        "review_strata": ["mention", "english_or_unknown", "substantive", "lexical_only_evidence", "humour_or_unreplied"],
        "A": [documents[0]], "B": [documents[1]],
    }
    (retrieval / "review_sample_100.json").write_text(json.dumps({"schema_version": 1, "case_count": 1, "items": [old_case]}), encoding="utf-8")
    (retrieval / "blind_assignment_manifest.json").write_text(json.dumps({"schema_version": 1, "assignments": {old_case["case_id"]: {"A": "lexical", "B": "hybrid"}}}), encoding="utf-8")
    (retrieval / "manual_review" / "human_reviews.json").write_text(json.dumps({"schema_version": 1, "items": {}}), encoding="utf-8")
    return project, retrieval


def test_review_context_audit_recovers_full_structured_context_and_replaces_truncated_case(tmp_path: Path):
    project, retrieval = _review_context_fixture(tmp_path)
    first = audit_review_context(project, retrieval, strict=True, apply=True)
    migrated = json.loads((retrieval / "review_sample_100.json").read_text())
    case = migrated["items"][0]

    assert first["original_truncated_cases"] == 1
    assert first["replaced_cases"] == 1
    assert case["target_id"] == "replacement"
    assert case["incoming"]["text"] == "@MrsMThatcher Полный многострочный текст\nвторая строка & detail"
    assert case["direct_parent"]["text"] == "Parent & context\nsecond line"
    assert case["older_thread_context"][0]["text"] == "Earlier context"
    assert case["query_basis"]["incoming_text_used"] is True
    assert case["query_basis"]["direct_parent_used"] is True
    assert case["query_basis"]["retrieval_text_matches_displayed_source"] is True
    assert (retrieval / "review_context_audit.json").is_file()
    assert (retrieval / "review_sample_migration.json").is_file()
    follow_up = json.loads((retrieval / "manual_review" / "context_follow_up_queue.json").read_text())
    assert follow_up["items"][0]["target_id"] == "old"
    assert list((retrieval / "manual_review" / "backups").glob("*/review_sample_100.json"))
    (retrieval / "manual_review" / "context_follow_up_queue.json").unlink()
    second = audit_review_context(project, retrieval, strict=True, apply=True)
    assert second["new_sample_hash"] == first["new_sample_hash"]
    assert second["replaced_cases"] == first["replaced_cases"] == 1
    assert second["old_sample_hash"] == first["old_sample_hash"]
    assert (retrieval / "manual_review" / "context_follow_up_queue.json").is_file()


def test_quote_tweet_context_is_separate_and_browser_payload_is_blind(tmp_path: Path):
    project, retrieval = _review_context_fixture(tmp_path)
    sample = json.loads((retrieval / "review_sample_100.json").read_text())
    replay = json.loads((retrieval / "replay_30d" / "replay_results.json").read_text())
    quote_row = replay["items"][2]
    quote_doc = sample["items"][0]
    quote_doc.update({"case_id": "shadow-review-001-quote", "target_id": "quote", "incoming_post": quote_row["incoming_text"], "thread_context": quote_row["parent_context"]})
    quote_doc["A"], quote_doc["B"] = quote_doc["B"], quote_doc["A"]
    sample["items"] = [quote_doc]
    (retrieval / "review_sample_100.json").write_text(json.dumps(sample), encoding="utf-8")
    (retrieval / "blind_assignment_manifest.json").write_text(json.dumps({"assignments": {quote_doc["case_id"]: {"A": "hybrid", "B": "lexical"}}}), encoding="utf-8")

    audit = audit_review_context(project, retrieval, strict=True, apply=True)
    public = browser_review_payload(retrieval)
    case = public["items"][0]
    encoded = json.dumps(public, ensure_ascii=False).casefold()
    blind = json.loads((retrieval / "blind_assignment_manifest.json").read_text())

    assert case["incoming"]["text"] == "Comentario español"
    assert case["direct_parent"] is None
    assert case["quoted_post"]["text"] == "Parent & context\nsecond line"
    assert case["query_basis"]["quoted_post_used"] is True
    assert blind["assignments"]["shadow-review-001-quote"] == {"A": "hybrid", "B": "lexical"}
    assert audit["retrieval_sets_changed_for_retained_cases"] is False
    assert "lexical" not in encoded and "hybrid" not in encoded


def test_material_context_change_invalidates_saved_review_but_keeps_audit(tmp_path: Path):
    project, retrieval = _review_context_fixture(tmp_path)
    review = {
        "case_id": "shadow-review-001-old", "choice": "A_better", "intervention": "historical_context",
        "packet_ratings": {}, "reason_tags": [], "note": "Reviewed truncated version", "revision": 1,
        "reviewed_at": "2026-07-10T00:00:00Z",
    }
    (retrieval / "manual_review" / "human_reviews.json").write_text(json.dumps({"schema_version": 1, "items": {review["case_id"]: review}}), encoding="utf-8")

    result = audit_review_context(project, retrieval, strict=True, apply=True)
    reviews = json.loads((retrieval / "manual_review" / "human_reviews.json").read_text())["items"]
    stale = json.loads((retrieval / "manual_review" / "stale_reviews.json").read_text())["items"]

    assert result["existing_reviews_invalidated"] == 1
    assert reviews == {}
    assert stale[0]["review"] == review
    assert "replacement" in stale[0]["reason"]


def test_insufficient_context_is_saved_and_excluded_from_preference_wins(tmp_path: Path):
    (tmp_path / "manual_review").mkdir()
    case = {"case_id": "c1", "context_status": "unavailable", "A": [], "B": []}
    (tmp_path / "review_sample_100.json").write_text(json.dumps({"case_count": 1, "items": [case]}))
    (tmp_path / "blind_assignment_manifest.json").write_text(json.dumps({"assignments": {"c1": {"A": "lexical", "B": "hybrid"}}}))
    (tmp_path / "manual_review" / "human_reviews.json").write_text(json.dumps({"schema_version": 1, "items": {}}))

    saved = save_review(tmp_path, {"case_id": "c1", "choice": "insufficient_context", "intervention": "", "packet_ratings": {}, "note": "", "reason_tags": []})
    results = hybrid.review_results(tmp_path)

    assert saved["choice"] == "insufficient_context"
    assert results["insufficient_context_count"] == 1
    assert results["ordinary_preference_review_count"] == 0
    assert results["decisive_retriever_wins"] == {}
    assert results["intervention_counts"] == {}


def test_review_html_has_full_safe_responsive_context_and_no_clamping():
    page = hybrid.REVIEW_HTML
    lowered = page.casefold()
    assert "incoming user contribution" in lowered
    assert "direct parent context" in lowered
    assert "earlier thread context" in lowered
    assert "insufficient context to judge" in lowered
    assert "textcontent" in lowered
    assert "white-space:pre-wrap" in lowered
    assert "line-clamp" not in lowered
    assert "text-overflow:ellipsis" not in lowered
    assert "max-height" not in lowered
    assert "@media(max-width:900px)" in lowered
    assert "hybrid_only_evidence" not in lowered
    assert "lexical_only_evidence" not in lowered


def test_replay_context_snapshot_never_slices_long_multiline_text():
    incoming = "Arabic العربية\nCyrillic Кириллица\nSpanish español\n" + "x" * 1400
    parent = "parent\n" + "y" * 900
    snapshot = replay_context_snapshot({"incoming_text": incoming, "parent_context": parent, "thread_context": "older"})
    assert snapshot == {"incoming_text": incoming, "parent_context": parent, "thread_context": "older"}


def test_structured_x_log_recovery_preserves_text_handles_and_references(tmp_path: Path):
    payload = {
        "data": [{
            "id": "10", "author_id": "20", "text": "Line one &amp; detail\nالسطر الثاني",
            "created_at": "2026-07-01T12:00:00Z", "referenced_tweets": [{"type": "replied_to", "id": "9"}],
        }],
        "includes": {"users": [{"id": "20", "username": "example_user"}]},
    }
    (tmp_path / "mrsMThatcher.log").write_text("2026 INFO X response json: " + json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    catalog = load_local_conversation_catalog(tmp_path)
    record = catalog["records"]["10"]
    assert record["text"] == "Line one &amp; detail\nالسطر الثاني"
    assert record["author_handle"] == "example_user"
    assert record["referenced_tweets"] == [{"type": "replied_to", "id": "9"}]


def test_build_review_sample_cannot_overwrite_context_migrated_sample(tmp_path: Path):
    (tmp_path / "replay_30d").mkdir()
    migrated = {"schema_version": 2, "context_version": hybrid.REVIEW_CONTEXT_VERSION, "case_count": 1, "items": [{"case_id": "kept"}]}
    (tmp_path / "review_sample_100.json").write_text(json.dumps(migrated))
    (tmp_path / "replay_30d" / "replay_results.json").write_text(json.dumps({"items": []}))
    (tmp_path / "retrieval_documents.jsonl").write_text("")
    assert build_review_sample(tmp_path, 100) == migrated
    assert json.loads((tmp_path / "review_sample_100.json").read_text()) == migrated


def test_shadow_operations_do_not_modify_production_state_or_receipts(tmp_path: Path):
    paths = [Path(name) for name in ("bot_state.json", "confirmed_reply_receipt.json", "regular_post_receipt.json") if Path(name).exists()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    submit_shadow_comparison(
        config={**bot.reply_strategy["hybrid_retrieval"], "enabled": False},
        project_dir=tmp_path, research_run=RESEARCH, incoming_text="tax policy",
        parent_context="", thread_context="", lane="mention", target_id="1", production_lexical=[],
    )
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_digest_reads_only_local_shadow_status_and_handles_absence(tmp_path: Path):
    missing = digest.hybrid_retrieval_shadow_snapshot(tmp_path)
    assert missing["available"] is False
    runtime = tmp_path / "hybrid_reply_retrieval_runtime"
    runtime.mkdir()
    (runtime / "shadow_status.json").write_text(json.dumps({
        "events": 4, "completed": 3, "failures": 1, "top_5_overlap_percent": 60.0,
        "hybrid_changed_evidence_set": 2, "hybrid_only_evidence": 1, "lexical_only_evidence": 0,
        "no_evidence_disagreements": 1, "latency_p50_ms": 100, "latency_p95_ms": 200,
        "latency_max_ms": 250, "index_version": "1:test", "model_revision": MODEL_REVISION,
    }))
    value = digest.hybrid_retrieval_shadow_snapshot(tmp_path)
    assert value["available"] is True and value["events"] == 4
    report = digest.analyse([])
    report["hybrid_retrieval_shadow"] = value
    rendered = digest.render_markdown(report)
    assert "## Hybrid retrieval shadow" in rendered and "60.0%" in rendered


def test_digest_handles_malformed_optional_shadow_metrics(tmp_path: Path):
    runtime = tmp_path / "hybrid_reply_retrieval_runtime"
    runtime.mkdir()
    (runtime / "shadow_status.json").write_text(json.dumps({
        "events": "not-a-number",
        "completed": [],
        "top_5_overlap_percent": "bad-percent",
    }))

    value = digest.hybrid_retrieval_shadow_snapshot(tmp_path)

    assert value["available"] is False
    assert "unavailable" in value["reason"]


def test_index_output_parent_is_created_before_atomic_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    packets = {}
    for index in range(626):
        quote_id = f"{index:064x}"
        packets[quote_id] = {
            "quote_id": quote_id, "quote_text": f"Quote {index}", "verified_text": "",
            "verification_status": "exact", "research_confidence": "high",
        }

    class FakeEmbedder:
        def __init__(self, model_dir): pass
        def encode(self, texts, **kwargs):
            matrix = np.zeros((len(texts), 384), dtype=np.float32)
            matrix[:, 0] = 1.0
            return matrix

    monkeypatch.setattr(hybrid, "validate_corpus_invariants", lambda research: (packets, set(), {
        "research_packets_sha256": "c" * 64, "eligible_quote_id_set_sha256": "d" * 64,
    }))
    monkeypatch.setattr(hybrid, "LocalE5Embedder", FakeEmbedder)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model_manifest.json").write_text(json.dumps({"model_id": MODEL_ID}))
    output = tmp_path / "new" / "nested"
    manifest = build_index(tmp_path / "research", output, model_dir)
    assert manifest["document_count"] == 626
    assert (output / "index" / "embeddings.npy").is_file()
