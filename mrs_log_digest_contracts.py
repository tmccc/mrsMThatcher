"""Checked shapes at the digest's parsed-observation and report boundaries.

Raw log JSON and saved cursor JSON remain dynamic until their existing readers
and validators have accepted the fields needed by an analysis operation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, NewType, Optional, Protocol, TypedDict, Union, cast, overload


# NewType preserves the exact reader-accepted dictionary and its identity.
# These are observed snapshots, not a full validated bot-state schema. The
# tags prevent accidentally wiring current bot state into a config slot.
RuntimeStateSnapshot = NewType("RuntimeStateSnapshot", dict[str, Any])
RuntimeConfigSnapshot = NewType("RuntimeConfigSnapshot", dict[str, Any])


class ReadStableSnapshot(Protocol):
    """Read one current file through the caller's bounded stable reader."""

    def __call__(self, path: Path, *, maximum: int) -> tuple[bytes, os.stat_result]:
        """Return bytes and metadata from the same stable file observation."""
        ...


class ParseNativeObject(Protocol):
    """Decode raw JSON while retaining the caller's labelled error boundary."""

    def __call__(self, data: bytes, *, label: str) -> dict[str, Any]:
        """Return a strictly parsed native-number JSON object."""
        ...


class _SourceReferenceBase(TypedDict):
    """Fields every parsed log source reference contains."""

    record_number: int
    timestamp: str
    logger: str


class SourceReference(_SourceReferenceBase, total=False):
    """Stable physical location retained after a log record is parsed."""

    input_file_index: int
    source_basename: str
    logged_source_line_number: int


class InputFileSummary(TypedDict):
    """Physical log-file coverage before resume filtering and deduplication."""

    path: str
    exists: bool
    size: Optional[int]
    mtime: Optional[str]
    total_records: int
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    records_after_since: int
    records_in_window: int


class ResumeContext(TypedDict):
    """Stable internal section saved through the existing cursor JSON keys."""

    active_xai_context: Optional[dict[str, Any]]
    active_xai_call_attempt: Optional[dict[str, Any]]
    pending_mention: Optional[dict[str, Any]]
    pending_qt: Optional[dict[str, Any]]


@dataclass(frozen=True)
class RestoredResumeContext:
    """Validated top-level cursor fields passed to one analysis invocation."""

    active_xai_context: Optional[dict[str, Any]] = None
    active_xai_call_attempt: Optional[dict[str, Any]] = None
    pending_mention: Optional[dict[str, Any]] = None
    pending_qt: Optional[dict[str, Any]] = None


class SummarySection(TypedDict):
    """Summary fields assembled by the core before optional overlays."""

    record_count: int
    time_start: Optional[str]
    time_end: Optional[str]
    headline: str
    _headline_without_current_cooldown: list[str]
    _headline_components: Mapping[str, Any]
    stats: dict[str, int]
    routine_skip_counts: dict[str, int]


class _MentionCurrentProgress(TypedDict, total=False):
    """Current strike projection appended after the runtime overlay."""

    current_author_no_reply_strike_progress: dict[str, Any]


class MentionBacklogSection(_MentionCurrentProgress):
    """Mention-control observations prepared from the selected records."""

    events: list[dict[str, Any]]
    event_counts: dict[str, int]
    pipeline_evaluations_skipped: int


class StructuredUnknownName(TypedDict):
    """One safely displayed EVENT name and its retained-record count."""

    name: str
    count: int


class StructuredEventDiagnosticsSection(TypedDict):
    """Bounded coverage observations for EVENT envelopes and future names."""

    unknown_count: int
    malformed_count: int
    unknown_names: list[StructuredUnknownName]
    unlisted_unknown_count: int
    malformed_reasons: dict[str, int]
    unknown_examples: list[SourceReference]
    malformed_examples: list[SourceReference]


class _RecoveryLifecycle(TypedDict, total=False):
    """Lifecycle summary added after receipt reconciliation."""

    receipt_lifecycle: dict[str, Any]


class MainPostRecoverySection(_RecoveryLifecycle):
    """Main-post receipt observations before lifecycle enrichment."""

    receipt_events: list[dict[str, Any]]
    confirmed_post_recovery: list[dict[str, Any]]


class ConfirmedReplyRecoverySection(_RecoveryLifecycle):
    """Reply receipt reconciliation prepared from historical/current evidence."""

    receipt_events: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    durably_reconciled_ambiguity_receipts: list[dict[str, Any]]
    status_unavailable_receipts: list[dict[str, Any]]
    active_snapshot_receipts: list[dict[str, Any]]


class RuntimeStateStatus(TypedDict):
    """Provenance of the current bot-state snapshot."""

    status: str
    path: str
    observed_at: str


class RuntimeConfigStatus(TypedDict):
    """Provenance of the current local-config snapshot."""

    status: str
    path: str
    time: Optional[str]


class ProviderRequestCoverage(TypedDict):
    """Coverage counts and historical gaps for exported provider requests."""

    logical_call_denominator: int
    physical_attempt_denominator: int
    category_counts: dict[str, int]
    historical_not_recorded_calls: list[dict[str, Any]]


class SingleCallCostTotal(TypedDict):
    """Published-cost overlay on the otherwise legacy single-call section."""

    status: str
    amount: object
    scope: object
    method: object


class SingleCallReplyCostSection(TypedDict, total=False):
    """Known cost field in the otherwise historical single-call section."""

    cost_total: SingleCallCostTotal


# NewType is an identity operation at runtime: the report remains the exact
# dictionary assembled by analysis. The nominal static boundary rejects
# unrelated mappings at run helpers; legacy sections remain dynamic.
DigestReport = NewType("DigestReport", dict[str, Any])

ReportSection = Union[
    SummarySection, ResumeContext, MentionBacklogSection, StructuredEventDiagnosticsSection, MainPostRecoverySection,
    ConfirmedReplyRecoverySection, RuntimeStateStatus, RuntimeConfigStatus,
    ProviderRequestCoverage, SingleCallReplyCostSection,
]


@overload
def report_section(report: DigestReport, name: Literal["summary"]) -> SummarySection:
    """Read the summary of an analysis-built report."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["resume_context"]) -> ResumeContext:
    """Read the resume context of an analysis-built report."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["mention_backlog_and_quarantine"]) -> MentionBacklogSection:
    """Read the mention section of an analysis-built report."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["structured_event_diagnostics"]) -> StructuredEventDiagnosticsSection:
    """Read bounded structured-envelope coverage diagnostics."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["main_post_recovery"]) -> MainPostRecoverySection:
    """Read main-post recovery of an analysis-built report."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["confirmed_reply_recovery"]) -> ConfirmedReplyRecoverySection:
    """Read reply recovery of an analysis-built report."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["runtime_state_status"]) -> RuntimeStateStatus:
    """Read current state provenance after its overlay."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["runtime_config_status"]) -> RuntimeConfigStatus:
    """Read current configuration provenance after its overlay."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["provider_request_coverage"]) -> ProviderRequestCoverage:
    """Read provider coverage after its overlay."""
    ...


@overload
def report_section(report: DigestReport, name: Literal["single_call_reply"]) -> SingleCallReplyCostSection:
    """Read the known cost field of the historical single-call section."""
    ...


def report_section(report: DigestReport, name: str) -> ReportSection:
    """Return the current known section of an analysis-built report, without copying.

    The sole cast connects the established build/overlay shape to typed reads.
    This is not validation of arbitrary dictionaries or a partial-report API.
    There is deliberately no overload for unknown extension section names.
    """
    return cast(ReportSection, report[name])
