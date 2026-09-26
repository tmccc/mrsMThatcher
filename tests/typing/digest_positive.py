"""Valid uses of the production digest contracts; checked, never executed."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from mrs_log_digest import (
    analyse, commit_digest_cursor, render_and_deliver_digest,
    load_current_runtime_config, load_current_runtime_state,
    read_stable_regular_snapshot,
)
from mrs_log_digest_analysis import DigestCurrentSnapshots, DigestInputSelection
from mrs_log_digest_contracts import (
    InputFileSummary, ReadStableSnapshot, RestoredResumeContext,
    SourceReference, report_section,
)
from mrs_log_digest_records import Record, ResumeWindowSelection


reader: ReadStableSnapshot = read_stable_regular_snapshot
source: SourceReference = {"record_number": 1, "timestamp": "2026-09-25 12:00:00", "logger": "bot"}


def valid_transaction(args: argparse.Namespace, path: Path) -> None:
    """Carry one parsed record and one report through delivery and cursor save."""
    record = Record(datetime(2026, 9, 25), "INFO", "bot", 1, "message", str(path), 1)
    file_summary: InputFileSummary = {
        "path": str(path), "exists": True, "size": 12, "mtime": None,
        "total_records": 1, "first_timestamp": None, "last_timestamp": None,
        "records_after_since": 1, "records_in_window": 1,
    }
    selected = ResumeWindowSelection([record], "timestamp", 0, False)
    inputs = DigestInputSelection(
        [path], None, None, None, False, Counter(), {}, [record],
        [file_summary], selected,
    )
    state, state_path, state_ts, state_status = load_current_runtime_state(path)
    config, config_path, config_ts, config_status = load_current_runtime_config(path)
    snapshots = DigestCurrentSnapshots(
        state, state_path, state_ts, state_status, datetime(2026, 9, 25),
        config, config_path, config_ts, config_status, [], [], {}, {},
    )
    resumed = RestoredResumeContext(pending_mention={"considered_seq": -1})
    report = analyse(inputs.records, initial_pending_mention=resumed.pending_mention,
                     current_runtime_state=snapshots.runtime_state)
    summary = report_section(report, "summary")
    summary["record_count"] += 1
    resume = report_section(report, "resume_context")
    resume["pending_mention"] = resumed.pending_mention
    report_section(report, "main_post_recovery")["receipt_events"].append({})
    report_section(report, "mention_backlog_and_quarantine")[
        "current_author_no_reply_strike_progress"
    ] = {"available": False}
    report_section(report, "single_call_reply")["cost_total"] = {
        "status": "unavailable", "amount": None, "scope": None, "method": None,
    }
    render_and_deliver_digest(report, inputs, args)
    commit_digest_cursor(report, inputs, args, path)
