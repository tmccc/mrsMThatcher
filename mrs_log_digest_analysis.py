"""Invocation-local mutable state for passive digest analysis.

Constructing these objects does not read files or observe the runtime. The
entry-point coordinator supplies records and invokes the specialist observers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mrs_log_digest_records import Record, ResumeWindowSelection
from mrs_log_digest_contracts import (
    DigestReport, InputFileSummary, RuntimeConfigSnapshot, RuntimeStateSnapshot,
)


@dataclass(frozen=True)
class DigestInputSelection:
    """Carry selected records and their physical resume boundary together."""

    logs: List[Path]
    since: Optional[datetime]
    until: Optional[datetime]
    since_source: Optional[str]
    since_exclusive: bool
    resume_boundary_counts: Counter[str]
    resume_data: Dict[str, Any]
    physical_records: List[Record]
    input_files: List[InputFileSummary]
    selection: ResumeWindowSelection

    @property
    def records(self) -> List[Record]:
        """Return records selected for analysis after physical cursor matching."""
        return self.selection.records


@dataclass(frozen=True)
class DigestCurrentSnapshots:
    """Keep current observations distinct from historical log-window evidence."""

    runtime_state: Optional[RuntimeStateSnapshot]
    runtime_state_path: Path
    runtime_state_ts: Optional[datetime]
    runtime_state_status: str
    runtime_state_observed_at: datetime
    runtime_config: Optional[RuntimeConfigSnapshot]
    runtime_config_path: Path
    runtime_config_ts: Optional[datetime]
    runtime_config_status: str
    confirmed_receipt_evidence: List[Dict[str, Any]]
    historical_history_evidence: List[Dict[str, Any]]
    durable_reply_evidence_status: Dict[str, Any]
    remote_write_safety: Dict[str, Any]


@dataclass
class AnalysisSourceContext:
    """Keep pending production and self-test observations independent."""

    pending_quote: Dict[str, Any] = field(default_factory=dict)
    pending_meme: Dict[str, Any] = field(default_factory=dict)
    pending_mention: Dict[str, Any] = field(default_factory=dict)
    pending_qt: Dict[str, Any] = field(default_factory=dict)
    pending_confirmed_reply_receipt: Dict[str, Any] = field(default_factory=dict)
    last_created_post: Dict[str, Any] = field(default_factory=dict)
    active_xai_context: Optional[Dict[str, Any]] = None
    active_xai_call_attempt_index: Optional[int] = None


@dataclass
class DigestAnalysisState:
    """Own collections and pending context for one digest analysis."""

    stats: Counter[str] = field(default_factory=Counter)
    events: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    self_test_errors: List[Dict[str, Any]] = field(default_factory=list)
    api_errors: List[Dict[str, Any]] = field(default_factory=list)
    handled_api_restrictions: List[Dict[str, Any]] = field(default_factory=list)
    receipt_events: List[Dict[str, Any]] = field(default_factory=list)
    confirmed_post_recovery: List[Dict[str, Any]] = field(default_factory=list)
    confirmed_reply_receipts: List[Dict[str, Any]] = field(default_factory=list)
    confirmed_reply_recovery: List[Dict[str, Any]] = field(default_factory=list)
    asset_health: List[Dict[str, Any]] = field(default_factory=list)
    reply_media_context: List[Dict[str, Any]] = field(default_factory=list)
    media_upload_incidents: List[Dict[str, Any]] = field(default_factory=list)
    remote_write_transactions: List[Dict[str, Any]] = field(default_factory=list)
    x_requests: List[Dict[str, Any]] = field(default_factory=list)
    latest_x_request_by_source: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    xai_usage_events: List[Dict[str, Any]] = field(default_factory=list)
    xai_call_attempts: List[Dict[str, Any]] = field(default_factory=list)
    xai_usage_parse_errors: List[Dict[str, Any]] = field(default_factory=list)
    regular_image_usage_events: List[Dict[str, Any]] = field(default_factory=list)
    original_editorial_shadow_events: List[Dict[str, Any]] = field(default_factory=list)
    pending_original_editorial_shadow_companions: Counter[str] = field(default_factory=Counter)
    generated_identity_shadow_events: List[Dict[str, Any]] = field(default_factory=list)
    generated_identity_policy_events: List[Dict[str, Any]] = field(default_factory=list)
    generated_image_spacing_events: List[Dict[str, Any]] = field(default_factory=list)
    latest_generated_image_spacing: Dict[str, Any] = field(default_factory=dict)
    cooldown_active: List[Dict[str, Any]] = field(default_factory=list)
    lifecycle: List[Dict[str, Any]] = field(default_factory=list)
    routine_skip_counts: Counter[str] = field(default_factory=Counter)
    configs: Dict[str, str] = field(default_factory=dict)
    latest_state: Optional[Dict[str, Any]] = None
    latest_state_ts: Optional[datetime] = None
    production_context: AnalysisSourceContext = field(default_factory=AnalysisSourceContext)
    selftest_context: AnalysisSourceContext = field(default_factory=AnalysisSourceContext)
    source_context: AnalysisSourceContext = field(init=False)
    structured_reply_confirmations: List[Dict[str, Any]] = field(default_factory=list)
    historical_reply_text_evidence: List[Dict[str, Any]] = field(default_factory=list)
    current_source_record: Optional[Record] = None
    msg: str = ""
    production_record: bool = False
    strict_structured_event_obj: Optional[Dict[str, Any]] = None
    is_reply_visual_description_event: bool = False
    is_asset_metadata_warning: bool = False
    is_handled_reply_restriction: bool = False
    production_event_object_ids: set[int] = field(default_factory=set)
    local_rejections_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    report: DigestReport = field(default_factory=lambda: DigestReport({}))

    def __post_init__(self) -> None:
        """Select production as the default context between record observations."""
        self.source_context = self.production_context
