from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import MappingProxyType

import pytest

import exact_receipt_retirement as exact_retirement
import historical_context_formatter as context_formatter
import mrsMThatcher2 as bot
from historical_context_formatter import (
    AmbiguousContextReplyOutcome,
    HistoricalContextReplyStore,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_reply_semantic_gate import (
    EXPECTED_LEDGER_SHA256,
    EXPECTED_PROJECTION_SHA256,
    HistoricalContextSemanticGate,
    load_historical_context_semantic_gate,
)
from historical_context_packet_corrections import (
    PACKET_CORRECTIONS_FILENAME,
    packet_correction_id,
    validate_packet_corrections,
)
from historical_context_published_reply_semantic_review import build_review
from historical_context_source_curated_evidence import (
    CURATED_EVIDENCE_FILENAME,
)
from transaction_mutation_authority import issue_transaction_mutation_authority


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
LEDGER = ROOT / "historical_context_published_reply_semantic_review.json"
OPEN_FUTURE = (
    "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351"
)
NEW_POST_BASELINE_FUTURE = (
    "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7"
)
REMEDIATED_PRIMARY = (
    "38805634a94b830357ca31de921357af37ce17c69a295c26dfc4c091979549ad"
)
NEW_UNREVIEWED = (
    "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae"
)
OPEN_INSUFFICIENT = (
    "04d26fbb2aa5ad3824e1f452699b6e9265298b84a61458f524e19db273a70550"
)
RESOLVED_104653 = (
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
)
RESOLVED_WORDING_CAVEAT = (
    "1244294a90af385fe940a432fab296f43725644d318cb011c7080482115a0236"
)


def _copy_gate_inputs(
    destination: Path,
    *,
    include_current_corpus: bool = False,
) -> None:
    destination.mkdir(parents=True)
    copied_research = (
        destination
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    if include_current_corpus:
        shutil.copytree(RESEARCH, copied_research)
    else:
        copied_research.mkdir(parents=True)
        shutil.copy2(
            RESEARCH / "historical_context_source_role_audit.json",
            copied_research / "historical_context_source_role_audit.json",
        )
    for name in (
        "historical_context_published_reply_semantic_review.json",
        "historical_context_evidence_truth_audit.json",
        "historical_context_mtf_primary_review.json",
    ):
        shutil.copy2(ROOT / name, destination / name)


def _gate(
    blocked: dict[str, str] | None = None,
    *,
    available: bool = True,
    reviewed: dict[str, str] | None = None,
) -> HistoricalContextSemanticGate:
    if not available:
        return HistoricalContextSemanticGate.closed("test gate unavailable")
    return HistoricalContextSemanticGate(
        available=True,
        reason="",
        ledger_sha256=EXPECTED_LEDGER_SHA256,
        projection_sha256=EXPECTED_PROJECTION_SHA256,
        blocked_dispositions=MappingProxyType(dict(blocked or {})),
        reviewed_dispositions=MappingProxyType(dict(reviewed or {})),
    )


def _formatted(quote_id: str) -> dict[str, object]:
    text = "Context — Safe reviewed context."
    return {
        "quote_id": quote_id,
        "text": text,
        "character_count": len(text),
        "weighted_character_count": len(text),
        "raw_character_count": len(text),
        "maximum_length": 4000,
        "historical_confidence": "high",
        "meaning_included": False,
        "meaning_omitted": True,
        "meaning_decision_reason": "Meaning omitted by reviewed formatter.",
        "shortening_applied": False,
        "verification_label": "Exact wording",
        "verification_omitted": False,
        "source": {"title": "Source", "url": "", "source_type": "official"},
        "source_class": "original speech transcript",
        "source_omitted": False,
        "formatter_version": context_formatter.HISTORICAL_CONTEXT_FORMATTER_V5,
        "confidence_dimensions": {
            "attribution": "high",
            "wording": "high",
            "source_event": "high",
            "date": "high",
            "historical_context": "high",
            "interpretation": "high",
        },
        "source_role_audit_version": "test-source-role-audit-v1",
        "rendering_mode": "public",
        "template_variant": "compact_without_redundant_meaning",
    }


def _install_bot_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    packet: dict[str, str],
    gate: HistoricalContextSemanticGate,
) -> list[dict[str, object]]:
    # These fixtures isolate semantic policy and delivery. Give their synthetic
    # packets supported research so the separate completeness check can pass.
    packet.setdefault("verification_status", "exact")
    packet.setdefault("research_confidence", "high")
    packet.setdefault("stable_locator", "Reviewed fixture transcript, page 1")
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE", tmp_path / "context-receipt.json")
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "context-outbox.json",
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        context_formatter,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: ({packet["quote_id"]: packet}, set()),
    )
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    return events


def test_real_gate_is_hash_bound_and_contains_exact_115_13_6_7_policy():
    packets, _unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    eligible = {
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }

    gate = load_historical_context_semantic_gate(
        root=ROOT,
        eligible_quote_ids=eligible,
    )

    assert gate.available is True
    assert gate.reason == ""
    assert gate.ledger_sha256 == EXPECTED_LEDGER_SHA256
    assert gate.projection_sha256 == EXPECTED_PROJECTION_SHA256
    assert len(gate.blocked_dispositions) == 13
    assert list(gate.blocked_dispositions.values()).count(
        "future_correction_needed"
    ) == 6
    assert list(gate.blocked_dispositions.values()).count(
        "insufficient_to_assess"
    ) == 7
    assert gate.disposition(OPEN_FUTURE) == "future_correction_needed"
    assert gate.disposition(NEW_POST_BASELINE_FUTURE) == "future_correction_needed"
    assert gate.disposition(OPEN_INSUFFICIENT) == "insufficient_to_assess"
    assert gate.disposition(NEW_UNREVIEWED) is None
    assert gate.reviewed_disposition(
        "e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b"
    ) == "supported_as_published"
    assert gate.reviewed_disposition("f" * 64) is None
    assert gate.blocks(NEW_UNREVIEWED) is False
    assert gate.blocks(REMEDIATED_PRIMARY) is False
    assert gate.blocks(RESOLVED_104653) is False
    assert len(eligible) == 611
    with pytest.raises(TypeError):
        gate.blocked_dispositions["f" * 64] = "future_correction_needed"  # type: ignore[index]


def test_missing_corrupt_and_stale_gate_inputs_close_the_lane(tmp_path: Path):
    missing = load_historical_context_semantic_gate(root=tmp_path / "missing")
    assert missing.available is False
    assert missing.blocks(RESOLVED_104653) is True

    copied = tmp_path / "copied"
    _copy_gate_inputs(copied)
    ledger_path = copied / LEDGER.name
    ledger_path.write_bytes(ledger_path.read_bytes() + b" ")
    corrupt = load_historical_context_semantic_gate(root=copied)
    assert corrupt.available is False
    assert "SHA-256 differs" in corrupt.reason

    _copy_gate_inputs(tmp_path / "stale")
    stale_path = tmp_path / "stale" / LEDGER.name
    stale_value = json.loads(stale_path.read_text(encoding="utf-8"))
    stale_value["remaining_items"][0]["disposition"] = "insufficient_to_assess"
    stale_path.write_text(
        json.dumps(stale_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stale_sha = hashlib.sha256(stale_path.read_bytes()).hexdigest()
    stale = load_historical_context_semantic_gate(
        root=tmp_path / "stale",
        expected_ledger_sha256=stale_sha,
    )
    assert stale.available is False
    assert "review differs" in stale.reason

    eligible = set(json.loads(
        (RESEARCH / "research_packets.json").read_text(encoding="utf-8")
    )["items"])
    eligible.remove(OPEN_FUTURE)
    ineligible = load_historical_context_semantic_gate(
        root=ROOT,
        eligible_quote_ids=eligible,
    )
    assert ineligible.available is False
    assert "ineligible quote" in ineligible.reason


def test_gate_closes_when_truth_declared_correction_sidecar_is_missing(
    tmp_path: Path,
):
    copied = tmp_path / "missing-correction"
    _copy_gate_inputs(copied, include_current_corpus=True)
    (
        copied
        / "semantic_alignment_research"
        / "quote_research_full_001"
        / PACKET_CORRECTIONS_FILENAME
    ).unlink()

    gate = load_historical_context_semantic_gate(root=copied)

    assert gate.available is False
    assert gate.blocks(RESOLVED_104653) is True
    assert "required historical-context packet corrections are missing" in gate.reason


def test_gate_closes_on_coherently_rehashed_correction_drift(tmp_path: Path):
    copied = tmp_path / "rehashed-correction"
    _copy_gate_inputs(copied, include_current_corpus=True)
    copied_research = (
        copied
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    corrections_path = copied_research / PACKET_CORRECTIONS_FILENAME
    corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
    item = corrections["items"][REMEDIATED_PRIMARY]
    item["corrected_value"] = (
        "Thatcher argued for abandoning all clear objectives."
    )
    item["corrected_value_sha256"] = hashlib.sha256(
        item["corrected_value"].encode("utf-8")
    ).hexdigest()
    item["correction_id"] = packet_correction_id(item)
    packets = json.loads(
        (copied_research / "research_packets.json").read_text(encoding="utf-8")
    )["items"]
    curated = json.loads(
        (copied_research / CURATED_EVIDENCE_FILENAME).read_text(encoding="utf-8")
    )
    validate_packet_corrections(corrections, packets, curated)
    corrections_path.write_text(
        json.dumps(corrections, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    gate = load_historical_context_semantic_gate(root=copied)

    assert gate.available is False
    assert gate.blocks(REMEDIATED_PRIMARY) is True
    assert "historical-context packet correction SHA-256 differs" in gate.reason


def test_gate_rejects_present_corrections_omitted_from_authoritative_hashes(
    tmp_path: Path,
):
    copied = tmp_path / "unbound-correction"
    _copy_gate_inputs(copied, include_current_corpus=True)
    copied_research = (
        copied
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    truth_path = copied / "historical_context_evidence_truth_audit.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    truth["input_hashes"].pop(PACKET_CORRECTIONS_FILENAME)
    truth_path.write_text(
        json.dumps(truth, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ledger_path = copied / LEDGER.name
    review = build_review(
        truth_path,
        copied_research / "historical_context_source_role_audit.json",
        copied / "historical_context_mtf_primary_review.json",
        reference_root=copied,
    )
    ledger_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    gate = load_historical_context_semantic_gate(
        root=copied,
        expected_ledger_sha256=hashlib.sha256(
            ledger_path.read_bytes()
        ).hexdigest(),
    )

    assert gate.available is False
    assert (
        "packet corrections lack an authoritative SHA-256 declaration"
        in gate.reason
    )

    # A legacy corpus with neither a declaration nor a sidecar remains valid;
    # only the present-but-unbound mismatch is rejected.
    (copied_research / PACKET_CORRECTIONS_FILENAME).unlink()
    packets, unresolved = load_and_validate_corpus(
        copied_research,
        require_source_role_audit=True,
        require_packet_corrections_hash_binding=True,
    )
    assert len(packets) == 627
    assert len(unresolved) == 5


def test_gate_closes_when_a_reviewed_allowed_current_render_drifts(
    monkeypatch: pytest.MonkeyPatch,
):
    original = context_formatter.format_context_reply_public

    def drifted(packet, **kwargs):
        rendered = original(packet, **kwargs)
        if packet["quote_id"] == REMEDIATED_PRIMARY and rendered is not None:
            return {**rendered, "text": rendered["text"] + "\nDrifted text."}
        return rendered

    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        drifted,
    )

    gate = load_historical_context_semantic_gate(root=ROOT)

    assert gate.available is False
    assert gate.blocks(REMEDIATED_PRIMARY) is True
    assert "semantic review current rendering differs" in gate.reason


def test_gate_binds_the_actual_runtime_formatter_options():
    packets, _unresolved = load_and_validate_corpus(
        RESEARCH,
        require_source_role_audit=True,
    )
    configured = context_formatter.format_context_reply_public(
        packets[RESOLVED_WORDING_CAVEAT],
        include_verification=False,
    )
    assert configured is not None
    assert "independently verified" not in configured["text"]

    gate = load_historical_context_semantic_gate(
        root=ROOT,
        formatter_options={
            "maximum_length": 4000,
            "include_meaning": True,
            "include_source": True,
            "include_verification": False,
        },
    )

    assert gate.available is False
    assert gate.blocks(RESOLVED_WORDING_CAVEAT) is True
    assert "semantic review current rendering differs" in gate.reason


def test_bot_passes_live_context_formatter_options_to_the_gate(
    monkeypatch: pytest.MonkeyPatch,
):
    import historical_context_reply_semantic_gate as gate_module

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {
            "enabled": True,
            "maximum_length": 1200,
            "include_meaning": False,
            "include_source": True,
            "include_verification": False,
        },
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", None)
    monkeypatch.setattr(
        gate_module,
        "load_historical_context_semantic_gate",
        lambda **kwargs: calls.append(kwargs) or _gate(),
    )

    bot.initialise_historical_context_semantic_gate(
        {
            REMEDIATED_PRIMARY: {
                "speaker": "Margaret Thatcher",
                "verification_status": "exact",
            }
        }
    )

    assert calls[0]["formatter_options"] == {
        "maximum_length": 1200,
        "include_meaning": False,
        "include_source": True,
        "include_verification": False,
    }


@pytest.mark.parametrize(
    ("gate", "expected_reason", "expected_disposition"),
    [
        (
            _gate({OPEN_FUTURE: "future_correction_needed"}),
            "open_semantic_review",
            "future_correction_needed",
        ),
        (_gate(available=False), "semantic_review_gate_unavailable", None),
    ],
)
def test_blocked_or_unavailable_gate_skips_before_formatter_or_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate: HistoricalContextSemanticGate,
    expected_reason: str,
    expected_disposition: str | None,
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=gate,
    )
    reconciled: list[bool] = []
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: reconciled.append(True) or False,
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: pytest.fail("blocked context must not reach store.post"),
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: pytest.fail("blocked context must not be formatted"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail("blocked context must not contact X"),
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash="b" * 64,
        quote_text="Alias text resolved to canonical packet",
        parent_post_id="123",
    )

    assert reconciled == [True]
    assert result["status"] == "skipped_future_policy"
    assert result["quote_id"] == OPEN_FUTURE
    assert result["reason"] == expected_reason
    assert result["semantic_review_disposition"] == expected_disposition
    assert events[-1]["status"] == "skipped_future_policy"
    assert events[-1]["quote_id"] == OPEN_FUTURE


def test_blocked_dry_run_emits_no_public_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: pytest.fail("dry-run must not reconcile durable state"),
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: pytest.fail("blocked dry-run must not render"),
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=OPEN_FUTURE,
        quote_text="Canonical quote",
        parent_post_id="123",
        dry_run=True,
    )

    assert result["status"] == "skipped_future_policy"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("quote_id", [RESOLVED_104653, "f" * 64])
def test_resolved_104653_and_other_allowed_quote_follow_public_post_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quote_id: str,
):
    packet = {"quote_id": quote_id, "quote_text": "Allowed quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    formatted = _formatted(quote_id)
    format_calls: list[object] = []
    post_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: False,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *args, **kwargs: format_calls.append((args, kwargs)) or formatted,
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: post_calls.append(kwargs)
        or {"status": "completed", "reply_post_id": "456"},
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Allowed quote",
        parent_post_id="123",
    )

    assert result["status"] == "completed"
    assert len(format_calls) == 1
    assert len(post_calls) == 1
    assert post_calls[0]["quote_id"] == quote_id
    posted = [
        event
        for event in events
        if event["event"] == "historical_context_reply_posted"
    ]
    assert posted == [
        {
            "event": "historical_context_reply_posted",
            "event_version": 1,
            "lane": "historical_context_reply",
            "parent_post_id": "123",
            "reply_post_id": "456",
            "root_post_id": "123",
            "conversation_id": "123",
            "reply_text": "Context — Safe reviewed context.",
            "quote_id": quote_id,
            "reply_created_at": None,
            "publication_authority": "confirmed_transport",
        }
    ]
    assert not {
        "author_id",
        "account_id",
        "api_key",
        "token",
        "secret",
    } & set(posted[0])


def test_completed_reply_emits_existing_semantic_review_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    quote_id = "e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b"
    packet = {"quote_id": quote_id, "quote_text": "Allowed reviewed quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate(reviewed={quote_id: "supported_as_published"}),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: False,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: _formatted(quote_id),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: {
            "status": "completed",
            "reply_post_id": "456",
        },
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Allowed reviewed quote",
        parent_post_id="123",
    )

    assert result["status"] == "completed"
    completed = next(
        event
        for event in events
        if event["event"] == "historical_context_reply"
        and event.get("status") == "completed"
    )
    assert completed["status"] == "completed"
    assert completed["semantic_review_disposition"] == "supported_as_published"
    assert completed["semantic_review_ledger_sha256"] == EXPECTED_LEDGER_SHA256
    assert completed["semantic_review_projection_sha256"] == (
        EXPECTED_PROJECTION_SHA256
    )
    assert sum(
        event["event"] == "historical_context_reply_posted" for event in events
    ) == 1


def test_already_completed_context_emits_same_stable_publication_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_id = "c" * 64
    packet = {"quote_id": quote_id, "quote_text": "Allowed quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate(),
    )
    monkeypatch.setattr(HistoricalContextReplyStore, "reconcile_receipt", lambda self: False)
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: _formatted(quote_id),
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: calls.append(kwargs)
        or {
            "status": "already_completed",
            "reply_post_id": "456",
            "reply_text": "Context — the already durable text.",
            "reply_epoch": 1_800_000_000,
        },
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Allowed quote",
        parent_post_id="123",
    )

    assert result["status"] == "already_completed"
    assert len(calls) == 1
    posted = [
        event for event in events if event["event"] == "historical_context_reply_posted"
    ]
    assert len(posted) == 1
    assert posted[0]["parent_post_id"] == "123"
    assert posted[0]["reply_post_id"] == "456"
    assert posted[0]["root_post_id"] == "123"
    assert posted[0]["reply_text"] == "Context — the already durable text."
    assert posted[0]["reply_created_at"] is None


@pytest.mark.parametrize(
    ("dry_run", "store_result"),
    [
        (False, {"status": "failed"}),
        (True, {"status": "dry_run"}),
    ],
)
def test_failed_and_dry_context_outcomes_emit_no_posted_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    store_result: dict[str, object],
) -> None:
    quote_id = "d" * 64
    packet = {"quote_id": quote_id, "quote_text": "Allowed quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate(),
    )
    monkeypatch.setattr(HistoricalContextReplyStore, "reconcile_receipt", lambda self: False)
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: _formatted(quote_id),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **_kwargs: dict(store_result),
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Allowed quote",
        parent_post_id="123",
        dry_run=dry_run,
    )

    assert result["status"] == store_result["status"]
    assert not any(
        event["event"] == "historical_context_reply_posted" for event in events
    )
    capsys.readouterr()


def test_ambiguous_preexisting_context_receipt_is_not_hidden_by_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    events = _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: (_ for _ in ()).throw(
            AmbiguousContextReplyOutcome("ambiguous sending receipt")
        ),
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: pytest.fail("ambiguous receipt must stop first"),
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        bot.maybe_post_historical_context_reply(
            quote_hash=OPEN_FUTURE,
            quote_text="Canonical quote",
            parent_post_id="123",
        )
    assert not any(
        event["event"] == "historical_context_reply_posted" for event in events
    )


def test_ambiguous_preexisting_context_receipt_is_not_hidden_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: (_ for _ in ()).throw(
            AmbiguousContextReplyOutcome("ambiguous sending receipt")
        ),
    )
    monkeypatch.setattr(
        context_formatter,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled configuration must reconcile before corpus loading"
        ),
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        bot.maybe_post_historical_context_reply(
            quote_hash=OPEN_FUTURE,
            quote_text="Canonical quote",
            parent_post_id="123",
        )


def test_ambiguous_preexisting_context_receipt_is_not_hidden_by_missing_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: (_ for _ in ()).throw(
            AmbiguousContextReplyOutcome("ambiguous sending receipt")
        ),
    )
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: pytest.fail(
            "receipt ambiguity must stop before packet lookup"
        ),
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        bot.maybe_post_historical_context_reply(
            quote_hash=OPEN_FUTURE,
            quote_text="Canonical quote",
            parent_post_id="123",
        )


def test_reconciled_receipt_for_another_parent_cannot_bypass_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    packet = {"quote_id": OPEN_FUTURE, "quote_text": "Canonical quote"}
    _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "reconcile_receipt",
        lambda self: True,
    )
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **_kwargs: pytest.fail(
            "an unrelated reconciled receipt must not bypass the gate"
        ),
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrelated reconciled receipt must not reach the formatter"
        ),
    )

    result = bot.maybe_post_historical_context_reply(
        quote_hash=OPEN_FUTURE,
        quote_text="Canonical quote",
        parent_post_id="123",
    )

    assert result["status"] == "skipped_future_policy"
    assert result["reason"] == "open_semantic_review"


def test_regular_receipt_replay_clears_after_policy_skip_without_context_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    open_record = next(
        row for row in ledger["records"] if row["quote_id"] == OPEN_FUTURE
    )
    quote_text = open_record["quote_text"]
    packet = {"quote_id": OPEN_FUTURE, "quote_text": quote_text}
    _install_bot_context(
        monkeypatch,
        tmp_path,
        packet=packet,
        gate=_gate({OPEN_FUTURE: "future_correction_needed"}),
    )
    regular_receipt = tmp_path / "regular-receipt.json"
    exact_retirement.initialise_retirement_ledger(
        regular_receipt,
        mutation_authority=issue_transaction_mutation_authority(
            lambda _operation: None,
            operation="semantic-gate replay fixture ledger initialisation",
        ),
    )
    bot.atomic_write_json(
        regular_receipt,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": OPEN_FUTURE,
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": quote_text,
        },
        durable=True,
    )
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular_receipt)
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: pytest.fail("blocked replay must not format"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail("blocked replay must not contact X"),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict[str, object] = {}
    assert bot.reconcile_regular_post_receipt(
        lines_used,
        images_used,
        state,
    ) is True

    assert not regular_receipt.exists()
    assert OPEN_FUTURE in lines_used
    assert "t01.jpg" in images_used
    assert state["last_main_post_id"] == "950001"
    assert not (tmp_path / "history.json").exists()
    assert not (tmp_path / "context-receipt.json").exists()
