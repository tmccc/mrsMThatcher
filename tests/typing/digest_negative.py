"""Intentional digest contract violations; checked, never executed."""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path

from mrs_log_digest import render_and_deliver_digest
from mrs_log_digest_analysis import AnalysisSourceContext, DigestCurrentSnapshots, DigestInputSelection
from mrs_log_digest_contracts import (
    ReadStableSnapshot, RestoredResumeContext, ResumeContext,
    RuntimeConfigSnapshot, SourceReference, SummarySection,
)
from mrs_log_digest_records import ResumeWindowSelection


bad_selection = ResumeWindowSelection([object()], "timestamp", 0, False)  # expect: list-item


def swapped_snapshot(snapshots: DigestCurrentSnapshots, config: RuntimeConfigSnapshot) -> None:
    """The current bot-state slot must reject current config."""
    replace(snapshots, runtime_state=config)  # expect: arg-type


bad_resume = RestoredResumeContext(active_xai_context="not a mapping")  # expect: arg-type
bad_persisted_resume: ResumeContext = {
    "active_xai_context": None, "active_xai_call_attempt": None,
    "pending_mention": "not a pending observation", "pending_qt": None,  # expect: typeddict-item
}
bad_source: SourceReference = {"record_number": "one", "timestamp": "now", "logger": "bot"}  # expect: typeddict-item
bad_summary: SummarySection = {
    "record_count": "one", "time_start": None, "time_end": None,  # expect: typeddict-item
    "headline": "", "_headline_without_current_cooldown": [],
    "_headline_components": {}, "stats": {}, "routine_skip_counts": {},
}
bad_context = AnalysisSourceContext(active_xai_call_attempt_index="first")  # expect: arg-type


def unrelated_mapping(inputs: DigestInputSelection, args: argparse.Namespace) -> None:
    """Delivery accepts an analysed digest report, not an arbitrary mapping."""
    render_and_deliver_digest({"summary": {}}, inputs, args)  # expect: arg-type


def wrong_reader(path: Path, max_bytes: int) -> tuple[bytes, os.stat_result]:
    """This callback cannot receive the required maximum keyword."""
    raise NotImplementedError


bad_reader: ReadStableSnapshot = wrong_reader  # expect: assignment
