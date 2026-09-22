"""Render prepared log-digest reports as deterministic Markdown.

This module only formats supplied values. It does not inspect files, resolve
project paths, read a clock, import the digest entry point or initialise the bot.
Receipt lifecycle classification is supplied separately by the caller so that
analysis stays outside presentation. Context transaction outcomes come from
the prepared report; older reports use the shared pure preparation helper.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from mrs_log_digest_consistency_events import prepare_context_transaction_outcomes
from mrs_log_digest_values import (
    GENERATED_POLICIES,
    REMOTE_WRITE_RECEIPT_ROLE_LABELS,
    UNKNOWN_MISSING_STATE_FIELD,
    QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT,
    parse_dt,
    int_or_none,
    short,
    plural_count,
    cooldown_state_text,
    _parse_openai_cost_decimal,
    _openai_decimal_text,
    _parse_openai_utc,
    _openai_window_text,
    _human_snapshot_age,
)


def format_rank(value: Any, *, mean: bool = False) -> str:
    """Format an ordinal rank without exposing floating-point noise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    number = float(value)
    if not math.isfinite(number):
        return "n/a"
    if mean:
        return f"{number:.2f}"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_display_number(value: Any, *, decimal_places: int = 2) -> str:
    """Format a numeric display value without changing its stored representation."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    raw = str(value)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    if not math.isfinite(number):
        return raw
    rendered = f"{number:.{decimal_places}f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def format_openai_usd(value: Any) -> str:
    """Render one cache decimal as provider-published US dollars."""

    amount = (
        value
        if isinstance(value, Decimal)
        else _parse_openai_cost_decimal(value, label="OpenAI cost")
    )
    return f"US${_openai_decimal_text(amount)}"


def md_table_row(cols: List[Any], *, cell_limit: int = 240) -> str:
    """Return the Markdown table row."""
    def esc(x: Any) -> str:
        s = short(x, cell_limit).replace("|", "\\|")
        return s
    return "| " + " | ".join(esc(c) for c in cols) + " |"


def _cfg_bool(cfg: Dict[str, Any], key: str) -> Optional[bool]:
    if key not in cfg:
        return None
    val = str(cfg.get(key)).strip().lower()
    if val in {"true", "1", "yes", "on"}:
        return True
    if val in {"false", "0", "no", "off"}:
        return False
    return None


def _seconds_to_minutes_text(value: Any) -> str:
    n = int_or_none(value)
    if n is None:
        return "?"
    if n % 60 == 0:
        return f"{n // 60} min"
    return f"{n} sec"


def _source_bits_for_state_config(st: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    bits: List[str] = []
    if st.get("_carried_forward"):
        bits.append("state carried forward")
    if st.get("_filled_from_previous"):
        bits.append("state partly filled")
    if cfg.get("_carried_forward"):
        bits.append("config carried forward")
    if cfg.get("_carried_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
    if cfg.get("_filled_from_previous"):
        bits.append("config partly filled from previous digest state")
    if cfg.get("_filled_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
    return bits


def _carried_state_presentation(
    st: Dict[str, Any], summary: Dict[str, Any]
) -> Dict[str, Any]:
    """Describe carried state freshness without changing persisted context."""
    carried = bool(st.get("_carried_forward"))
    if not carried:
        return {"stale": False, "snapshot_only": False, "age": None}
    try:
        state_time = parse_dt(st.get("time"))
    except (TypeError, ValueError):
        state_time = None
    try:
        window_start = parse_dt(summary.get("time_start"))
    except (TypeError, ValueError):
        window_start = None
    try:
        window_end = parse_dt(summary.get("time_end"))
    except (TypeError, ValueError):
        window_end = None
    stale = bool(
        state_time is not None
        and window_start is not None
        and state_time < window_start
    )
    age = (
        _human_snapshot_age((window_end - state_time).total_seconds())
        if state_time is not None and window_end is not None
        else None
    )
    return {
        "stale": stale,
        "snapshot_only": stale or state_time is None,
        "age": age,
    }


def _compact_counts(values: Dict[str, Any]) -> str:
    visible = [(name, count) for name, count in values.items() if isinstance(count, int) and count > 0]
    return ", ".join(f"{name}={count}" for name, count in visible) or "none observed"


def _render_overview(report: Dict[str, Any], out: List[str]) -> None:
    s = report["summary"]
    out.append("# MrsMThatcher log digest")
    out.append("")
    out.append(
        f"Observed event window: `{s.get('time_start')}` → `{s.get('time_end')}`"
    )
    out.append(f"Project directory: `{report.get('project_dir') or 'unavailable'}`")
    if report.get("requested_since"):
        mode = "exclusive" if report.get("since_exclusive") else "inclusive"
        source = report.get("since_source") or "manual"
        out.append(f"Requested since: `{report.get('requested_since')}` ({mode}, source={source})")
    if report.get("resume_cursor_mode") == "fingerprint_tail":
        out.append(
            "Resume cursor: `physical append order` "
            f"({report.get('resume_tail_match_length', 0)} fingerprint(s) matched)"
        )
    if report.get("local_clock_rollback_count"):
        out.append(
            f"Input warning: **detected {report.get('local_clock_rollback_count')} local clock rollback(s); "
            "records are shown and resumed in physical append order**"
        )
    if report.get("resume_state_file"):
        out.append(f"Resume state file: `{report.get('resume_state_file')}`")
    out.append(f"Records parsed: `{s.get('record_count')}`")
    input_warning = report.get("input_warning")
    if input_warning:
        out.append(f"Input warning: **{input_warning}**")
    retention = report.get("input_retention_coverage") or {}
    if retention.get("requested_since"):
        coverage = retention.get("requested_start_covered")
        coverage_text = (
            "yes" if coverage is True else "no" if coverage is False else "unknown"
        )
        out.append(
            "Retained-log coverage of requested start: "
            f"**{coverage_text}**; earliest retained timestamp: "
            f"`{retention.get('earliest_retained_timestamp') or 'unavailable'}`."
        )
    out.append("")

    input_files = report.get("input_files") or []
    if input_files:
        out.append("## Input files")
        out.append("```text")
        for item in input_files:
            out.append(str(item.get("path")))
            if not item.get("exists"):
                out.append("  missing")
                continue
            out.append(f"  size={item.get('size')}  mtime={item.get('mtime')}")
            out.append(
                f"  first_timestamp={item.get('first_timestamp')}  "
                f"last_timestamp={item.get('last_timestamp')}"
            )
            out.append(
                f"  total_records={item.get('total_records')}  "
                f"records_after_since={item.get('records_after_since')}  "
                f"records_in_window_before_dedupe={item.get('records_in_window')}"
            )
        out.append("```")
        out.append("")

    out.append("## Headline")
    out.append(s.get("headline") or "")
    out.append("")


def _render_latest_state(report: Dict[str, Any], out: List[str]) -> None:
    s = report["summary"]
    st = report.get("latest_state") or {}
    state_presentation = _carried_state_presentation(st, s)
    stale_state_snapshot = state_presentation["stale"] is True
    state_snapshot_only = state_presentation["snapshot_only"] is True
    state_label_prefix = "snapshot_" if state_snapshot_only else ""
    if st:
        if stale_state_snapshot:
            out.append("## Latest state (stale carried-forward snapshot)")
        elif state_snapshot_only:
            out.append("## Latest state (carried-forward snapshot; age unavailable)")
        else:
            out.append("## Latest state")
        state_source = st.get("_state_source")
        state_source_path = st.get("_state_source_path")
        if stale_state_snapshot:
            out.append(
                f"State timestamp: `{st.get('time')}` (carried forward from previous "
                f"digest state; stale snapshot age at window end: "
                f"{state_presentation.get('age') or 'unavailable'})"
            )
            out.append(
                "Historical snapshot values only; the counters and schedules below "
                "are not current."
            )
        elif state_snapshot_only:
            out.append(
                "State timestamp: `unavailable` (carried forward from previous digest "
                "state; age and staleness unavailable)"
            )
            out.append(
                "Snapshot values only; without a state timestamp their currentness "
                "cannot be established."
            )
        elif st.get("_carried_forward"):
            out.append(f"State timestamp: `{st.get('time')}` (carried forward from previous digest state)")
        elif st.get("_filled_from_previous"):
            out.append(f"State timestamp: `{st.get('time')}` (current snapshot with missing fields filled from previous digest state)")
        elif state_source == "bot_state.json":
            path_text = f" `{state_source_path}`" if state_source_path else ""
            out.append(f"State timestamp: `{st.get('time')}` (authoritative current state from{path_text})")
        elif st.get("_partial"):
            out.append(f"State timestamp: `{st.get('time')}` (partial/truncated log snapshot)")
        else:
            out.append(f"State timestamp: `{st.get('time')}`")
        out.append("")
        out.append("```text")
        out.append(f"{state_label_prefix}daily_reply_count       = {st.get('daily_reply_count')}  date={st.get('daily_reply_date')}")
        out.append(f"{state_label_prefix}daily_quote_reply_count = {st.get('daily_quote_reply_count')}  date={st.get('daily_quote_reply_date')}")
        if st.get("next_reply_lane_priority") is not None:
            out.append(f"{state_label_prefix}next_reply_lane_priority = {st.get('next_reply_lane_priority')}")
        if st.get("skipped_hot_reply_count") is not None:
            out.append(f"{state_label_prefix}skipped_hot_reply_count = {st.get('skipped_hot_reply_count')}")
        out.append(f"{state_label_prefix}quote_spam_author_count = {st.get('quote_spam_author_count')}")
        generation_epoch = report.get("generation_epoch")
        api_cooldown_status = cooldown_state_text(st.get("api_cooldown_until_epoch"), generation_epoch)
        api_cooldown_suffix = f"  {api_cooldown_status}" if api_cooldown_status else ""
        api_cooldown_human = st.get("api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}x_read_api_cooldown_until = {st.get('api_cooldown_until_epoch')}  "
            f"{api_cooldown_human}{api_cooldown_suffix}"
        )
        if st.get("api_cooldown_reason"):
            out.append(f"{state_label_prefix}x_read_api_cooldown_reason = {st.get('api_cooldown_reason')}")
        x_write_api_cooldown_status = cooldown_state_text(st.get("x_write_api_cooldown_until_epoch"), generation_epoch)
        x_write_api_cooldown_suffix = f"  {x_write_api_cooldown_status}" if x_write_api_cooldown_status else ""
        x_write_api_cooldown_human = st.get("x_write_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}x_write_api_cooldown_until = {st.get('x_write_api_cooldown_until_epoch')}  "
            f"{x_write_api_cooldown_human}{x_write_api_cooldown_suffix}"
        )
        if st.get("x_write_api_cooldown_reason"):
            out.append(f"{state_label_prefix}x_write_api_cooldown_reason = {st.get('x_write_api_cooldown_reason')}")
        openai_api_cooldown_status = cooldown_state_text(st.get("openai_api_cooldown_until_epoch"), generation_epoch)
        openai_api_cooldown_suffix = f"  {openai_api_cooldown_status}" if openai_api_cooldown_status else ""
        openai_api_cooldown_human = st.get("openai_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}openai_api_cooldown_until = {st.get('openai_api_cooldown_until_epoch')}  "
            f"{openai_api_cooldown_human}{openai_api_cooldown_suffix}"
        )
        if st.get("openai_api_cooldown_reason"):
            out.append(f"{state_label_prefix}openai_api_cooldown_reason = {st.get('openai_api_cooldown_reason')}")
        quote_api_cooldown_status = cooldown_state_text(st.get("quote_api_cooldown_until_epoch"), generation_epoch)
        quote_api_cooldown_suffix = f"  {quote_api_cooldown_status}" if quote_api_cooldown_status else ""
        quote_api_cooldown_human = st.get("quote_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}quote_api_cooldown_until = {st.get('quote_api_cooldown_until_epoch')}  "
            f"{quote_api_cooldown_human}{quote_api_cooldown_suffix}"
        )
        if st.get("quote_api_cooldown_reason"):
            out.append(f"{state_label_prefix}quote_api_cooldown_reason = {st.get('quote_api_cooldown_reason')}")
        out.append(f"{state_label_prefix}last_main_post_id       = {st.get('last_main_post_id')}")
        out.append(f"{state_label_prefix}last_seen_mention_id    = {st.get('last_seen_mention_id')}")
        out.append(
            f"{state_label_prefix}mention_backlog_active   = {str(bool(st.get('mention_backlog_active'))).lower()}"
        )
        if st.get("mention_backlog_active"):
            backlog_age = st.get("mention_backlog_age_seconds")
            out.append(
                f"{state_label_prefix}mention_backlog_age      = "
                + (
                    _human_snapshot_age(float(backlog_age))
                    if type(backlog_age) in {int, float}
                    else "unavailable"
                )
            )
            out.append(
                f"{state_label_prefix}mention_backlog_pages    = {st.get('mention_backlog_pages_completed')}"
            )
            out.append(
                f"{state_label_prefix}mention_backlog_highest  = {st.get('mention_backlog_highest_mention_id')}"
            )
            out.append(
                f"{state_label_prefix}mention_backlog_token    = {str(bool(st.get('mention_backlog_continuation_token_present'))).lower()}"
            )
        out.append(
            f"{state_label_prefix}mention_pending_candidates = {st.get('mention_pending_candidate_count')}"
        )
        out.append(
            f"{state_label_prefix}active_author_evaluation_quarantines = {st.get('active_author_evaluation_quarantine_count')}"
        )
        if st.get("last_quote_post_epoch") is not None:
            out.append(f"{state_label_prefix}last_quote_post         = {st.get('last_quote_post_human')}  epoch={st.get('last_quote_post_epoch')}")
        out.append(f"{state_label_prefix}next_quote_post         = {st.get('next_quote_post_human')}  epoch={st.get('next_quote_post_epoch')}")
        out.append(f"{state_label_prefix}next_meme_post          = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
        if st.get("next_meme_schedule_mode") is not None:
            out.append(f"{state_label_prefix}next_meme_mode          = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
        if st.get("meme_anchor_quote_post_epoch"):
            out.append(f"{state_label_prefix}meme_anchor_quote_post  = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
        if st.get("meme_schedule_version") is not None:
            out.append(f"{state_label_prefix}meme_schedule_version   = {st.get('meme_schedule_version')}")
        out.append(f"{state_label_prefix}posted_meme_count       = {st.get('posted_meme_count')}")
        out.append("```")
        if st.get("posted_meme_filenames_tail"):
            out.append(
                "Snapshot recent posted meme filenames:"
                if state_snapshot_only
                else "Recent posted meme filenames:"
            )
            out.append("```text")
            for name in st["posted_meme_filenames_tail"]:
                out.append(str(name))
            out.append("```")
        out.append("")

    if not st:
        runtime_state_status = report.get("runtime_state_status") or {}
        out.append("## Latest state")
        out.append(
            "Current bot runtime state: **unavailable** "
            f"(`{runtime_state_status.get('status') or 'not read'}`; "
            f"source `{runtime_state_status.get('path') or 'unavailable'}`)."
        )
        out.append(
            "No digest resume snapshot or historical log snapshot is used as current state."
        )
        out.append("")


def _render_mention_backlog(report: Dict[str, Any], out: List[str]) -> None:
    st = report.get("latest_state") or {}
    mention_control = report.get("mention_backlog_and_quarantine") or {}
    mention_control_counts = mention_control.get("event_counts") or {}
    out.append("## Mention backlog and author evaluation quarantine")
    if st:
        author_ids = st.get("active_author_evaluation_quarantine_author_ids") or []
        out.append(
            "Active mention backlog: "
            + ("yes" if st.get("mention_backlog_active") else "no")
            + "; active author evaluation quarantines: "
            + str(st.get("active_author_evaluation_quarantine_count", 0))
            + "."
        )
        out.append(
            "Active quarantined author IDs: "
            + (", ".join(str(value) for value in author_ids) if author_ids else "none")
            + "."
        )
    out.append(
        "Observed events: starts={starts}, progress={progress}, completions={completions}, "
        "resets={resets}, quarantine_starts={quarantine_starts}, quarantine_skips={quarantine_skips}.".format(
            starts=mention_control_counts.get("mention_backlog_started", 0),
            progress=mention_control_counts.get("mention_backlog_progress", 0),
            completions=mention_control_counts.get("mention_backlog_completed", 0),
            resets=mention_control_counts.get("mention_backlog_reset", 0),
            quarantine_starts=mention_control_counts.get(
                "author_evaluation_quarantine_started", 0
            ),
            quarantine_skips=mention_control_counts.get(
                "author_evaluation_quarantine_skip", 0
            ),
        )
    )
    out.append(
        "Pipeline evaluations skipped by active author quarantine "
        "(explicit event counts only): "
        + str(mention_control.get("pipeline_evaluations_skipped", 0))
        + "."
    )
    out.append("")


def _render_retained_snapshots(report: Dict[str, Any], out: List[str]) -> None:
    retained_state = report.get("historical_retained_state")
    retained_config = report.get("historical_retained_config")
    if retained_state or retained_config:
        out.append("## Historical retained diagnostic snapshots")
        out.append(
            "These snapshots come from prior digest context and are preserved "
            "for historical diagnosis only; they are not current runtime state "
            "or effective live configuration."
        )
        if isinstance(retained_state, dict) and retained_state:
            out.append(
                "Retained state snapshot timestamp: "
                f"`{retained_state.get('time') or 'unavailable'}`."
            )
            out.append("Historical state snapshot (diagnostic only):")
            out.append("```json")
            out.extend(
                json.dumps(
                    retained_state,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).splitlines()
            )
            out.append("```")
        if isinstance(retained_config, dict) and retained_config:
            out.append("Historical configuration snapshot (not effective live configuration):")
            out.append("```json")
            out.extend(
                json.dumps(
                    retained_config,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).splitlines()
            )
            out.append("```")
        out.append("")


def _render_remote_write_safety(report: Dict[str, Any], out: List[str]) -> None:
    safety = report.get("remote_write_safety") or {}
    if safety:
        out.append("## Remote-write safety")
        if safety.get("configured") is not True:
            out.append(
                "No activated remote-write protocol state was found in the current "
                "project directory; this section is informational for non-production checkouts."
            )
        elif safety.get("available") is not True:
            out.append(
                "Current safety state is **unavailable**: "
                + str(safety.get("reason") or "inspection failed")
            )
        else:
            status = str(safety.get("status") or "unavailable")
            out.append(
                "Current read-only filesystem snapshot: "
                f"**{status.replace('_', ' ')}**"
                + (
                    "; active safety barriers are present."
                    if safety.get("blocking") is True
                    else "; no active transaction safety barrier is present."
                )
            )
            out.append(
                "Snapshot observed at `"
                + str(safety.get("observed_at") or "unavailable")
                + "`; selected report-window end `"
                + str(safety.get("selected_window_end") or "unavailable")
                + "` (relationship: **"
                + str(
                    safety.get("selected_window_relationship")
                    or "unavailable"
                ).replace("_", " ")
                + "**)."
            )
            out.append(
                "Current filesystem evidence is "
                + (
                    "**authoritative for current-health classification** "
                    "because no explicit historical `--until` was supplied."
                    if safety.get("current_health_snapshot_authoritative") is True
                    else "kept separate from the explicit historical cut-off unless reliable in-window evidence connects it."
                )
            )
            protocol = safety.get("protocol") or {}
            control = safety.get("control") or {}
            transport = safety.get("transport") or {}
            media = safety.get("media") or {}
            ledgers = safety.get("retirement_ledgers") or []
            healthy_ledgers = sum(
                row.get("valid") is True and row.get("blocking") is not True
                for row in ledgers
            )
            out.append("```text")
            out.append(
                "protocol_activation      = "
                + ("valid v2" if protocol.get("valid") is True else "INVALID/MISSING")
            )
            out.append(
                f"transport_journal        = {transport.get('classification', 'unavailable')}"
            )
            out.append(
                f"media_upload_receipt     = {media.get('classification', 'unavailable')}"
            )
            out.append(
                f"retirement_ledgers       = {healthy_ledgers} / {len(ledgers)} valid and nonblocking"
            )
            out.append(
                f"source_marker_entries    = {len(safety.get('active_entries') or [])}"
            )
            out.append(
                f"control_generation       = {control.get('generation')}"
            )
            out.append(
                "control_active_keys      = "
                + (", ".join(control.get("active_keys") or []) or "none")
            )
            out.append(
                "ready_for_remote_writes  = "
                + str(safety.get("ready_for_remote_writes") is True).lower()
            )
            out.append(
                f"active_safety_components = {len(safety.get('active_transaction_identities') or [])}"
            )
            out.append("```")
            active_entries = safety.get("active_entries") or []
            if active_entries:
                out.append("Active blockers:")
                out.append(
                    md_table_row(
                        [
                            "name",
                            "kind",
                            "receipt role",
                            "safe regular",
                            "mode",
                            "size",
                            "transaction/attempt",
                            "lane",
                            "target",
                            "selected-window relationship",
                        ]
                    )
                )
                out.append(md_table_row(["---"] * 10))
                for item in active_entries:
                    out.append(
                        md_table_row(
                            [
                                item.get("name", ""),
                                item.get("kind", ""),
                                REMOTE_WRITE_RECEIPT_ROLE_LABELS.get(
                                    str(item.get("receipt_role") or ""),
                                    item.get("receipt_role", ""),
                                ),
                                item.get("safe_regular", ""),
                                item.get("mode", ""),
                                item.get("size", ""),
                                item.get("transaction_id", ""),
                                item.get("lane", ""),
                                item.get("target_id", ""),
                                str(
                                    item.get("selected_window_relationship")
                                    or "unavailable"
                                ).replace("_", " "),
                            ]
                        )
                    )
            active_identities = safety.get("active_transaction_identities") or []
            if active_identities:
                out.append("Active logical remote-write safety components:")
                out.append(
                    md_table_row(
                        [
                            "transaction/attempt",
                            "lane",
                            "target",
                            "receipt roles",
                            "kind / state",
                            "selected-window relationship",
                            "artefacts",
                        ]
                    )
                )
                out.append(md_table_row(["---"] * 7))
                for item in active_identities:
                    out.append(
                        md_table_row(
                            [
                                ", ".join(item.get("transaction_ids") or []),
                                ", ".join(item.get("lanes") or []),
                                ", ".join(item.get("target_ids") or []),
                                ", ".join(
                                    item.get("receipt_role_labels") or []
                                ),
                                ", ".join(item.get("artifact_kinds") or [])
                                + " / " + ", ".join(item.get("transaction_states") or []),
                                str(
                                    item.get("selected_window_relationship")
                                    or "unavailable"
                                ).replace("_", " "),
                                ", ".join(item.get("artifact_names") or []),
                            ]
                        )
                    )
            top_level_blockers = safety.get("snapshot_incident_evidence") or []
            if top_level_blockers:
                out.append("Top-level safety blocker evidence:")
                for item in top_level_blockers:
                    out.append(
                        "- `" + str(item.get("category") or "unavailable")
                        + "`: " + str(item.get("summary") or "unavailable")
                        + " (selected-window relationship: "
                        + str(item.get("selected_window_relationship") or "unavailable").replace("_", " ")
                        + ")"
                    )
            archive = safety.get("reconciliation_archive") or {}
            if archive.get("present") is True and archive.get("valid") is not True:
                out.append(
                    "Warning: the reconciliation archive contains invalid audit "
                    "evidence; it cannot resolve a historical ambiguity."
                )
                for reason in archive.get("invalid_audits") or []:
                    out.append("- Invalid audit: `" + str(reason) + "`")
            marker_audit = archive.get("latest_marker_reconciliation") or {}
            media_audit = archive.get("latest_media_reconciliation") or {}
            if marker_audit or media_audit:
                out.append(
                    "Durable reconciliation evidence: marker audits "
                    f"**{archive.get('valid_marker_reconciliation_count', 0)}**; "
                    "unattached-media audits "
                    f"**{archive.get('valid_media_reconciliation_count', 0)}**."
                )
                if marker_audit:
                    out.append(
                        "- Latest marker audit: `"
                        + str(marker_audit.get("audit_path") or "")
                        + "`"
                    )
                if media_audit:
                    out.append(
                        "- Latest media audit: `"
                        + str(media_audit.get("audit_path") or "")
                        + "`"
                    )
        out.append("")


def _render_remote_transactions(report: Dict[str, Any], out: List[str]) -> None:
    remote_transactions = report.get("remote_write_transactions") or []
    if remote_transactions:
        out.append("## Remote-write transaction lifecycle")
        out.append(
            md_table_row(
                ["time", "kind", "phase", "lane", "transaction/attempt", "detail"]
            )
        )
        out.append(md_table_row(["---", "---", "---", "---", "---", "---"]))
        for item in remote_transactions:
            out.append(
                md_table_row(
                    [
                        item.get("time", ""),
                        item.get("kind", ""),
                        item.get("phase", ""),
                        item.get("lane", ""),
                        item.get("transaction_id") or item.get("attempt_id") or "",
                        item.get("image")
                        or item.get("post_id")
                        or item.get("disposition")
                        or item.get("path")
                        or "",
                    ]
                )
            )
        out.append("")


def _render_media_upload(report: Dict[str, Any], out: List[str]) -> None:
    media_upload = report.get("media_upload") or {}
    media_incidents = media_upload.get("incidents") or []
    if media_incidents:
        out.append("## Media upload incidents")
        out.append("```text")
        out.append(f"handled_fallbacks     = {len(media_upload.get('handled_fallbacks') or [])}")
        out.append(f"reconciled_ambiguities = {len(media_upload.get('reconciled_incidents') or [])}")
        out.append(f"unrecovered_failures  = {len(media_upload.get('unrecovered_failures') or [])}")
        out.append("```")
        out.append(md_table_row(["time", "status", "media", "v1.1 result", "post result", "summary"]))
        out.append(md_table_row(["---", "---", "---", "---", "---", "---"]))
        for item in media_incidents:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("status", ""),
                item.get("media", ""),
                item.get("v1_result", ""),
                item.get("post_result", ""),
                item.get("summary", ""),
            ]))
        out.append("")
        out.append("Details:")
        out.append(md_table_row(["time", "v2 failure", "fallback log"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in media_incidents:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("v2_failure", ""),
                item.get("fallback", ""),
            ]))
        out.append("")


def _render_meme_schedule(report: Dict[str, Any], out: List[str]) -> None:
    st = report.get("latest_state") or {}
    state_presentation = _carried_state_presentation(st, report["summary"])
    stale_state_snapshot = state_presentation["stale"] is True
    state_snapshot_only = state_presentation["snapshot_only"] is True
    state_label_prefix = "snapshot_" if state_snapshot_only else ""
    cfg = report.get("latest_config") or {}
    meme_keys = {
        "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
        "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
        "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
    }
    if cfg or st:
        has_new_meme_config = any(k in cfg for k in meme_keys)
        has_meme_state = any(st.get(k) is not None for k in (
            "next_meme_post_epoch", "next_meme_schedule_mode",
            "meme_anchor_quote_post_epoch", "meme_schedule_version",
        ))
        if has_new_meme_config or has_meme_state:
            out.append("## Daily meme schedule")
            out.append("```text")
            source_bits = _source_bits_for_state_config(st, cfg)
            if stale_state_snapshot:
                source_bits.append(
                    "state is a stale snapshot"
                    + (
                        f" ({state_presentation.get('age')} old at window end)"
                        if state_presentation.get("age")
                        else ""
                    )
                )
            elif state_snapshot_only:
                source_bits.append("state snapshot age unavailable")
            if source_bits:
                out.append(f"source                  = {', '.join(source_bits)}")
            enabled = _cfg_bool(cfg, "ENABLE_DAILY_MEME_POSTS")
            if enabled is not None:
                out.append(f"enabled                 = {enabled}")
            trigger = cfg.get("MEME_TRIGGER_AFTER_HOUR")
            min_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS")
            max_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS")
            fallback_hour = cfg.get("MEME_FALLBACK_HOUR")
            fallback_minute = cfg.get("MEME_FALLBACK_MINUTE")
            if trigger is not None:
                out.append(f"rule                    = first normal quote/image post after {trigger}:00 schedules the daily meme")
            if min_delay is not None or max_delay is not None:
                out.append(f"random_delay_after_rule = {_seconds_to_minutes_text(min_delay)} to {_seconds_to_minutes_text(max_delay)}")
            if fallback_hour is not None or fallback_minute is not None:
                hh = str(fallback_hour) if fallback_hour is not None else "?"
                mm_int = int_or_none(fallback_minute)
                mm = f"{mm_int:02d}" if mm_int is not None else "?"
                out.append(f"fallback_if_no_anchor   = {hh}:{mm}")
            if cfg.get("MEME_MIN_SECONDS_AFTER_QUOTE_POST") is not None:
                out.append(f"min_gap_after_quote     = {_seconds_to_minutes_text(cfg.get('MEME_MIN_SECONDS_AFTER_QUOTE_POST'))}")
            if cfg.get("MEME_SCHEDULE_VERSION") is not None:
                out.append(f"config_schedule_version = {cfg.get('MEME_SCHEDULE_VERSION')}")
            if st.get("next_meme_post_epoch") is not None:
                state_meme_label = (
                    "snapshot_next_meme"
                    if state_snapshot_only
                    else "current_next_meme"
                )
                out.append(f"{state_meme_label:<24} = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
            if st.get("next_meme_schedule_mode") is not None:
                state_mode_label = (
                    "snapshot_mode" if state_snapshot_only else "current_mode"
                )
                out.append(f"{state_mode_label:<24} = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
            if st.get("meme_anchor_quote_post_epoch"):
                state_anchor_label = (
                    "snapshot_anchor" if state_snapshot_only else "current_anchor"
                )
                out.append(f"{state_anchor_label:<24} = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
            else:
                if st.get("next_meme_schedule_mode") and str(st.get("next_meme_schedule_mode")).startswith("fallback"):
                    if state_snapshot_only:
                        out.append(
                            "snapshot_anchor          = none recorded in snapshot; "
                            "snapshot fallback mode retained for diagnosis"
                        )
                    else:
                        out.append("current_anchor          = none yet; fallback remains until first qualifying post/image after midday")
            out.append("```")
            out.append("")


def _render_reply_budget_and_priority(report: Dict[str, Any], out: List[str]) -> None:
    st = report.get("latest_state") or {}
    state_presentation = _carried_state_presentation(st, report["summary"])
    stale_state_snapshot = state_presentation["stale"] is True
    state_snapshot_only = state_presentation["snapshot_only"] is True
    state_label_prefix = "snapshot_" if state_snapshot_only else ""
    derived = report.get("derived") or {}
    budget = derived.get("reply_budget") or {}
    if budget:
        out.append("## Reply budget")
        out.append("```text")
        au, al, ar = budget.get("auto_used"), budget.get("auto_limit"), budget.get("auto_remaining")
        per_author_limit = budget.get("per_author_limit")
        qu, ql, qr = budget.get("quote_used"), budget.get("quote_limit"), budget.get("quote_remaining")
        source_bits = []
        if budget.get("state_carried_forward"):
            source_bits.append("state carried forward")
            if stale_state_snapshot:
                source_bits.append("state counters are stale snapshot values")
            elif state_snapshot_only:
                source_bits.append("state counter age unavailable")
        if budget.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if budget.get("config_carried_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if budget.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if budget.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if budget.get("config_filled_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if budget.get("config_is_on_disk_override"):
            source_bits.append(
                "limits from on-disk local overrides; live effectiveness unverified"
            )
        if source_bits:
            out.append(f"source             = {', '.join(source_bits)}")
        if not budget.get("has_any_budget_input"):
            out.append("not available      = no state/config snapshot in this window or saved resume context")
        elif state_snapshot_only:
            if au is not None or al is not None:
                out.append(
                    f"snapshot auto replies used  = {au if au is not None else '?'} / "
                    f"{al if al is not None else '?'}  snapshot_remaining="
                    f"{ar if ar is not None else '?'}"
                )
            if qu is not None or ql is not None:
                out.append(
                    f"snapshot quote replies used = {qu if qu is not None else '?'} / "
                    f"{ql if ql is not None else '?'}  snapshot_remaining="
                    f"{qr if qr is not None else '?'}"
                )
        else:
            if au is not None or al is not None:
                out.append(f"auto replies used  = {au if au is not None else '?'} / {al if al is not None else '?'}  remaining={ar if ar is not None else '?'}")
            if per_author_limit is not None:
                out.append(f"per-author reply cap = {per_author_limit}")
            if qu is not None or ql is not None:
                out.append(f"quote replies used = {qu if qu is not None else '?'} / {ql if ql is not None else '?'}  remaining={qr if qr is not None else '?'}")
        out.append("```")
        out.append("")

    lane = derived.get("reply_lane_priority") or {}
    if lane:
        out.append("## Reply lane priority")
        out.append("```text")
        source_bits = []
        if lane.get("state_carried_forward"):
            source_bits.append("state carried forward")
            if stale_state_snapshot:
                source_bits.append("state priority is a stale snapshot value")
            elif state_snapshot_only:
                source_bits.append("state priority age unavailable")
        if lane.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if lane.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if lane.get("config_carried_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if lane.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if lane.get("config_filled_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if source_bits:
            out.append(f"source                         = {', '.join(source_bits)}")
        priority = lane.get("current_next_priority")
        priority_label = (
            "snapshot_next_priority" if state_snapshot_only else "current_next_priority"
        )
        out.append(f"{priority_label:<31} = {priority if priority is not None else 'not available'}")
        out.append(f"normal_lane_due_checks         = {lane.get('normal_lane_due_checks')}")
        out.append(f"mention_function_entries       = {lane.get('mention_function_entries')}")
        out.append(f"mention_fetch_attempts         = {lane.get('mention_fetch_attempts')}")
        out.append(f"mention_checks_skipped_spacing = {lane.get('mention_checks_skipped_spacing')}")
        out.append(f"mention_checks_skipped_cooldown = {lane.get('mention_checks_skipped_cooldown')}")
        out.append(f"quote_lane_due_checks          = {lane.get('quote_lane_due_checks')}")
        out.append(f"priority_flipped_to_quote      = {lane.get('flipped_to_quote')}")
        out.append(f"priority_flipped_to_normal     = {lane.get('flipped_to_normal')}")
        out.append(f"forced_normal_before_quote     = {lane.get('forced_normal_before_quote')}")
        out.append(f"normal_first_refusal_no_post   = {lane.get('normal_first_refusal_no_post')}")
        out.append(f"quote_tweet_status_posted      = {lane.get('quote_tweet_status_posted')}")
        out.append(f"quote_tweet_status_checked     = {lane.get('quote_tweet_status_checked')}")
        out.append(f"quote_tweet_status_spacing     = {lane.get('quote_tweet_status_skipped_spacing')}")
        out.append(f"quote_tweet_status_cap         = {lane.get('quote_tweet_status_skipped_cap')}")
        out.append(f"quote_tweet_status_cooldown    = {lane.get('quote_tweet_status_skipped_cooldown')}")
        out.append(f"quote_tweet_checks_no_post     = {lane.get('quote_tweet_checks_no_post')}")
        out.append("```")
        out.append("")


def _render_openai_cost(report: Dict[str, Any], out: List[str]) -> None:
    openai_cost = report.get("openai_published_cost") or {
        "available": False,
        "reason": "cache was not inspected",
    }
    out.append("## OpenAI published-cost cache")
    if not openai_cost.get("available"):
        out.append("OpenAI published cost: unknown")
        out.append(
            f"Cache status: **unavailable** ({openai_cost.get('reason') or 'unknown reason'})."
        )
    else:
        openai_scope = openai_cost.get("scope") or {}
        if openai_scope.get("kind") == "project":
            scope_text = f"project {openai_scope.get('project_id', 'unavailable')}"
        else:
            scope_text = "organisation-wide (not bot-exclusive)"
        out.append(f"Cache scope: **{scope_text}**.")
        out.append(
            f"Cache updated: **{openai_cost.get('updated_at_display')}** "
            f"(age: {openai_cost.get('age')})."
        )
        current_day = openai_cost.get("current_day") or {}
        if current_day.get("available"):
            out.append(
                "Current UTC-day provider-published total: "
                f"**{format_openai_usd(current_day.get('primary_total'))}** "
                f"for `{current_day.get('utc_date')}`."
            )
            out.append("Status: **provisional**.")
        else:
            out.append("Current UTC-day provider-published total: **unknown**.")

        selected = openai_cost.get("selected_window") or {}
        selected_status = selected.get("status")
        organization_prefix = (
            "OpenAI organisation-wide "
            if openai_scope.get("kind") == "organization"
            else "OpenAI "
        )
        if selected_status == "complete":
            out.append(
                f"{organization_prefix}selected-window estimate: "
                f"**{format_openai_usd(selected.get('amount'))}**."
            )
        elif selected_status == "partial":
            out.append(
                f"{organization_prefix}selected-window estimate (partial coverage): "
                f"**{format_openai_usd(selected.get('amount'))}**."
            )
        else:
            out.append(f"{organization_prefix}selected-window estimate: **unknown**.")
        if selected.get("method"):
            out.append(f"Method: {selected.get('method')}.")
        if selected.get("requested_window"):
            out.append(f"Requested window: **{selected.get('requested_window')}**.")
        valid_segments = [
            item
            for item in selected.get("segments", [])
            if item.get("status") in {"complete", "partial"}
        ]
        if valid_segments:
            coverage_parts = []
            requested_coverage_parts = []
            outside_requested = False
            for item in valid_segments:
                sample_start = _parse_openai_utc(
                    item.get("sample_start_utc"), label="sample coverage start"
                )
                sample_end = _parse_openai_utc(
                    item.get("sample_end_utc"), label="sample coverage end"
                )
                requested_start = _parse_openai_utc(
                    item.get("requested_start_utc"), label="requested segment start"
                )
                requested_end = _parse_openai_utc(
                    item.get("requested_end_utc"), label="requested segment end"
                )
                outside_requested = outside_requested or (
                    sample_start < requested_start or sample_end > requested_end
                )
                coverage_parts.append(
                    f"{item.get('utc_date')}: {_openai_window_text(sample_start, sample_end)}"
                )
                represented_start = max(sample_start, requested_start)
                represented_end = min(sample_end, requested_end)
                if represented_end > represented_start:
                    requested_coverage_parts.append(
                        f"{item.get('utc_date')}: "
                        f"{_openai_window_text(represented_start, represented_end)}"
                    )
            out.append("Sample coverage: **" + "; ".join(coverage_parts) + "**.")
            if selected_status == "partial" and requested_coverage_parts:
                out.append(
                    "Requested coverage represented: approximately **"
                    + "; ".join(requested_coverage_parts)
                    + "**."
                )
            trailing_segments = [
                item
                for item in valid_segments
                if item.get("status") == "partial"
                and item.get("trailing_uncovered_seconds") is not None
            ]
            if len(trailing_segments) == 1:
                out.append(
                    "Trailing period unavailable: **"
                    + _human_snapshot_age(
                        float(trailing_segments[0]["trailing_uncovered_seconds"])
                    )
                    + "**."
                )
            if outside_requested:
                out.append(
                    "The estimate can include a small amount immediately outside the "
                    "requested log window because samples are collected at intervals."
                )
        unavailable_segments = [
            item
            for item in selected.get("segments", [])
            if item.get("status") == "unavailable"
        ]
        if unavailable_segments:
            out.append(
                "Unavailable UTC segment(s): "
                + "; ".join(
                    f"{item.get('utc_date')}: {item.get('reason') or 'unknown reason'}"
                    for item in unavailable_segments
                )
                + "."
            )
        elif selected.get("reason"):
            out.append(f"Estimate status: {selected.get('reason')}.")

    out.append(
        "The provider-published estimate is not allocated to individual "
        "single-call decisions."
    )
    out.append("")


def _render_generated_images(report: Dict[str, Any], out: List[str]) -> None:
    generated_spacing = report.get("generated_image_spacing") or {}
    generated_spacing_latest = generated_spacing.get("latest") or {}
    generated_spacing_events = generated_spacing.get("events") or []
    generated_pool_enabled = generated_spacing_latest.get("pool_enabled")
    if isinstance(generated_pool_enabled, str):
        generated_pool_enabled = generated_pool_enabled.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    generated_pool_allowed = generated_spacing_latest.get("allowed")
    if isinstance(generated_pool_allowed, str):
        generated_pool_allowed = generated_pool_allowed.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    pool_health = report.get("generated_image_pool_health") or {}
    runtime_retired = pool_health.get("status") == "retired"
    if pool_health and not runtime_retired:
        out.append("## Generated image pool health")
        out.append("Current filesystem snapshot at digest generation time; these counts are not limited to the selected log window.")
        if generated_pool_enabled is False:
            disabled_explanation = (
                "**The generated-image pool is intentionally disabled.** "
                "Pool inventory and historical usage are reported for observability only."
            )
            if generated_pool_allowed is True:
                disabled_explanation += (
                    " `allowed=true` means a spacing rule would permit selection, "
                    "not that the pool is enabled."
                )
            else:
                disabled_explanation += (
                    " The `allowed` flag reports spacing eligibility separately "
                    "from the enablement setting."
                )
            out.append(disabled_explanation)
        out.append("")
        out.append("```text")
        out.append(f"active_generated_images       = {pool_health.get('active_generated_images', 'unavailable')}")
        out.append(f"quarantined_generated_images  = {pool_health.get('quarantined_generated_images', 'unavailable')}")
        out.append(f"total_known_generated_images  = {pool_health.get('total_known_generated_images', 'unavailable')}")
        out.append(f"active_analysis_records       = {pool_health.get('active_analysis_records', 'unavailable')}")
        out.append(f"active_identity_records       = {pool_health.get('active_identity_records', 'unavailable')}")
        out.append(f"metadata_coverage             = {pool_health.get('metadata_coverage', 'unavailable')}")
        out.append(f"hash_validation               = {pool_health.get('hash_valid', 0)} / {pool_health.get('hash_total', 0)} valid ({pool_health.get('hash_validation', 'unavailable')})")
        out.append(f"generated_images_in_used_history = {pool_health.get('generated_images_in_used_history', 0)}")
        out.append(f"health                        = {pool_health.get('health', 'WARNING')}")
        out.append("```")
        out.append("Active policy counts:")
        out.append("```text")
        for policy in GENERATED_POLICIES:
            out.append(f"{policy:<18} = {(pool_health.get('active_policy_counts') or {}).get(policy, 0)}")
        out.append("```")
        out.append("Usage history:")
        out.append("```text")
        out.append(f"active_used_in_current_cycle       = {pool_health.get('active_previously_used', 0)}")
        out.append(f"active_unused_in_current_cycle     = {pool_health.get('active_never_used', 0)}")
        out.append(f"quarantined_used_in_current_cycle  = {pool_health.get('quarantined_previously_used', 0)}")
        out.append(f"quarantined_unused_in_current_cycle = {pool_health.get('quarantined_never_used', 0)}")
        out.append("```")
        rates = report.get("generated_image_post_rates") or {}
        if rates:
            out.append("Recent successful regular-post rate (bounded historical log scan):")
            out.append("```text")
            for label in ("trailing_7d", "trailing_30d"):
                window = (rates.get("windows") or {}).get(label) or {}
                share = window.get("generated_share_percent")
                out.append(f"{label}_coverage_days          = {float(window.get('coverage_days') or 0.0):.1f}")
                out.append(f"{label}_observed_logging_days  = {float(window.get('observed_logging_days') or 0.0):.1f}")
                out.append(f"{label}_coverage_quality       = {window.get('coverage_quality') or 'unavailable'}")
                out.append(f"{label}_largest_detected_gap   = {float(window.get('largest_detected_gap_seconds') or 0.0) / 3600.0:.1f} hours")
                out.append(f"{label}_regular_posts          = {window.get('regular_posts', 0)}")
                out.append(f"{label}_generated_posts        = {window.get('generated_posts', 0)}")
                out.append(f"{label}_generated_share        = {float(share):.1f}%" if share is not None else f"{label}_generated_share        = unavailable")
                regular_rate = window.get("regular_posts_per_day"); generated_rate = window.get("generated_posts_per_day")
                out.append(f"{label}_regular_posts_per_day  = {float(regular_rate):.2f}" if regular_rate is not None else f"{label}_regular_posts_per_day  = unavailable")
                out.append(f"{label}_generated_posts_per_day = {float(generated_rate):.2f}" if generated_rate is not None else f"{label}_generated_posts_per_day = unavailable")
            out.append(f"contaminated_seconds_excluded  = {rates.get('contaminated_seconds_excluded', 0)}")
            out.append("```")
            if any(((rates.get("windows") or {}).get(label) or {}).get("coverage_quality") == "gapped" for label in ("trailing_7d", "trailing_30d")):
                out.append("WARNING: material gaps were detected in available logs; posts/day and runway estimates are not treated as reliable.")
        runway = report.get("generated_image_pool_runway") or {}
        if runway:
            out.append("Estimated current-cycle runway (not an all-time posting claim):")
            out.append("```text")
            out.append(f"active_generated_unused_in_current_cycle = {runway.get('remaining_active_generated_in_current_cycle', 0)}")
            out.append(f"primary_basis                           = {runway.get('primary_basis') or 'unavailable'}")
            primary = (runway.get("observed") or {}).get(str(runway.get("primary_basis"))) or {}
            if primary.get("available"):
                out.append(f"estimated_regular_posts_to_cycle_exhaustion = {primary.get('regular_posts_to_cycle_exhaustion')}")
                out.append(f"estimated_days_to_cycle_exhaustion     = {float(primary.get('days_to_cycle_exhaustion') or 0.0):.1f}")
            else:
                out.append("estimated_regular_posts_to_cycle_exhaustion = unavailable")
                out.append(f"estimated_days_to_cycle_exhaustion     = unavailable ({primary.get('reason') or 'no reliable observed basis'})")
            schedule = runway.get("schedule") or {}
            if schedule.get("available"):
                out.append(f"schedule_model_generated_share_max     = {float(schedule.get('maximum_generated_share_percent') or 0.0):.1f}%")
                out.append(f"schedule_model_generated_posts_per_day = {float(schedule.get('generated_posts_per_day') or 0.0):.2f}")
                out.append(
                    "schedule_model_days_to_cycle_exhaustion = "
                    f"{float(schedule.get('days_to_cycle_exhaustion') or 0.0):.1f} "
                    "(maximum-throughput minimum; assumes generated selection whenever spacing permits)"
                )
            else:
                out.append(f"schedule_model_days_to_cycle_exhaustion = unavailable ({schedule.get('reason') or 'pool disabled'})")
            out.append("```")
        out.append("Curation trend (completed transaction image actions):")
        out.append("```text")
        for days in (7, 30):
            trend = pool_health.get(f"curation_{days}d") or {}
            out.append(f"quarantined_last_{days}d = {trend.get('quarantined', 0)}")
            out.append(f"restored_last_{days}d    = {trend.get('restored', 0)}")
            out.append(f"net_active_change_last_{days}d = {trend.get('net_active_change', 0):+d}")
        out.append("```")
        latest_quarantine = pool_health.get("latest_quarantine")
        if latest_quarantine:
            out.append("Latest completed quarantine:")
            out.append("```text")
            out.append(f"transaction_id = {latest_quarantine.get('transaction_id', '')}")
            out.append(f"timestamp      = {latest_quarantine.get('timestamp', '')}")
            out.append(f"image_count    = {latest_quarantine.get('image_count', 0)}")
            out.append("```")
        else:
            out.append("Latest completed quarantine: none")
            out.append("")
        out.append(f"completed_quarantine_transactions = {pool_health.get('completed_quarantine_transactions', 0)}")
        out.append(f"completed_restore_transactions    = {pool_health.get('completed_restore_transactions', 0)}")
        if pool_health.get("latest_restore"):
            restore = pool_health["latest_restore"]
            out.append(f"latest_restore = {restore.get('transaction_id')} at {restore.get('timestamp')} ({restore.get('image_count', 0)} images)")
        out.append("")
        warnings = pool_health.get("warnings") or []
        if warnings:
            out.append("Pool-health warnings:")
            out.append(md_table_row(["kind", "basename", "detail"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in warnings[:20]:
                out.append(md_table_row([item.get("kind", ""), item.get("basename", ""), item.get("detail", "")]))
            if len(warnings) > 20:
                out.append(f"{len(warnings) - 20} additional warning(s) omitted.")
            out.append("")

    utilisation = report.get("generated_image_utilisation") or {}
    if utilisation and not runtime_retired:
        out.append("## Generated image utilisation")
        out.append(
            "Current-cycle history records whether an image is marked used in the live image cycle. "
            "Bounded structured-log observations count successful post records retained in the scanned logs. "
            "The two measures answer different questions and are not interchangeable."
        )
        out.append(
            "The active-image inventory is a current filesystem snapshot. The structured-log "
            "coverage shown below is a secondary bounded scan and can extend slightly beyond "
            "the selected digest event window."
        )
        out.append("Deprecated machine-readable usage aliases retain the bounded-log values for compatibility and are planned for removal only in a future major digest schema version.")
        coverage_start = utilisation.get("history_coverage_start") or "unavailable"
        coverage_end = utilisation.get("history_coverage_end") or "unavailable"
        out.append(f"Observed structured-log coverage: `{coverage_start}` to `{coverage_end}`.")
        out.append("")
        out.append("```text")
        out.append(f"active_generated_images                    = {utilisation.get('active_generated_images', 0)}")
        out.append(f"active_images_used_in_observed_logs        = {utilisation.get('active_images_used_in_observed_logs', utilisation.get('active_images_used_ever', 0))}")
        out.append(f"active_images_not_seen_in_observed_logs    = {utilisation.get('active_images_not_seen_in_observed_logs', utilisation.get('active_images_never_used', 0))}")
        percentage = utilisation.get("active_pool_observed_usage_percentage", utilisation.get("active_pool_ever_used_percentage"))
        out.append(f"active_pool_observed_usage_percentage      = {float(percentage):.1f}%" if percentage is not None else "active_pool_observed_usage_percentage      = unavailable")
        out.append(f"active_images_used_in_current_cycle        = {utilisation.get('active_images_used_in_current_cycle', 0)}")
        out.append(f"active_images_unused_in_current_cycle      = {utilisation.get('active_images_unused_in_current_cycle', 0)}")
        out.append(f"total_successful_generated_posts_observed  = {utilisation.get('total_successful_generated_posts_observed', 0)}")
        median_count = utilisation.get("median_successful_posts_per_used_image")
        out.append(f"median_successful_posts_per_used_image     = {float(median_count):.1f}" if median_count is not None else "median_successful_posts_per_used_image     = unavailable")
        out.append(f"maximum_successful_posts_for_one_image     = {utilisation.get('maximum_successful_posts_for_one_image', 0)}")
        top_share = utilisation.get("top_10_share_of_successful_generated_posts")
        out.append(f"top_10_share_of_successful_generated_posts = {float(top_share):.1f}%" if top_share is not None else "top_10_share_of_successful_generated_posts = unavailable")
        out.append("```")

        out.append("Most frequently used active generated images")
        out.append(md_table_row(["image", "successful_posts", "last_successful_post"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in utilisation.get("most_frequently_used") or []:
            out.append(md_table_row([item.get("image", ""), item.get("successful_posts", 0), item.get("last_successful_post") or "never"]))
        if not utilisation.get("most_frequently_used"): out.append(md_table_row(["none observed", "0", "never"]))
        out.append("")

        filename_sample_limit = 5
        never_used_rows = list(utilisation.get("never_used") or [])
        unused_longest_rows = list(utilisation.get("unused_longest") or [])
        never_used_total = int(utilisation.get("never_used_total", 0) or 0)
        never_used_sample = never_used_rows[:filename_sample_limit]
        unused_longest_sample = unused_longest_rows[:filename_sample_limit]
        unused_longest_total = int(
            utilisation.get(
                "unused_longest_total",
                utilisation.get("active_generated_images", len(unused_longest_rows)),
            )
            or 0
        )

        same_unused_population = (
            never_used_total == unused_longest_total
            and [item.get("image") for item in never_used_rows]
            == [item.get("image") for item in unused_longest_rows]
            and all(not item.get("last_successful_post") for item in unused_longest_rows)
        )
        out.append(
            "Active generated images never successfully posted in observed logs"
            + (
                " (the same population is therefore also unused longest)"
                if same_unused_population else ""
            )
            + f" (count: **{never_used_total}**; sample: **{len(never_used_sample)}**)"
        )
        out.append(md_table_row(["image", "origin_quote_hash"]))
        out.append(md_table_row(["---", "---"]))
        for item in never_used_sample:
            out.append(md_table_row([item.get("image", ""), item.get("origin_quote_hash") or "unavailable"]))
        omitted = max(0, never_used_total - len(never_used_sample))
        if omitted > 0:
            out.append(
                f"{plural_count(omitted, 'additional active image')} omitted from the readable summary."
            )
        if not never_used_sample:
            out.append(md_table_row(["none", "-"]))
        out.append("")

        if not same_unused_population:
            out.append(
                "Active generated images unused longest in observed logs "
                f"(count: **{unused_longest_total}**; sample: **{len(unused_longest_sample)}**)"
            )
            out.append(md_table_row(["image", "last_successful_post", "successful_posts"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in unused_longest_sample:
                out.append(md_table_row([item.get("image", ""), item.get("last_successful_post") or "never", item.get("successful_posts", 0)]))
            if unused_longest_total > len(unused_longest_sample):
                out.append(
                    f"{plural_count(unused_longest_total - len(unused_longest_sample), 'additional row')} "
                    "omitted from the readable summary."
                )
            if not unused_longest_sample:
                out.append(md_table_row(["none", "never", "0"]))
            out.append("")

        if report.get("detailed_appendix") and (never_used_rows or unused_longest_rows):
            out.append("### Detailed generated-image filename appendix")
            out.append(
                "This optional appendix contains every filename retained in the digest's "
                "already-bounded utilisation result."
            )
            if never_used_rows:
                out.append("Never observed:")
                for item in never_used_rows:
                    out.append(f"- `{item.get('image', '')}`")
            if unused_longest_rows and not same_unused_population:
                out.append("Unused longest:")
                for item in unused_longest_rows:
                    out.append(
                        f"- `{item.get('image', '')}` — "
                        f"{item.get('last_successful_post') or 'never'}"
                    )
            out.append("")

    if generated_spacing_latest or generated_spacing_events:
        out.append("## Historical generated image spacing" if runtime_retired else "## Generated image spacing")
        if runtime_retired:
            out.append("Generated-image selection has been removed from the bot runtime. These are retained historical observations.")
        if generated_spacing_latest:
            if generated_pool_enabled is False:
                out.append(
                    "The pool is intentionally disabled; spacing eligibility is informational "
                    "and does not activate generated-image selection."
                )
            out.append("```text")
            out.append(f"required_original_posts_between = {generated_spacing_latest.get('required', '')}")
            out.append(f"original_posts_since_generated  = {generated_spacing_latest.get('original_posts_since_generated', '')}")
            out.append(f"generated_pool_enabled          = {generated_spacing_latest.get('pool_enabled', '')}")
            out.append(f"generated_pool_allowed          = {generated_spacing_latest.get('allowed', '')}")
            out.append("```")
        spacing_state_fields = (
            "pool_enabled", "allowed", "original_posts_since_generated", "required"
        )
        invariant_spacing = bool(generated_spacing_events) and len({
            tuple(str(item.get(field, "")) for field in spacing_state_fields)
            for item in generated_spacing_events
        }) == 1
        if invariant_spacing:
            out.append(
                f"All **{len(generated_spacing_events)}** spacing observations had the same "
                f"state; first `{generated_spacing_events[0].get('time', '')}`, "
                f"last `{generated_spacing_events[-1].get('time', '')}`."
            )
        else:
            out.append(md_table_row(["time", "kind", "pool_enabled", "allowed", "original_posts_since_generated", "required"]))
            out.append(md_table_row(["---"] * 6))
            for item in generated_spacing_events[-20:]:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("kind", ""),
                    item.get("pool_enabled", ""),
                    item.get("allowed", ""),
                    item.get("original_posts_since_generated", ""),
                    item.get("required", ""),
                ]))
        out.append("")


def _render_regular_image_usage(report: Dict[str, Any], out: List[str]) -> None:
    regular_image_usage = report.get("regular_image_usage") or {}
    regular_image_events = regular_image_usage.get("events") or []
    if regular_image_events:
        summary = regular_image_usage.get("summary") or {}
        out.append("## Regular image usage")
        out.append("```text")
        out.append(f"selections          = {summary.get('selections', 0)}")
        out.append(f"original_images     = {summary.get('original', 0)}")
        out.append(f"generated_images    = {summary.get('generated', 0)}")
        out.append(f"originating_quote   = {summary.get('origin_matches', 0)}")
        out.append(f"cross_quote         = {summary.get('cross_quote', 0)}")
        out.append(f"made_with_ai_true   = {summary.get('made_with_ai_true', 0)}")
        out.append(f"made_with_ai_false  = {summary.get('made_with_ai_false', 0)}")
        out.append(f"made_with_ai_unknown = {summary.get('made_with_ai_unknown', 0)}")
        out.append(f"generated_share     = {float(summary.get('generated_share', 0.0)):.1f}%")
        out.append(f"origin_match_share  = {float(summary.get('origin_match_share', 0.0)):.1f}%")
        out.append("```")
        out.append(md_table_row(["time", "source", "basename", "score", "origin_quote_match", "origin_quote_boost", "made_with_ai"]))
        out.append(md_table_row(["---"] * 7))
        for item in regular_image_events:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("source", ""),
                item.get("basename", ""),
                format_display_number(item.get("score", "")),
                item.get("origin_quote_match", ""),
                format_display_number(item.get("origin_quote_boost", "")),
                item.get("made_with_ai", ""),
            ]))
        out.append("")


def _render_original_editorial(report: Dict[str, Any], out: List[str]) -> None:
    shadow = report.get("original_editorial_shadow") or {}
    shadow_events = shadow.get("events") or []
    shadow_summary = shadow.get("summary") or {}
    if shadow_events:
        active_selection_observations = int(shadow_summary.get("active_selection_observations", 0) or 0)
        legacy_shadow_observations = int(shadow_summary.get("legacy_shadow_observations", 0) or 0)
        if active_selection_observations:
            out.append("## Original editorial image selection")
            out.append("Active selection events report the pre-editorial baseline and the image actually selected by the editorial scorer.")
            if legacy_shadow_observations:
                out.append("Unmatched legacy shadow events remain hypothetical and are labelled shadow-only below.")
        else:
            out.append("## Original editorial shadow scoring")
            out.append("This section is shadow-only. It reports hypothetical original-image choices and does not imply the shadow image was posted.")
        out.append("")
        out.append("```text")
        avg_rank = shadow_summary.get("average_production_winner_shadow_rank")
        if active_selection_observations:
            out.append(f"editorial_observations                 = {shadow_summary.get('observations', 0)}")
            out.append(f"active_selection_observations          = {active_selection_observations}")
            out.append(f"legacy_shadow_observations             = {legacy_shadow_observations}")
            out.append(f"selector_applied                       = {shadow_summary.get('selector_applied_observations', 0)}")
            out.append(f"selector_not_applied                   = {shadow_summary.get('selector_not_applied_observations', 0)}")
            out.append(f"selected_winner_changes                = {shadow_summary.get('selected_winner_changes', 0)}")
            out.append(f"baseline_original_winners              = {shadow_summary.get('production_original', 0)}")
            out.append(f"baseline_generated_winners             = {shadow_summary.get('production_generated', 0)}")
            out.append(f"comparable_original_observations       = {shadow_summary.get('comparable_original_observations', 0)}")
            out.append(f"editorial_winner_differences           = {shadow_summary.get('winner_changes', 0)} ({float(shadow_summary.get('winner_change_percent', 0.0)):.1f}%)")
            out.append(f"mean_baseline_winner_editorial_rank    = {format_rank(avg_rank, mean=True)}")
            out.append(f"median_baseline_winner_editorial_rank  = {format_rank(shadow_summary.get('median_production_winner_shadow_rank'))}")
            out.append(f"worst_baseline_winner_editorial_rank   = {format_rank(shadow_summary.get('worst_production_winner_shadow_rank'))}")
            out.append(f"baseline_winner_editorial_rank_1       = {shadow_summary.get('production_rank_1', 0)}")
            out.append(f"baseline_winner_editorial_rank_2_or_3  = {shadow_summary.get('production_rank_2_or_3', 0)}")
            out.append(f"baseline_winner_editorial_rank_10_plus = {shadow_summary.get('production_rank_10_or_worse', 0)}")
        else:
            out.append(f"shadow_observations                  = {shadow_summary.get('observations', 0)}")
            out.append(f"production_original_winners          = {shadow_summary.get('production_original', 0)}")
            out.append(f"production_generated_winners         = {shadow_summary.get('production_generated', 0)}")
            out.append(f"comparable_original_observations     = {shadow_summary.get('comparable_original_observations', 0)}")
            out.append(f"original_winner_changes              = {shadow_summary.get('winner_changes', 0)} ({float(shadow_summary.get('winner_change_percent', 0.0)):.1f}%)")
            out.append(
                "mean_production_winner_shadow_rank    = "
                f"{format_rank(avg_rank, mean=True)}"
            )
            out.append(
                "median_production_winner_shadow_rank  = "
                f"{format_rank(shadow_summary.get('median_production_winner_shadow_rank'))}"
            )
            out.append(
                "worst_production_winner_shadow_rank   = "
                f"{format_rank(shadow_summary.get('worst_production_winner_shadow_rank'))}"
            )
            out.append(f"production_winner_shadow_rank_1      = {shadow_summary.get('production_rank_1', 0)}")
            out.append(f"production_winner_shadow_rank_2_or_3 = {shadow_summary.get('production_rank_2_or_3', 0)}")
            out.append(f"production_winner_shadow_rank_10_plus = {shadow_summary.get('production_rank_10_or_worse', 0)}")
        out.append(f"average_abs_editorial_adjustment     = {float(shadow_summary.get('average_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"max_abs_editorial_adjustment         = {float(shadow_summary.get('max_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"cap_hit_count                        = {shadow_summary.get('cap_hit_count', 0)}")
        out.append("```")
        severe = shadow_summary.get("severe_disagreements") or []
        if severe:
            winner_term = "baseline" if active_selection_observations else "production"
            editorial_term = "editorial" if active_selection_observations else "shadow"
            out.append(f"Severe disagreements ({winner_term} winner ranked 10 or worse):")
            out.append(md_table_row(["time", winner_term, editorial_term, f"{winner_term} rank"]))
            out.append(md_table_row(["---"] * 4))
            for item in severe:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("production_winner", ""),
                    item.get("shadow_original_winner", ""),
                    item.get("production_shadow_rank", ""),
                ]))
            out.append("")
        else:
            winner_term = "baseline" if active_selection_observations else "production"
            out.append(f"Severe disagreements ({winner_term} winner ranked 10 or worse): **0**.")
            out.append("")
        if shadow_summary.get("most_frequent_shadow_winners"):
            out.append("Most frequent editorial winners:" if active_selection_observations else "Most frequent shadow winners:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_shadow_winners", []) if name))
            out.append("")
        if shadow_summary.get("most_frequent_affinity_concepts"):
            out.append("Most frequent affinity concepts:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_affinity_concepts", []) if name))
            out.append("")
        if shadow_summary.get("most_frequent_active_dimensions"):
            out.append("Most frequent positive editorial-winner dimensions:" if active_selection_observations else "Most frequent positive shadow-winner dimensions:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_active_dimensions", []) if name))
            out.append("")
        changed_shadow = [
            item
            for item in shadow_events
            if item.get("production_source") == "original" and item.get("winner_changed") is True
        ]
        if changed_shadow:
            out.append("Changed-winner observations:")
            if active_selection_observations:
                out.append(md_table_row(["time", "mode", "line_no", "baseline", "editorial winner", "selected", "baseline rank", "adjustment", "reason"]))
                out.append(md_table_row(["---"] * 9))
            else:
                out.append(md_table_row(["time", "line_no", "production", "shadow", "production rank", "adjustment", "reason"]))
                out.append(md_table_row(["---"] * 7))
            for item in changed_shadow[:20]:
                reason_bits = []
                if item.get("affinity_matches"):
                    reason_bits.append("affinity=" + ",".join(str(v) for v in item.get("affinity_matches", [])[:4]))
                if item.get("dimension_matches"):
                    reason_bits.append("dimensions=" + ",".join(str(v) for v in item.get("dimension_matches", [])[:4]))
                if item.get("penalties"):
                    reason_bits.append("penalties=" + ",".join(str(v) for v in item.get("penalties", [])[:3]))
                if active_selection_observations:
                    is_selection = item.get("event_mode") == "selection"
                    out.append(md_table_row([
                        item.get("time", ""),
                        "active" if is_selection else "shadow-only",
                        item.get("line_no", ""),
                        f"{item.get('production_winner', '')} ({item.get('production_source', '')})",
                        item.get("shadow_original_winner", ""),
                        item.get("selected_winner", "") if is_selection else "hypothetical",
                        item.get("production_shadow_rank", ""),
                        item.get("shadow_winner_editorial_adjustment", ""),
                        "; ".join(reason_bits),
                    ]))
                else:
                    out.append(md_table_row([
                        item.get("time", ""),
                        item.get("line_no", ""),
                        f"{item.get('production_winner', '')} ({item.get('production_source', '')})",
                        item.get("shadow_original_winner", ""),
                        item.get("production_shadow_rank", ""),
                        item.get("shadow_winner_editorial_adjustment", ""),
                        "; ".join(reason_bits),
                    ]))
            out.append("")
        else:
            out.append("No changed-winner observations in this window.")
            out.append("")


def _render_identity_policy(report: Dict[str, Any], out: List[str]) -> None:
    identity_policy = report.get("generated_identity_policy") or {}
    identity_policy_events = identity_policy.get("events") or []
    identity_policy_summary = identity_policy.get("summary") or {}
    if identity_policy_events:
        out.append("## Generated identity policy")
        if (report.get("generated_image_pool_health") or {}).get("status") == "retired":
            out.append("These are historical observations of the retired generated-image identity policy. Counterfactual baseline images were not posted.")
        else:
            out.append("This policy is active in real production. New events compare policy-disabled and policy-enabled selection with the same candidates, scores and saved random state; the counterfactual baseline image was not posted.")
        out.append("")
        out.append("The winner-change percentage denominator is counterfactually validated selections where the policy affected a score or eligibility. Older events without that comparison are reported separately and are not attributed causally.")
        out.append("")
        out.append("```text")
        out.append(f"regular_selections_under_policy        = {identity_policy_summary.get('observations', 0)}")
        out.append(f"policy_relevant_selections             = {identity_policy_summary.get('policy_relevant_observations', 0)}")
        out.append(f"identity_policy_winner_changed         = {identity_policy_summary.get('identity_policy_winner_changes', identity_policy_summary.get('winner_changes', 0))} ({float(identity_policy_summary.get('winner_change_percent', 0.0)):.1f}%)")
        out.append(f"policy_effect_without_winner_change   = {identity_policy_summary.get('policy_effect_without_winner_change', 0)}")
        out.append(f"legacy_policy_causation_unverified    = {identity_policy_summary.get('legacy_policy_causation_unverified', 0)}")
        out.append(f"counterfactual_invariant_failures     = {identity_policy_summary.get('counterfactual_invariant_failures', 0)}")
        out.append(f"policy_neutral_baseline_differences    = {identity_policy_summary.get('policy_neutral_baseline_differences', 0)}")
        out.append(f"policy_neutral_equal_score_ties        = {identity_policy_summary.get('policy_neutral_equal_score_tie_resolutions', 0)}")
        out.append(f"origin_only_baseline_winners_prevented = {identity_policy_summary.get('baseline_origin_only_prevented', 0)}")
        out.append(f"small_penalty_baseline_winners_displaced = {identity_policy_summary.get('baseline_small_penalty_displaced', 0)}")
        out.append(f"strong_penalty_baseline_winners_displaced = {identity_policy_summary.get('baseline_strong_penalty_displaced', 0)}")
        out.append(f"cross_quote_candidates_excluded       = {identity_policy_summary.get('cross_quote_candidates_excluded', 0)}")
        out.append(f"cross_quote_candidates_penalised      = {identity_policy_summary.get('cross_quote_candidates_penalised', 0)}")
        out.append(f"recovery_observations                  = {identity_policy_summary.get('recovery_observations', 0)}")
        out.append(f"no_valid_candidate_events             = {identity_policy_summary.get('no_valid_candidate_events', 0)}")
        out.append("```")
        for label, key in (("Replacement source transitions", "replacement_source_transitions"), ("Most frequently excluded images", "most_frequent_excluded_images"), ("Most frequent replacement images", "most_frequent_replacement_images"), ("Selection phases", "selection_phases")):
            values = identity_policy_summary.get(key) or []
            if values:
                out.append(f"{label}: " + ", ".join(f"{name} ({count})" for name, count in values if name))
                out.append("")
        event_categories = identity_policy_summary.get("event_categories") or []
        changed_policy = [
            item for item, category in zip(identity_policy_events, event_categories)
            if category == "identity_policy_winner_change"
        ]
        if changed_policy:
            out.append("Counterfactual winners changed by the generated-identity policy:")
            out.append(md_table_row(["time", "line_no", "policy-disabled winner", "action", "policy-enabled winner", "baseline score", "policy score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item in changed_policy[:20]:
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), f"{item.get('baseline_winner', '')} ({item.get('baseline_winner_source', '')})", item.get("baseline_identity_action", ""), f"{item.get('production_winner', '')} ({item.get('production_winner_source', '')})", item.get("baseline_winner_score", ""), item.get("production_policy_score", ""), item.get("selection_phase", "")]))
            out.append("")
        neutral_differences = [
            (item, category) for item, category in zip(identity_policy_events, event_categories)
            if category.startswith("policy_neutral_")
        ]
        if neutral_differences:
            out.append("Policy-neutral baseline differences:")
            out.append("These differences did not result from a generated-identity penalty or exclusion.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "deterministic baseline", "actual production winner", "reason", "baseline score", "production score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item, category in neutral_differences[:20]:
                reason = "equal-score production tie resolution" if category.endswith("equal_score_tie_resolution") else "downstream production selection"
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), f"{item.get('baseline_winner', '')} ({item.get('baseline_winner_source', '')})", f"{item.get('production_winner', '')} ({item.get('production_winner_source', '')})", reason, item.get("baseline_winner_score", ""), item.get("production_policy_score", ""), item.get("selection_phase", "")]))
            out.append("")
        legacy_unverified = [
            item for item, category in zip(identity_policy_events, event_categories)
            if category == "legacy_policy_causation_unverified"
        ]
        if legacy_unverified:
            out.append("Legacy winner differences with unverified policy causation:")
            out.append("These records predate shared-random-state counterfactual telemetry and are excluded from the causal winner-change count.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "reported baseline", "production winner", "baseline action", "phase"]))
            out.append(md_table_row(["---"] * 6))
            for item in legacy_unverified[:20]:
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), item.get("baseline_winner", ""), item.get("production_winner", ""), item.get("baseline_identity_action", ""), item.get("selection_phase", "")]))
            out.append("")


def _render_identity_shadow(report: Dict[str, Any], out: List[str]) -> None:
    identity_shadow = report.get("generated_identity_shadow") or {}
    identity_events = identity_shadow.get("events") or []
    identity_summary = identity_shadow.get("summary") or {}
    if identity_events:
        out.append("## Generated identity-policy shadow scoring")
        out.append("This section is shadow-only and hypothetical. It does not imply that the identity-policy shadow winner was posted.")
        out.append("")
        out.append("The winner-change percentage denominator is counterfactually validated observations where at least one cross-quote generated candidate was penalised or excluded. Older observations without saved-RNG replay are reported separately and are not attributed causally.")
        out.append("")
        out.append("```text")
        out.append(f"shadow_observations                         = {identity_summary.get('observations', 0)}")
        out.append(f"production_original_winners                 = {identity_summary.get('production_original', 0)}")
        out.append(f"production_generated_winners                = {identity_summary.get('production_generated', 0)}")
        out.append(f"production_generated_origin_quote_winners   = {identity_summary.get('production_generated_origin_quote', 0)}")
        out.append(f"production_generated_cross_quote_winners    = {identity_summary.get('production_generated_cross_quote', 0)}")
        out.append(f"policy_relevant_observations                = {identity_summary.get('policy_relevant_observations', 0)}")
        out.append(f"winner_changes                              = {identity_summary.get('winner_changes', 0)} ({float(identity_summary.get('winner_change_percent', 0.0)):.1f}%)")
        out.append(f"policy_effect_without_winner_change         = {identity_summary.get('policy_effect_without_winner_change', 0)}")
        out.append(f"legacy_policy_causation_unverified          = {identity_summary.get('legacy_policy_causation_unverified', 0)}")
        out.append(f"counterfactual_invariant_failures           = {identity_summary.get('counterfactual_invariant_failures', 0)}")
        out.append(f"production_winners_origin_only_excluded     = {identity_summary.get('production_winner_origin_only_excluded', 0)}")
        out.append(f"production_winners_small_penalty            = {identity_summary.get('production_winner_small_penalty', 0)}")
        out.append(f"production_winners_strong_penalty           = {identity_summary.get('production_winner_strong_penalty', 0)}")
        out.append(f"cross_quote_candidates_excluded             = {identity_summary.get('cross_quote_candidates_excluded', 0)}")
        out.append(f"cross_quote_candidates_penalised            = {identity_summary.get('cross_quote_candidates_penalised', 0)}")
        out.append("```")
        for label, key in (
            ("Most frequently excluded images", "most_frequent_excluded_images"),
            ("Most frequently penalised images", "most_frequent_penalised_images"),
            ("Most frequent production policies", "most_frequent_production_policies"),
            ("Most frequent shadow winners", "most_frequent_shadow_winners"),
            ("Selection phases", "selection_phases"),
        ):
            values = identity_summary.get(key) or []
            if values:
                out.append(f"{label}:")
                out.append(", ".join(f"{name} ({count})" for name, count in values if name))
                out.append("")
        identity_categories = identity_summary.get("event_categories") or []
        changed_identity = [
            item for item, category in zip(identity_events, identity_categories)
            if category == "identity_policy_winner_change"
        ]
        if changed_identity:
            out.append("Counterfactual winners changed by the generated-identity shadow policy:")
            out.append(md_table_row(["time", "line_no", "production", "action", "shadow", "production score", "shadow score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item in changed_identity[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""),
                    f"{item.get('production_winner', '')} ({item.get('production_source', '')})",
                    item.get("production_identity_action", ""),
                    f"{item.get('shadow_winner', '')} ({item.get('shadow_winner_source', '')})",
                    item.get("production_score", ""), item.get("shadow_winner_score", ""), item.get("selection_phase", ""),
                ]))
            out.append("")
        legacy_identity = [
            item for item, category in zip(identity_events, identity_categories)
            if category == "legacy_policy_causation_unverified"
        ]
        if legacy_identity:
            out.append("Legacy shadow winner differences with unverified policy causation:")
            out.append("These observations predate saved-random-state counterfactual telemetry and are excluded from winner-change counts.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "production", "reported shadow", "phase"]))
            out.append(md_table_row(["---"] * 5))
            for item in legacy_identity[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""), item.get("production_winner", ""),
                    item.get("shadow_winner", ""), item.get("selection_phase", ""),
                ]))
            out.append("")
        origin_only_production = [
            item for item in identity_events
            if item.get("production_identity_action") == "generated_cross_quote_origin_only_excluded"
        ]
        if origin_only_production:
            out.append("Production winners excluded by origin-quote-only shadow policy:")
            out.append(md_table_row(["time", "line_no", "production", "shadow replacement", "phase"]))
            out.append(md_table_row(["---"] * 5))
            for item in origin_only_production[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""), item.get("production_winner", ""),
                    f"{item.get('shadow_winner', '')} ({item.get('shadow_winner_source', '')})", item.get("selection_phase", ""),
                ]))
            out.append("")


def _render_historical_context_quality(report: Dict[str, Any], out: List[str]) -> None:
    context_quality = report.get("historical_context_quality") or {}
    out.append("## Historical context reply quality")
    error_health = report.get("error_health") or {}
    context_categories = {
        "historical_context_source_role_incompatibility",
        "historical_context_reply_failure",
        "legacy_regular_receipt_barrier",
    }
    current_context_incidents = [
        item
        for item in error_health.get("current_incidents") or []
        if item.get("category") in context_categories
    ]
    resolved_context_incidents = [
        item
        for item in error_health.get("historical_resolved_incidents") or []
        if item.get("category") in context_categories
    ]
    out.append(
        "Operational status: "
        f"**{plural_count(len(current_context_incidents), 'current independent incident')}**; "
        f"**{plural_count(len(resolved_context_incidents), 'resolved legacy receipt/source-role incident')}** "
        "in the selected log window."
    )
    if resolved_context_incidents:
        out.append(
            "Resolved legacy incidents remain visible as history; they are not counted as "
            "current historical-context failures."
        )
    status_counts = context_quality.get("status_counts") or {}
    out.append(
        f"Attempted: **{context_quality.get('attempted_count', 0)}**; "
        f"completed: **{status_counts.get('completed', 0)}**; "
        f"already completed: **{status_counts.get('already_completed', 0)}**; "
        f"failed: **{status_counts.get('failed', 0)}**; "
        f"skipped: **{status_counts.get('skipped', 0)}**; "
        f"dry run: **{status_counts.get('dry_run', 0)}**."
    )
    out.append(
        "Lengths (raw average / X-weighted average / weighted range): "
        f"**{round(context_quality['average_raw_characters'], 1) if context_quality.get('average_raw_characters') is not None else 'unavailable'} / "
        f"{round(context_quality['average_weighted_characters'], 1) if context_quality.get('average_weighted_characters') is not None else 'unavailable'} / "
        f"{context_quality.get('minimum_weighted_characters') if context_quality.get('minimum_weighted_characters') is not None else 'unavailable'}–"
        f"{context_quality.get('maximum_weighted_characters') if context_quality.get('maximum_weighted_characters') is not None else 'unavailable'}**."
    )
    out.append(
        f"Length metadata (raw observed/unavailable; weighted observed/unavailable): "
        f"**{context_quality.get('raw_length_observation_count', 0)}/{context_quality.get('raw_length_metadata_unavailable_count', 0)}; "
        f"{context_quality.get('weighted_length_observation_count', 0)}/{context_quality.get('weighted_length_metadata_unavailable_count', 0)}**."
    )
    out.append(
        f"Shortened: **{context_quality.get('shortened_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('shortening_metadata_unavailable_count', 0)}); "
        f"meaning omitted: **{context_quality.get('meaning_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('meaning_omitted_metadata_unavailable_count', 0)}); "
        f"source omitted: **{context_quality.get('source_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('source_omitted_metadata_unavailable_count', 0)}); "
        f"verification omitted: **{context_quality.get('verification_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('verification_omitted_metadata_unavailable_count', 0)})."
    )
    for label, key in (
        ("Verification labels", "verification_counts"),
        ("Source classes", "source_class_counts"),
        ("Overall reply confidence", "confidence_counts"),
        ("Formatter versions", "formatter_version_counts"),
        ("Rendering modes", "rendering_mode_counts"),
        ("Source-role audit versions", "source_role_audit_version_counts"),
    ):
        values = context_quality.get(key) or {}
        out.append(f"{label}: {_compact_counts(values)}")
    rendering_counts = context_quality.get("rendering_context_counts") or {}
    out.append(
        "Context rendering: "
        f"concrete event/date included={rendering_counts.get('concrete_event_or_date_context_included', 0)}, "
        f"date-only qualified included={rendering_counts.get('date_only_qualified_context_included', 0)}, "
        f"omitted because no useful event/date was admitted="
        f"{rendering_counts.get('context_omitted_no_useful_event_or_date', 0)}, "
        f"old generic fallback sentence used={rendering_counts.get('old_generic_fallback_used', 0)}"
        + (
            f", metadata unavailable={rendering_counts.get('rendering_metadata_unavailable', 0)}"
            if rendering_counts.get("rendering_metadata_unavailable", 0)
            else ""
        )
        + "."
    )
    for field, values in (context_quality.get("confidence_dimension_counts") or {}).items():
        out.append(
            f"Confidence {field.replace('_', ' ')}: {_compact_counts(values)}"
        )
    if context_quality.get("skip_reason_counts"):
        out.append("Skip reasons:")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in context_quality["skip_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    out.append("")


def _render_historical_corpus(report: Dict[str, Any], out: List[str]) -> None:
    corpus = report.get("historical_context_corpus_snapshot") or {}
    out.append("## Current historical-context corpus")
    if not corpus.get("available"):
        out.append(
            f"Snapshot incomplete: **{corpus.get('reason') or 'authoritative files unavailable'}**."
        )
    out.append(
        "Completed packets / attribution eligible / attribution ineligible: "
        f"**{corpus.get('completed_packet_count', 'unavailable')} / "
        f"{corpus.get('attribution_eligible_count', 'unavailable')} / "
        f"{corpus.get('completed_attribution_ineligible_count', 'unavailable')}**."
    )
    out.append(
        "Ordinary-post cycle / unresolved / historical-context blocked / allowed: "
        f"**{corpus.get('ordinary_post_cycle_count', 'unavailable')} / "
        f"{corpus.get('unresolved_quote_count', 'unavailable')} / "
        f"{corpus.get('historical_context_blocked_count', 'unavailable')} / "
        f"{corpus.get('historical_context_allowed_count', 'unavailable')}**."
    )
    out.append(
        f"Source-role policy: **{corpus.get('source_role_policy_version') or 'unavailable'}**; "
        f"semantic-gate policy: **{corpus.get('semantic_gate_policy_version') or 'unavailable'}**."
    )
    out.append(
        "Current ledger / gate projection: "
        f"`{str(corpus.get('semantic_review_ledger_sha256') or '')[:16] or 'unavailable'}` / "
        f"`{str(corpus.get('semantic_gate_projection_sha256') or '')[:16] or 'unavailable'}`."
    )
    if corpus.get("file_sha256"):
        out.append("Authoritative snapshot hashes:")
        for label, value in sorted(corpus["file_sha256"].items()):
            out.append(f"- {label}: `{str(value)[:16]}`")
    out.append("")


def _render_historical_engagement(report: Dict[str, Any], out: List[str]) -> None:
    engagement = report.get("historical_context_engagement") or {}
    out.append("## Historical context engagement")
    if not engagement.get("available"):
        out.append(f"Unavailable: **{engagement.get('reason') or 'analytics database not initialised'}**.")
    else:
        def engagement_percent(value: Any) -> str:
            return "metric unavailable" if value is None else f"{float(value) * 100:.2f}%"

        out.append(
            f"Trailing window: **{engagement.get('window_days', 28)} days**; "
            f"tracked post pairs: **{engagement.get('tracked_post_pairs', 0)}**; "
            f"with context replies: **{engagement.get('posts_with_context_replies', 0)}**; "
            f"snapshot coverage: **{engagement_percent(engagement.get('latest_snapshot_coverage'))}**."
        )
        out.append(
            "Medians (context view ratio / main engagement / context engagement / "
            "context bookmark / source-link click): "
            f"**{engagement_percent(engagement.get('median_context_view_ratio'))} / "
            f"{engagement_percent(engagement.get('median_main_post_engagement_rate'))} / "
            f"{engagement_percent(engagement.get('median_context_engagement_rate'))} / "
            f"{engagement_percent(engagement.get('median_context_bookmark_rate'))} / "
            f"{engagement_percent(engagement.get('median_source_link_click_rate'))}**."
        )
        out.append(
            "Latest analytics snapshots with unavailable impressions: "
            f"**{engagement.get('unavailable_impressions_count', 0)}**; "
            "latest analytics snapshots with unavailable URL-link clicks: "
            f"**{engagement.get('unavailable_click_metrics_count', 0)}**."
        )
        warnings = engagement.get("sample_size_warnings") or []
        if warnings:
            out.append("Sample-size warnings: " + ", ".join(str(value) for value in warnings) + ".")
        out.append("All associations are observational; the digest does not attribute causation.")
    out.append("")


def _render_shadow_lifecycle(report: Dict[str, Any], out: List[str]) -> None:
    lifecycle = report.get("shadow_feature_lifecycle") or {}
    out.append("## Shadow feature lifecycle")
    if not lifecycle.get("available"):
        out.append(f"Unavailable: **{lifecycle.get('reason') or 'invalid lifecycle register'}**.")
    else:
        out.append(
            "; ".join(
                f"**{feature.get('feature_name')}**=`{feature.get('current_state')}`"
                for feature in lifecycle.get("features", [])
            )
            + "."
        )
        overdue = lifecycle.get("overdue_decisions") or []
        if overdue:
            out.append(
                "Overdue lifecycle decisions: **"
                + ", ".join(
                    f"{row.get('feature_name')} ({row.get('next_decision_date')})"
                    for row in overdue
                )
                + "**."
            )
    out.append("")


def _render_reply_validation_failures(single_reply: Dict[str, Any], out: List[str]) -> None:
    """Show bounded unpublished reply previews and validation diagnostics."""
    details = single_reply.get("validation_failure_details") or {}
    if not details.get("candidate_count"):
        return
    out.append("")
    out.append("### Rejected reply validation details")
    out.append(
        "Failed rules: **" + _compact_counts(details.get("rule_counts") or {})
        + "**. Detail availability: **"
        + _compact_counts(details.get("details_status_counts") or {}) + "**."
    )
    out.append("")
    out.append(md_table_row([
        "time", "lane", "target ID", "category", "failed rules", "details",
        "Rejected reply (not published)",
    ]))
    out.append(md_table_row(["---"] * 7))
    for candidate in details.get("candidates") or []:
        codes = candidate.get("validation_error_codes") or []
        status = candidate.get("validation_error_details_status", "missing")
        availability = {
            "available": "available",
            "missing": "unavailable (older event did not record rules)",
            "empty": "unavailable (no rule codes recorded)",
            "partial": "partial (some values omitted)",
            "malformed": "unavailable (malformed rule details)",
        }.get(status, "unavailable")
        omitted = candidate.get("validation_error_codes_omitted_count", 0)
        if omitted:
            availability += f"; {omitted} values omitted"
        rejected_text = candidate.get("rejected_reply_text")
        rejected_preview = "unavailable (reply text not recorded)"
        if isinstance(rejected_text, str):
            rejected_preview = short(rejected_text, 600)
            if candidate.get("rejected_reply_text_status") == "truncated":
                rejected_preview += (
                    " (retained text truncated; original: "
                    f"{candidate.get('rejected_reply_text_character_count')} characters)"
                )
            elif len(rejected_text) > 600:
                rejected_preview += " (preview; retained text in JSON)"
        out.append(md_table_row([
            candidate.get("time", ""), candidate.get("lane", ""),
            candidate.get("target_id", ""), candidate.get("error_category", ""),
            ", ".join(codes) if codes else "unavailable", availability,
            rejected_preview,
        ], cell_limit=2000))
    omitted_candidates = details.get("omitted_candidate_count", 0)
    if omitted_candidates:
        out.append(
            f"Earlier rejected decisions omitted from this table: **{omitted_candidates}**; "
            "rule counts include every rejected decision in the selected window."
        )
    out.append("")


def _render_single_call_replies(report: Dict[str, Any], out: List[str]) -> None:
    single_reply = report.get("single_call_reply") or {}
    out.append("## Single-call conversational replies")
    out.append(
        f"**{plural_count(single_reply.get('candidate_evaluation_count', 0), 'candidate')} evaluated; "
        f"{plural_count(single_reply.get('replies_posted_count', 0), 'reply', 'replies')} posted; "
        f"{plural_count(single_reply.get('editorial_no_reply_count', 0), 'valid editorial no-reply decision')}; "
        f"{plural_count(single_reply.get('operational_failure_count', 0), 'operational failure')}.**"
    )
    out.append(
        "One-call compliance: **"
        f"{single_reply.get('one_call_compliance', 'no_candidates')}** "
        f"({single_reply.get('one_call_compliant_count', 0)} compliant decisions; "
        f"{single_reply.get('one_call_violation_count', 0)} violations; "
        f"{single_reply.get('one_call_incomplete_count', 0)} incomplete; "
        f"{single_reply.get('recovered_draft_count', 0)} drafts recovered with no provider call)."
    )
    out.append(
        "Logical/physical call diagnostics: **"
        f"{single_reply.get('repeated_model_attempt_candidate_count', 0)} repeated model-attempt candidates; "
        f"{single_reply.get('excess_provider_usage_candidate_count', 0)} excess usage candidates; "
        f"{single_reply.get('authorised_pre_execution_retry_count', 0)} authorised pre-execution retries; "
        f"{single_reply.get('provider_request_attempt_mismatch_candidate_count', 0)} attempt-count mismatches**. "
        "Attempt telemetry: **"
        + _compact_counts(
            single_reply.get("provider_request_attempt_metadata_status_counts") or {}
        )
        + "**."
    )
    out.append(
        "Strategy versions: **"
        + _compact_counts(single_reply.get("strategy_version_counts") or {})
        + "**; models: **"
        + _compact_counts(single_reply.get("model_counts") or {})
        + "**; lanes: **"
        + _compact_counts(single_reply.get("lane_counts") or {})
        + "**."
    )
    out.append(
        "Reply kinds: **"
        + _compact_counts(single_reply.get("reply_kind_counts") or {})
        + "**; no-reply reasons: **"
        + _compact_counts(single_reply.get("no_reply_reason_counts") or {})
        + "**."
    )
    out.append(
        "Operational failure reasons: **"
        + _compact_counts(single_reply.get("operational_failure_reason_counts") or {})
        + "**; error categories: **"
        + _compact_counts(single_reply.get("error_category_counts") or {})
        + "**."
    )
    out.append(
        "Schema/local-validation failures: **"
        f"{single_reply.get('schema_validation_failure_count', 0)} / "
        f"{single_reply.get('local_validation_failure_count', 0)}**; "
        f"posting failures: **{single_reply.get('posting_failure_count', 0)}**."
    )
    _render_reply_validation_failures(single_reply, out)
    out.append(
        "Average visible turns / visible characters / same-author interactions / "
        "recent conversational replies / trusted facts / supplied images: **"
        + " / ".join(
            "unavailable" if value is None else f"{float(value):.2f}"
            for value in (
                single_reply.get("average_visible_turn_count"),
                single_reply.get("average_visible_character_count"),
                single_reply.get("average_same_author_interaction_count"),
                single_reply.get(
                    "average_recent_conversational_reply_count"
                ),
                single_reply.get("average_trusted_fact_count"),
                single_reply.get("average_supplied_image_count"),
            )
        )
        + "**."
    )
    token_totals = single_reply.get("token_totals") or {}
    out.append(
        "Provider usage totals: **"
        f"input={token_totals.get('input_tokens', 0)}, "
        f"cached-input={token_totals.get('cached_input_tokens', 0)}, "
        f"cache-write-input={token_totals.get('cache_write_input_tokens', 0)}, "
        f"output={token_totals.get('output_tokens', 0)}, "
        f"reasoning={token_totals.get('reasoning_tokens', 0)}, "
        f"total={token_totals.get('total_tokens', 0)}** across "
        f"{single_reply.get('provider_usage_event_count', 0)} successful responses."
    )
    average_latency = single_reply.get("provider_latency_average_ms")
    maximum_latency = single_reply.get("provider_latency_maximum_ms")
    cost_total = single_reply.get("cost_total") or {}
    out.append(
        "Provider latency average/max: **"
        f"{round(float(average_latency), 2) if average_latency is not None else 'unavailable'} / "
        f"{maximum_latency if maximum_latency is not None else 'unavailable'} ms**; "
        "published selected-window cost: **"
        f"{format_openai_usd(cost_total.get('amount')) if cost_total.get('amount') is not None else 'unavailable'} "
        f"({cost_total.get('status', 'unavailable')})**."
    )
    if "provider_request_coverage" in report:
        request_coverage = report.get("provider_request_coverage") or {}
        out.append(
            "Exact provider-request coverage: **"
            + _compact_counts(request_coverage.get("category_counts") or {})
            + "** across **"
            + str(request_coverage.get("logical_call_denominator", 0))
            + " logical calls** and **"
            + str(request_coverage.get("physical_attempt_denominator", 0))
            + " physical attempts**. Complete bodies are included only in JSON; "
            "this Markdown section is a labelled summary, not the complete input."
        )
    legacy_multi_stage = report.get("legacy_multi_stage") or {}
    if (
        legacy_multi_stage.get("decision_count")
        or legacy_multi_stage.get("stage_summary_event_count")
    ):
        out.append(
            "Legacy multi-stage events in this window (compatibility count only): "
            f"**{legacy_multi_stage.get('decision_count', 0)} decisions; "
            f"{legacy_multi_stage.get('stage_summary_event_count', 0)} stage summaries**."
        )
    out.append("")


def _render_pagination_warnings(report: Dict[str, Any], out: List[str]) -> None:
    bounded_protocol_warnings = [
        event
        for event in (report.get("events") or [])
        if event.get("kind") == "quote_pagination_repeated_token"
    ]
    if bounded_protocol_warnings:
        out.append("## Bounded protocol warnings")
        out.append(
            "Repeated quote-pagination tokens ended their individual traversal "
            "as bounded partial successes; they are warnings, not operational failures."
        )
        out.append(
            md_table_row(
                [
                    "time",
                    "post ID",
                    "token fingerprint",
                    "pages completed",
                    "results retained",
                ]
            )
        )
        out.append(md_table_row(["---"] * 5))
        for event in bounded_protocol_warnings:
            out.append(
                md_table_row(
                    [
                        event.get("time", ""),
                        event.get("post_id", ""),
                        event.get("token_fingerprint", ""),
                        event.get("pages_completed", ""),
                        event.get("results_retained", ""),
                    ]
                )
            )
        out.append("")


def _render_counts(report: Dict[str, Any], out: List[str]) -> None:
    stats = report["summary"].get("stats", {})
    routine = report["summary"].get("routine_skip_counts", {})
    out.append("## Counts")
    out.append("```json")
    out.append(json.dumps({"stats": stats, "routine_skip_counts": routine}, indent=2, ensure_ascii=False))
    out.append("```")
    out.append("")


def _render_event_details(report: Dict[str, Any], out: List[str]) -> None:
    events = report.get("events") or []
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        by_kind.setdefault(ev["kind"], []).append(ev)

    def section(
        kind: str,
        title: str,
        cols: List[str],
        *,
        column_labels: Optional[Dict[str, str]] = None,
        value_formatters: Optional[Dict[str, Any]] = None,
        cell_limit: int = 240,
    ) -> None:
        rows = by_kind.get(kind) or []
        if not rows:
            return
        out.append(f"## {title}")
        labels = column_labels or {}
        formatters = value_formatters or {}
        out.append(md_table_row(
            [labels.get(column, column) for column in cols],
            cell_limit=cell_limit,
        ))
        out.append(md_table_row(["---"] * len(cols), cell_limit=cell_limit))
        for ev in rows:
            out.append(md_table_row(
                [
                    formatters[c](ev.get(c, ""))
                    if c in formatters
                    else ev.get(c, "")
                    for c in cols
                ],
                cell_limit=cell_limit,
            ))
        out.append("")

    section(
        "quote_image_posted",
        "Quote/image posts",
        [
            "time",
            "post_id",
            "line_no",
            "quote_hash",
            "image_basename",
            "image_no",
            "image_score",
            "made_with_ai",
            "text",
        ],
        cell_limit=1000,
    )

    quote_publication = report.get("quote_publication") or {}
    warning_rows = quote_publication.get("correlation_warnings") or []
    if warning_rows or quote_publication.get("correlation_warning_omitted_count"):
        out.append("## Quote publication correlation warnings")
        out.append(
            "Conflicting structured evidence is left unresolved rather than "
            "silently overwritten."
        )
        warning_columns = ["time", "post_id", "field", "event types", "warning"]
        out.append(md_table_row(warning_columns, cell_limit=1000))
        out.append(md_table_row(["---"] * len(warning_columns), cell_limit=1000))
        for row in warning_rows:
            out.append(md_table_row(
                [
                    row.get("time", ""),
                    row.get("post_id", ""),
                    row.get("field", ""),
                    " ↔ ".join(row.get("event_types") or []),
                    row.get("message", ""),
                ],
                cell_limit=1000,
            ))
        omitted = int(
            quote_publication.get("correlation_warning_omitted_count") or 0
        )
        if omitted:
            out.append(
                f"{omitted} additional correlation warning(s) omitted after "
                f"the fixed limit of {QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT}."
            )
        out.append("")

    section("daily_meme_posted", "Daily meme posts", ["time", "post_id", "file", "summary"])
    section("meme_cycle_recycled", "Meme cycle recycling in the log window", ["time", "message"])
    section("quote_selected", "Regular quote selections", ["time", "line_no", "quote_hash", "weight", "seasonal_boost"])
    section("matched_image_selected", "Matched image selections", ["time", "image", "image_no", "score", "components"])
    section(
        "regular_image_selected",
        "Regular image selection metadata",
        ["time", "source", "basename", "score", "origin_quote_hash", "origin_quote_match", "origin_quote_boost"],
        value_formatters={
            "score": format_display_number,
            "origin_quote_boost": format_display_number,
        },
    )
    section("image_cycle_status", "Image cycle status", ["time", "used_count", "currently_eligible", "remaining_count", "seasonally_excluded", "stale_excluded", "cycle_reset"])
    section("quote_cycle_reset", "Quote cycle resets", ["time", "reason", "affected", "full_selectable", "full_hard_excluded"])
    section("mention_reply_posted", "Mention replies", ["time", "mention_id", "author_id", "incoming_text", "public_reply_text", "reply_post_id", "public_reply_text_status", "public_reply_text_source", "public_reply_text_reason"])
    section("hot_post_reply_posted", "Hot-post replies", ["time", "hot_post_reply_id", "author_id", "incoming_text", "public_reply_text", "reply_post_id", "public_reply_text_status", "public_reply_text_source", "public_reply_text_reason"])
    section("quote_tweet_reply_posted", "Quote-tweet replies", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text", "public_reply_text", "reply_post_id", "public_reply_text_status", "public_reply_text_source", "public_reply_text_reason"])
    section(
        "confirmed_public_reply", "Confirmed reply evidence",
        ["time", "lane", "target_id", "reply_post_id", "public_reply_text",
         "public_reply_text_status", "public_reply_text_source", "public_reply_text_reason",
         "evidence_scope"],
    )
    text_health = report.get("published_reply_text_health") or {}
    evidence_failures = {
        name: status for name, status in (text_health.get("durable_evidence") or {}).items()
        if status.get("available") is not True and status.get("status") not in {"absent", None}
    }
    if text_health.get("confirmed_record_count") or text_health.get("warnings") or evidence_failures:
        out.append("## Published reply text health")
        total = int(text_health.get("confirmed_record_count") or 0)
        complete = int(text_health.get("complete_text_record_count") or 0)
        out.append(
            f"{complete}/{total} confirmation records have complete text; "
            f"{total - complete} incomplete or unavailable, including "
            f"{text_health.get('conflict_count', 0)} conflicts. "
            "Drafts are not confirmed public text. Durable snapshot evidence is "
            "independent of the log window and does not add to its publication counts."
        )
        if evidence_failures:
            out.append("Durable reply evidence unavailable:")
            out.append(md_table_row(["source", "status", "reason"]))
            out.append(md_table_row(["---"] * 3))
            for name, status in evidence_failures.items():
                out.append(md_table_row([name, status.get("status"), status.get("reason", "")]))
        if text_health.get("warnings"):
            out.append("")
            out.append(md_table_row(["reply_post_id", "text health warning"]))
            out.append(md_table_row(["---", "---"]))
        for warning in text_health.get("warnings") or []:
            out.append(md_table_row([
                warning.get("reply_post_id", ""), warning.get("reason", "")
            ], cell_limit=500))
        out.append("")
        if report.get("verbose_replies"):
            for event in events:
                text = event.get("public_reply_text")
                if not text or event.get("public_reply_text_complete") is not True:
                    continue
                out.append(
                    "### Exact confirmed reply " + str(event.get("reply_post_id") or "unknown")
                    + " (" + str(event.get("public_reply_text_source") or "source unavailable") + ")"
                )
                # A fence longer than any run in the text preserves exact prose,
                # including newlines, without interpreting it as Markdown.
                fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
                out.extend([fence + "text", text, fence, ""])
        out.append("")
    section(
        "historical_context_semantic_gate",
        "Historical context semantic gate",
        ["time", "status", "policy_version", "ledger_sha256", "projection_sha256", "blocked_quote_count", "reason"],
    )
    section(
        "historical_context_runtime",
        "Historical-context runtime availability",
        ["time", "status", "regular_post_eligibility_unchanged", "reason"],
    )
    section(
        "reply_evidence_unavailable",
        "Reply evidence unavailable",
        ["time", "lane", "target_id"],
    )
    section(
        "runtime_control_pause",
        "Runtime control pauses",
        ["time", "key", "lanes", "until_epoch"],
    )
    section(
        "clarification_reply_cap_override",
        "Clarification reply cap overrides",
        ["time", "target_id", "thread_id", "author_id", "bypassed_cap"],
    )
    section(
        "clarification_reply_used",
        "Clarification replies used",
        ["time", "target_id", "thread_id", "author_id", "reply_post_id", "trigger"],
    )
    section(
        "repair_reply_completed",
        "Repair replies completed",
        ["time", "target_id", "thread_id", "author_id", "reply_post_id"],
    )
    historical_rows = by_kind.get("historical_context_reply") or []
    if historical_rows:
        semantic_columns = (
            "semantic_review_disposition",
            "semantic_review_ledger_sha256",
            "semantic_review_projection_sha256",
        )
        reliable_semantic_metadata = any(
            all(row.get(field) not in (None, "") for field in semantic_columns)
            for row in historical_rows
        )
        cols = [
            "time", "status", "parent_post_id", "quote_id", "reply_post_id",
            "public_reply_text", "public_reply_text_status", "public_reply_text_source",
            "public_reply_text_reason", "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "formatter_version", "rendering_mode",
            "shortening_applied", "reason",
        ]
        if reliable_semantic_metadata:
            cols.extend(semantic_columns)
        if report.get("verbose_replies"):
            cols.append("reply_preview")
        section(
            "historical_context_reply",
            "Historical context replies",
            cols,
            column_labels={
                "historical_confidence": "overall_reply_confidence",
            },
        )
        if not reliable_semantic_metadata:
            out.append(
                "Per-reply semantic-review disposition and ledger/projection hashes were "
                "not supplied reliably by these events; empty columns are omitted."
            )
            out.append("")
    transaction_outcomes = (report.get("production_consistency") or {}).get(
        "context_transaction_outcomes"
    )
    if not isinstance(transaction_outcomes, dict):
        transaction_outcomes = prepare_context_transaction_outcomes(events)
    resolved_pending_count = transaction_outcomes.get("resolved_pending_count", 0)
    outstanding_transaction_rows = transaction_outcomes.get("outstanding_transactions") or []
    if resolved_pending_count or outstanding_transaction_rows:
        out.append("## Confirmed-main/context transaction states")
        if resolved_pending_count:
            out.append(
                f"**{resolved_pending_count}** intermediate `context_reply_pending` "
                "states subsequently reached a terminal outbox state; they are not outstanding."
            )
        if outstanding_transaction_rows:
            out.append("Outstanding intermediate states:")
            transaction_columns = [
                "time",
                "parent_post_id",
                "main_post_state",
                "context_reply_state",
                "context_state_persisted",
                "reason",
            ]
            out.append(md_table_row(transaction_columns))
            out.append(md_table_row(["---"] * len(transaction_columns)))
            for row in outstanding_transaction_rows:
                out.append(md_table_row([
                    row.get(column, "") for column in transaction_columns
                ]))
        out.append("")
    section(
        "historical_context_obligation",
        "Historical-context outbox obligations",
        [
            "time",
            "status",
            "parent_post_id",
            "context_reply_state",
            "attempt_number",
            "remote_work_repeated",
            "error_type",
            "reason",
        ],
    )
    section(
        "historical_context_outbox",
        "Historical-context outbox health",
        [
            "time",
            "status",
            "parent_post_id",
            "error_type",
            "main_post_success_preserved",
            "unrelated_lanes_available",
            "reason",
        ],
    )
    section(
        "daily_meme_failure",
        "Daily meme failures by stage",
        ["time", "stage", "post_id", "error_type", "reason"],
    )
    section(
        "single_call_reply_decision",
        "Single-call decision detail",
        [
            "time",
            "lane",
            "target_id",
            "strategy_version",
            "model",
            "pipeline_status",
            "reply_kind",
            "reason_code",
            "model_call_count",
            "local_validation_status",
            "error_category",
            "visible_turn_count",
            "visible_character_count",
            "same_author_interaction_count",
            "recent_conversational_reply_count",
            "trusted_fact_count",
            "supplied_image_count",
            "provider_latency_ms",
            "provider_request_attempt_count",
            "provider_request_attempt_count_status",
            "provider_status_code",
            "provider_reset_epoch",
            "provider_retry_after_seconds",
        ],
    )
    section(
        "single_call_reply_posting_outcome",
        "Single-call posting outcomes",
        [
            "time",
            "status",
            "lane",
            "target_id",
            "reply_post_id",
            "strategy_version",
            "reply_kind",
            "reason_code",
            "failure_reason",
        ],
    )
    section(
        "reply_target_terminal",
        "Terminal reply targets",
        ["time", "lane", "target_id", "outcome", "reason"],
    )
    section("hot_post_search_result", "Hot-post recent-search results", ["time", "original_post_id", "candidates"])
    section("mention_skipped", "Mention direct skips", ["time", "mention_id", "author_id", "incoming_text", "reason"])
    section("hot_post_reply_skipped", "Hot-post direct skips", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reason"])
    section("quote_tweet_skipped", "Quote-tweet direct skips", ["time", "quote_tweet_id", "reason"])
    section("api_cooldown_entered", "API cooldowns entered", ["time", "reason", "until"])
    section("used_history_migrated", "Used-history migrations", ["time", "legacy_file", "json_file"])
    section("used_history_normalized", "Used-history normalizations", ["time", "json_file"])


def _render_main_post_recovery(report: Dict[str, Any], out: List[str], lifecycle_summary: Mapping[str, Any]) -> None:
    recovery = report.get("main_post_recovery") or {}
    receipt_events = recovery.get("receipt_events") or []
    confirmed_post_recovery = recovery.get("confirmed_post_recovery") or []
    if receipt_events or confirmed_post_recovery:
        out.append("## Transactional receipt lifecycle")
        if receipt_events:
            outstanding = lifecycle_summary["unresolved"]
            normal_pairs = lifecycle_summary["completed_count"]
            out.append(
                f"Routine two-phase receipt write/remove pairs completed: **{normal_pairs}**. "
                "The write event can be logged at WARNING while still being a normal durable "
                "transaction step; it is not an incident by itself."
            )
            out.append(
                "Completed main-post receipt lifecycles by lane: "
                f"regular quote/image **{lifecycle_summary['regular_completed_count']}**; "
                f"daily-meme **{lifecycle_summary['meme_completed_count']}**."
            )
            if lifecycle_summary["boundary_removal_count"]:
                out.append(
                    "Reconciled main-post receipt removals whose opening write was "
                    "outside the selected window: "
                    f"**{lifecycle_summary['boundary_removal_count']}**."
                )
            if outstanding:
                out.append("Stale or unresolved receipt events:")
                out.append(md_table_row(["time", "level", "lane", "kind", "post_id", "quote_hash", "image/file", "message"]))
                out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
            for item in outstanding:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("lane", ""),
                    item.get("kind", ""),
                    item.get("post_id", ""),
                    item.get("quote_hash", ""),
                    item.get("image", item.get("file", "")),
                    item.get("message", ""),
                ]))
            out.append("")
        if confirmed_post_recovery:
            out.append("Confirmed remote posts with local recovery/persistence trouble:")
            out.append(md_table_row(["time", "level", "where", "message"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in confirmed_post_recovery:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("where", ""),
                    item.get("message", ""),
                ]))
            out.append("")


def _render_reply_recovery(
    report: Dict[str, Any], out: List[str], lifecycle_summary: Mapping[str, Any],
) -> None:
    reply_recovery = report.get("confirmed_reply_recovery") or {}
    reply_receipt_events = reply_recovery.get("receipt_events") or []
    reply_recovery_warnings = reply_recovery.get("warnings") or []
    active_snapshot_receipts = reply_recovery.get("active_snapshot_receipts") or []
    if reply_receipt_events or reply_recovery_warnings or active_snapshot_receipts:
        normal_reply_pairs = lifecycle_summary.get("normal_reply_pairs", 0)
        terminal_reply_removals_outside_window = lifecycle_summary.get(
            "terminal_reply_removals_outside_window", 0,
        )
        definite_non_success_clears = lifecycle_summary.get("definite_non_success_clears", 0)
        confirmed_state_fallback_clears = lifecycle_summary.get("confirmed_state_fallback_clears", 0)
        reconciled_ambiguity_sending_receipts = lifecycle_summary.get(
            "reconciled_ambiguity_sending_receipts", 0,
        )
        unresolved_reply_receipts = lifecycle_summary.get("unresolved_reply_receipts") or []
        unavailable_reply_receipt_rows = lifecycle_summary.get("unavailable_reply_receipt_rows") or []
        has_actual_recovery = bool(
            reply_recovery_warnings
            or unresolved_reply_receipts
            or unavailable_reply_receipt_rows
            or active_snapshot_receipts
            or confirmed_state_fallback_clears
        )
        out.append(
            "## Confirmed-reply recovery"
            if has_actual_recovery
            else "## Confirmed-reply receipt lifecycle"
        )
        if active_snapshot_receipts:
            out.append("Active confirmed-reply receipt identities in the current snapshot:")
            out.append(
                md_table_row(
                    [
                        "transaction/attempt",
                        "receipt role",
                        "lane",
                        "target",
                        "artefacts",
                    ]
                )
            )
            out.append(md_table_row(["---"] * 5))
            for item in active_snapshot_receipts:
                out.append(
                    md_table_row(
                        [
                            ", ".join(item.get("transaction_ids") or []),
                            item.get("receipt_role_label", ""),
                            item.get("lane", ""),
                            ", ".join(item.get("target_ids") or []),
                            ", ".join(item.get("artifact_names") or []),
                        ]
                    )
                )
        if reply_receipt_events:
            out.append(
                f"Routine confirmed-reply receipt write/remove pairs completed: "
                f"**{normal_reply_pairs}**."
            )
            if definite_non_success_clears:
                out.append(
                    "Prepared reply receipts cleared after a definite non-success: "
                    f"**{definite_non_success_clears}**."
                )
            if confirmed_state_fallback_clears:
                out.append(
                    "Confirmed replies preserved through the durable canonical-state "
                    f"fallback: **{confirmed_state_fallback_clears}**."
                )
            if reconciled_ambiguity_sending_receipts:
                out.append(
                    "Sending-receipt barrier observations durably reconciled with "
                    "their remote-write ambiguity: "
                    f"**{reconciled_ambiguity_sending_receipts}**. They remain "
                    "visible under historical/resolved incident errors."
                )
            if terminal_reply_removals_outside_window:
                out.append(
                    "Confirmed-reply receipt removals whose opening write was outside "
                    f"the observed window: **{terminal_reply_removals_outside_window}**."
                )
            if unresolved_reply_receipts:
                out.append("Stale or unresolved confirmed-reply receipts:")
                out.append(md_table_row(["time", "level", "lane", "kind", "target_id", "reply_post_id", "message"]))
                out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in unresolved_reply_receipts:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("lane", ""),
                    item.get("kind", ""),
                    item.get("target_id", ""),
                    item.get("reply_post_id", ""),
                    item.get("message", ""),
                ]))
            if unavailable_reply_receipt_rows:
                out.append(
                    "Confirmed-reply receipt starts whose current status cannot "
                    "be established from retained evidence:"
                )
                out.append(
                    md_table_row(
                        ["time", "lane", "kind", "target_id", "message"]
                    )
                )
                out.append(md_table_row(["---"] * 5))
                for item in unavailable_reply_receipt_rows:
                    out.append(
                        md_table_row(
                            [
                                item.get("time", ""),
                                item.get("lane", ""),
                                item.get("kind", ""),
                                item.get("target_id", ""),
                                item.get("message", ""),
                            ]
                        )
                    )
            out.append("")
        if reply_recovery_warnings:
            out.append("Confirmed replies with local recovery/persistence trouble:")
            out.append(md_table_row(["time", "level", "where", "message"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in reply_recovery_warnings:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("where", ""),
                    item.get("message", ""),
                ]))
            out.append("")


def _render_asset_health(report: Dict[str, Any], out: List[str]) -> None:
    queue = report.get("meme_queue_health") or {}
    if queue:
        out.append("## Current meme queue")
        out.append(
            f"Current filesystem/configuration snapshot at `{queue.get('observed_at')}`; "
            "counts are independent of the selected log window. Configuration describes "
            "the files on disk, not confirmation that a running process has reloaded them."
        )
        out.append(f"Status: **{queue.get('status')}**. {queue.get('reason') or ''}")
        out.append(
            f"Candidates: **{queue.get('candidate_count')}**; already posted in this cycle: "
            f"**{queue.get('posted_count')}**; unposted: **{queue.get('unposted_count')}**; "
            f"available for selection, including recycling: **{queue.get('available_to_select_count')}**."
        )
        out.append(
            f"Daily posting enabled: `{queue.get('posting_enabled')}`; automatic recycling: "
            f"`{queue.get('reset_when_all_posted')}`. Directory: `{queue.get('directory')}`."
        )
        out.append("")
    asset_health = report.get("asset_health") or []
    if asset_health:
        has_meme_availability = any(item.get("kind", "").startswith("meme_") for item in asset_health)
        out.append("## Asset availability and metadata observations" if has_meme_availability else "## Asset metadata health")
        if has_meme_availability:
            out.append("These are historical observations in the selected log window; see the current meme queue snapshot for availability now.")
        out.append(md_table_row(["time", "level", "kind", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for item in asset_health:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("level", ""),
                item.get("kind", ""),
                item.get("message", ""),
            ]))
        out.append("")


def _render_api_health(report: Dict[str, Any], out: List[str]) -> None:
    api_health = report.get("api_health") or {}
    api_errors = api_health.get("errors") or []
    handled_restrictions = api_health.get("handled_restrictions") or []
    cooldown_active = api_health.get("cooldown_active") or []
    post_cooldown_errors = api_health.get("post_cooldown_errors") or []
    if api_errors or handled_restrictions or cooldown_active:
        out.append("## API health")
        out.append(
            f"Unique incidents: **{api_health.get('unique_incident_count', 0)}**; "
            "tweet-create requests observed: "
            f"**{api_health.get('tweet_create_request_count', 0)}**; "
            "media-upload requests observed: "
            f"**{api_health.get('media_upload_request_count', 0)}**; "
            "failed post/reply requests: "
            f"**{api_health.get('posting_attempt_count', 0)}**; "
            f"reply-target eligibility 403 responses: **{api_health.get('target_eligibility_403_count', 0)}**; "
            f"deleted/inaccessible-tweet 403 responses: "
            f"**{api_health.get('deleted_or_inaccessible_tweet_403_count', 0)}**; "
            f"transient transport failures: **{api_health.get('transient_failure_count', 0)}**; "
            f"rate-limit failures: **{api_health.get('rate_limit_failure_count', 0)}**."
        )
        out.append("")
        if api_errors:
            out.append(md_table_row(["time", "service", "endpoint", "status", "window", "remaining", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in api_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                    item.get("remaining", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if handled_restrictions:
            out.append("Handled API restrictions:")
            out.append(md_table_row(["time", "classification", "service", "endpoint", "status", "lane", "target", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
            for item in handled_restrictions:
                out.append(md_table_row([
                    item.get("time", ""),
                    str(item.get("restriction_kind") or "other").replace("_", " "),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("lane", ""),
                    item.get("target_id", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if cooldown_active:
            out.append("Cooldown-active checks:")
            out.append(md_table_row(["time", "until", "reason"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in cooldown_active:
                out.append(md_table_row([item.get("time", ""), item.get("until", ""), item.get("reason", "")]))
            out.append("")
        if post_cooldown_errors:
            out.append("Post-cooldown errors:")
            out.append(md_table_row(["time", "service", "endpoint", "status", "window"]))
            out.append(md_table_row(["---", "---", "---", "---", "---"]))
            for item in post_cooldown_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                ]))
            out.append("")
        if api_health.get("has_5xx_failures") and api_health.get("not_rate_limited"):
            out.append("503/5xx summary: likely upstream/API-side failure, not quota exhaustion; remaining quota was non-zero on recorded error headers.")
            out.append("")
        if handled_restrictions:
            legacy_cooldowns = int(api_health.get("legacy_cooldown_from_target_restriction_count", 0) or 0)
            deleted_count = int(
                api_health.get("deleted_or_inaccessible_tweet_403_count", 0) or 0
            )
            target_count = int(api_health.get("target_eligibility_403_count", 0) or 0)
            if legacy_cooldowns:
                out.append(
                    "403 restriction summary: deterministic target restrictions were identified; "
                    f"**{legacy_cooldowns} legacy cooldown activation(s)** in this historical window "
                    "were caused by the pre-fix classification."
                )
            else:
                out.append(
                    "403 restriction summary: "
                    f"{plural_count(deleted_count, 'deleted/inaccessible target')} and "
                    f"{plural_count(target_count, 'reply-target eligibility restriction')} "
                    "were handled locally without being presented as current independent errors."
                )
            out.append("")


def _render_self_test_errors(report: Dict[str, Any], out: List[str]) -> None:
    self_test_errors = report.get("self_test_errors") or []
    if self_test_errors:
        out.append("## Self-test failures")
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in self_test_errors:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
        out.append("")


def _render_operational_errors(report: Dict[str, Any], out: List[str]) -> None:
    error_health = report.get("error_health") or {}
    current_incidents = error_health.get("current_incidents") or []
    historical_incidents = error_health.get("historical_resolved_incidents") or []
    resolution_unavailable_incidents = (
        error_health.get("resolution_unavailable_incidents") or []
    )
    transient_observations = error_health.get("transient_provider_observations") or []
    out.append("## Transient provider observations")
    if not transient_observations:
        out.append("None observed in the selected window.")
    else:
        out.append(
            "A transient provider failure was observed. Provider recovery is unverified: "
            "these point-in-time observations are neither current local safety incidents "
            "nor historically resolved incidents."
        )
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "locations",
                ]
            )
        )
        out.append(md_table_row(["---"] * 6))
        for observation in transient_observations:
            out.append(
                md_table_row(
                    [
                        str(observation.get("category") or "").replace("_", " "),
                        observation.get("first_seen", ""),
                        observation.get("last_seen", ""),
                        observation.get("record_count", 0),
                        observation.get("traceback_count", 0),
                        ", ".join(observation.get("affected_locations") or []),
                    ]
                )
            )
    out.append("")

    out.append("## Current independent errors")
    safety = report.get("remote_write_safety") or {}
    if safety.get("configured") is True and safety.get("available") is not True:
        out.append("Current remote-write safety is **unknown / unavailable**; incident absence cannot establish readiness.")
    elif safety.get("blocking") is True:
        out.append(
            "Current remote writes are **blocked**; see the active components in Remote-write safety. "
            "Valid in-flight receipts, media and transport transactions block other writes "
            "without themselves establishing an operational incident. This is a current "
            "snapshot; historical-cutoff incident attribution is unchanged."
        )
    if not current_incidents:
        if safety.get("configured") is True and (safety.get("available") is not True or safety.get("blocking") is True):
            out.append("No independent operational incident established in the selected window.")
        else:
            out.append("None unresolved in the selected window.")
    else:
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "locations",
                    "root summary",
                ]
            )
        )
        out.append(md_table_row(["---"] * 7))
        for incident in current_incidents:
            out.append(
                md_table_row(
                    [
                        str(incident.get("category") or "").replace("_", " "),
                        incident.get("first_seen", ""),
                        incident.get("last_seen", ""),
                        incident.get("record_count", 0),
                        incident.get("traceback_count", 0),
                        ", ".join(incident.get("affected_locations") or []),
                        incident.get("summary", ""),
                    ]
                )
            )
    out.append("")

    if resolution_unavailable_incidents:
        out.append("## Incident status unavailable from retained evidence")
        out.append(
            "These historical observations are not asserted to be current or "
            "resolved because neither retained terminal evidence nor an "
            "authoritative current snapshot is available."
        )
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "locations",
                    "status evidence",
                ]
            )
        )
        out.append(md_table_row(["---"] * 6))
        for incident in resolution_unavailable_incidents:
            out.append(
                md_table_row(
                    [
                        str(incident.get("category") or "").replace("_", " "),
                        incident.get("first_seen", ""),
                        incident.get("last_seen", ""),
                        incident.get("record_count", 0),
                        ", ".join(incident.get("affected_locations") or []),
                        incident.get("resolution_reason", ""),
                    ]
                )
            )
        out.append("")

    out.append("## Historical/resolved incident errors")
    if not historical_incidents:
        out.append("None identified in the selected window.")
    else:
        out.append(
            "Repeated tracebacks are grouped under their root incident and retained here as "
            "historical evidence; they do not determine the current-health headline."
        )
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "resolution",
                    "resolved at",
                ]
            )
        )
        out.append(md_table_row(["---"] * 7))
        for incident in historical_incidents:
            out.append(
                md_table_row(
                    [
                        str(incident.get("category") or "").replace("_", " "),
                        incident.get("first_seen", ""),
                        incident.get("last_seen", ""),
                        incident.get("record_count", 0),
                        incident.get("traceback_count", 0),
                        incident.get("resolution_reason", ""),
                        incident.get("resolution_time", ""),
                    ]
                )
            )
    out.append("")


def _render_warnings(report: Dict[str, Any], out: List[str]) -> None:
    warnings = [
        item
        for item in report.get("errors_and_warnings") or []
        if item.get("level") == "WARNING"
    ]
    out.append("## Other warnings")
    if not warnings:
        out.append("None found in selected window.")
    else:
        out.append(md_table_row(["time", "where", "message"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in warnings:
            out.append(
                md_table_row(
                    [item.get("time"), item.get("where"), item.get("message")]
                )
            )
    out.append("")


def _render_lifecycle(report: Dict[str, Any], out: List[str]) -> None:
    if report.get("lifecycle"):
        out.append("## Lifecycle")
        out.append("```text")
        for item in report["lifecycle"]:
            out.append(f"{item['time']} {item['level']} {item['message']}")
        out.append("```")
        out.append("")


def _render_config(report: Dict[str, Any], out: List[str]) -> None:
    cfg = report.get("latest_config") or {}
    if cfg:
        out.append("## On-disk local configuration overrides")
        if cfg.get("_config_source") == "mrsMThatcher.local.json":
            out.append(
                f"On-disk override source: `{cfg.get('_config_source_path')}`; "
                f"file timestamp: `{cfg.get('_config_source_time')}`."
            )
            out.append(
                "This read-only file observation does not establish which "
                "values are effective in a live process."
            )
        if cfg.get("_carried_forward"):
            out.append("Config source: carried forward from previous digest state.")
        elif cfg.get("_carried_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: backfilled from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous") and cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from previous digest state and earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous"):
            out.append("Config source: current window plus missing values from previous digest state.")
        keep = [
            "MAX_AUTO_REPLIES_PER_DAY", "MAX_REPLIES_PER_AUTHOR_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY", "MIN_SECONDS_BETWEEN_REPLIES",
            "REPLY_CHECK_EVERY_SECONDS", "MAX_MENTIONS_PER_CHECK", "MENTIONS_MAX_PAGES_PER_CHECK",
            "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
            "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
            "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
            "QUOTE_CHECK_EVERY_SECONDS", "QUOTE_LOOKUP_API_MAX_RESULTS", "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
            "QUOTE_CHECK_SPACING_RETRY_SECONDS", "ENABLE_HOT_POST_REPLY_CHECKS",
            "MAX_HOT_POST_REPLIES_PER_CHECK", "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
            "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
            "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
            "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
            "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
            "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
            "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS",
            "QUOTE_ANALYSIS_FILE", "IMAGE_ANALYSIS_FILE", "QUOTE_ANALYSIS_OVERRIDES_FILE",
            "IMAGE_STRONG_MISMATCH_PENALTY",
            "POST_SLEEP_MIN", "POST_SLEEP_MAX",
        ]
        out.append("```text")
        for k in keep:
            if k in cfg:
                out.append(f"{k}={cfg[k]}")
        out.append("```")
        out.append("")
    else:
        runtime_config_status = report.get("runtime_config_status") or {}
        out.append("## On-disk local configuration overrides")
        out.append(
            "On-disk local overrides: **unavailable** "
            f"(`{runtime_config_status.get('status') or 'not read'}`; "
            f"source `{runtime_config_status.get('path') or 'unavailable'}`)."
        )
        out.append(
            "Effective live configuration is not established; no digest resume "
            "snapshot or historical startup log is presented as current configuration."
        )
        out.append("")


def render_markdown(
    report: Dict[str, Any],
    *,
    main_post_receipt_lifecycle: Mapping[str, Any],
    reply_receipt_lifecycle: Mapping[str, Any],
) -> str:
    """Render a prepared report and its main-post and reply lifecycle summaries.

    The caller supplies empty mappings when there are no receipt events.
    Neither the report nor the lifecycle summaries are modified.
    """
    out: List[str] = []
    _render_overview(report, out)
    _render_latest_state(report, out)
    _render_mention_backlog(report, out)
    _render_retained_snapshots(report, out)
    _render_remote_write_safety(report, out)
    _render_remote_transactions(report, out)
    _render_media_upload(report, out)
    _render_meme_schedule(report, out)
    _render_reply_budget_and_priority(report, out)
    _render_openai_cost(report, out)
    _render_generated_images(report, out)
    _render_regular_image_usage(report, out)
    _render_original_editorial(report, out)
    _render_identity_policy(report, out)
    _render_identity_shadow(report, out)
    _render_historical_context_quality(report, out)
    _render_historical_corpus(report, out)
    _render_historical_engagement(report, out)
    _render_shadow_lifecycle(report, out)
    _render_single_call_replies(report, out)
    _render_pagination_warnings(report, out)
    _render_counts(report, out)
    _render_event_details(report, out)
    _render_main_post_recovery(report, out, main_post_receipt_lifecycle)
    _render_reply_recovery(report, out, reply_receipt_lifecycle)
    _render_asset_health(report, out)
    _render_api_health(report, out)
    _render_self_test_errors(report, out)
    _render_operational_errors(report, out)
    _render_warnings(report, out)
    _render_lifecycle(report, out)
    _render_config(report, out)
    return "\n".join(out)
