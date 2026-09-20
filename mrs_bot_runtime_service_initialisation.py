"""Runtime health, lazy reply evidence and historical-context gate initialisation.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any

from mrs_bot_historical_context_delivery import historical_context_formatter_options


def initialise_bot_health_reporting(
    *,
    BASE_DIR: Any,
    BotHealthReporter: Any,
    HealthLoggingObserver: Any,
    INITIALISE_REQUESTED: Any,
    SELF_TEST_REQUESTED: Any,
    TEST_MODE: Any,
    _get_bot_health_reporter: Any,
    _get_bot_logger: Any,
    _set_bot_health_logging_observer: Any,
    _set_bot_health_reporter: Any,
    health_file_path_from_environment: Any,
) -> None:
    """Initialise fail-open telemetry after production logging is ready."""

    if _get_bot_health_reporter() is not None:
        return
    if SELF_TEST_REQUESTED or INITIALISE_REQUESTED:
        return
    try:
        health_path = health_file_path_from_environment(
            test_mode=TEST_MODE,
            test_base_dir=BASE_DIR if TEST_MODE else None,
        )
        if health_path is None:
            return
        reporter = BotHealthReporter(
            health_path,
            write_failure_callback=lambda message: _get_bot_logger().warning("%s", message),
        )
        observer = HealthLoggingObserver(reporter)
        _set_bot_health_reporter(reporter)
        _set_bot_health_logging_observer(observer)
        _get_bot_logger().addHandler(observer)
    except Exception:
        if TEST_MODE:
            raise
        _get_bot_logger().warning(
            "Bot health telemetry could not be initialised; bot operation continues",
            exc_info=True,
        )


def reply_evidence_repository(
    *,
    BASE_DIR: Any,
    Path: Any,
    ReplyEvidenceUnavailable: Any,
    SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH: Any,
    _get_bot_logger: Any,
    _get_reply_evidence_load_error: Any,
    _get_reply_evidence_repository_cache: Any,
    _set_reply_evidence_load_error: Any,
    _set_reply_evidence_repository_cache: Any,
):
    """Load claim evidence on first use and cache a fail-closed load failure."""
    if _get_reply_evidence_repository_cache() is not None:
        return _get_reply_evidence_repository_cache()
    if _get_reply_evidence_load_error() is not None:
        raise ReplyEvidenceUnavailable(_get_reply_evidence_load_error())

    from reply_evidence import EvidenceRepository

    research_path = Path(SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH)
    if not research_path.is_absolute():
        research_path = BASE_DIR / research_path
    factual_evidence_path = BASE_DIR / "reply_factual_evidence.json"
    try:
        _set_reply_evidence_repository_cache(EvidenceRepository(
            research_path,
            factual_evidence_path=factual_evidence_path,
        ))
    except Exception as exc:
        _set_reply_evidence_load_error((
            f"reply evidence unavailable at {research_path}: "
            f"{type(exc).__name__}: {exc}"
        ))
        _get_bot_logger().critical(
            "%s; conversational replies are disabled until a controlled restart",
            _get_reply_evidence_load_error(),
        )
        raise ReplyEvidenceUnavailable(_get_reply_evidence_load_error()) from exc
    _get_bot_logger().info(
        "Reply evidence loaded lazily. completed=%d unresolved=%d attribution_eligible=%d factual=%d passages=%d",
        _get_reply_evidence_repository_cache().completed_packet_count,
        _get_reply_evidence_repository_cache().unresolved_packet_count,
        _get_reply_evidence_repository_cache().attribution_eligible_packet_count,
        _get_reply_evidence_repository_cache().factual_evidence_count,
        len(_get_reply_evidence_repository_cache().passages),
    )
    return _get_reply_evidence_repository_cache()


def initialise_historical_context_semantic_gate(
    packets: dict[str, dict],
    *,
    BASE_DIR: Any,
    _get_bot_logger: Any,
    _get_historical_context_semantic_gate: Any,
    _set_historical_context_semantic_gate: Any,
    historical_context_reply: Any,
    log_event: Any,
) -> object:
    """Load the reviewed gate without failing the independent main-post lane."""
    if _get_historical_context_semantic_gate() is not None:
        return _get_historical_context_semantic_gate()

    from historical_context_formatter import (
        packet_is_attributed_to_margaret_thatcher,
    )
    from historical_context_reply_semantic_gate import (
        POLICY_VERSION,
        load_historical_context_semantic_gate,
    )

    eligible_quote_ids = {
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    gate = load_historical_context_semantic_gate(
        root=BASE_DIR,
        eligible_quote_ids=eligible_quote_ids,
        formatter_options=historical_context_formatter_options(historical_context_reply),
    )
    _set_historical_context_semantic_gate(gate)
    if gate.available:
        _get_bot_logger().info(
            "Historical-context semantic gate loaded. policy=%s ledger_sha256=%s "
            "projection_sha256=%s blocked=%d regular_post_eligibility_unchanged=true",
            POLICY_VERSION,
            gate.ledger_sha256,
            gate.projection_sha256,
            len(gate.blocked_dispositions),
        )
        log_event(
            "historical_context_semantic_gate",
            status="loaded",
            policy_version=POLICY_VERSION,
            ledger_sha256=gate.ledger_sha256,
            projection_sha256=gate.projection_sha256,
            blocked_quote_count=len(gate.blocked_dispositions),
        )
    else:
        _get_bot_logger().critical(
            "Historical-context semantic gate unavailable; only public context "
            "replies are fail-closed until a controlled restart. reason=%s",
            gate.reason,
        )
        log_event(
            "historical_context_semantic_gate",
            status="unavailable",
            policy_version=POLICY_VERSION,
            ledger_sha256=gate.ledger_sha256,
            reason=gate.reason,
        )
    return gate
