"""In-memory shapes at the live state, reply-context and receipt boundaries.

These are descriptions of existing dictionaries.  JSON and provider input is
untrusted until the existing normalisers and receipt validators accept it.
Transaction proofs remain separate from these structural contracts.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict


ReplyLane = Literal["mention", "hot_post_reply", "quote_tweet"]
MainPostLane = Literal["quote_image", "daily_meme"]
ReplyRole = Literal["account", "user", "other_user"]
RawTweet: TypeAlias = dict[str, object]


class VisibleReplyTurn(TypedDict):
    """One bounded model-facing conversation turn."""

    post_id: str
    author_role: ReplyRole
    text: str


class ReplyContextData(TypedDict):
    """Context emitted by both verified normal and quote builders."""

    target_id: str
    thread_id: str
    root_post_id: str
    parent_post_id: str | None
    lane: str
    incoming_contribution: str
    quoted_post: VisibleReplyTurn | None
    quoted_post_id: str | None
    quoted_post_relationship: str | None
    parent_thread: list[VisibleReplyTurn]
    visible_conversation: list[VisibleReplyTurn]
    visual_description: str | None
    clarification_request: object | None
    current_date: str
    target_author_id: str
    target_created_at: str


class ReplyMediaContext(TypedDict):
    """The media owner's selected or unavailable photo context."""

    lane: str
    target_id: str
    mode: str
    status: str
    photos_expected: int
    photos: list[dict[str, object]]


class CurrentReplyDraftRequired(TypedDict):
    """Schema-four durable reply draft after full local revalidation."""

    draft_schema_version: Literal[4]
    strategy_version: str
    target_id: str
    target_author_id: str
    root_post_id: str
    parent_post_id: str | None
    candidate_source: ReplyLane
    incoming_contribution_sha256: str
    canonical_visible_context_sha256: str
    quoted_subject_sha256: str | None
    model_payload_sha256: str
    prompt_sha256: str
    response_schema_sha256: str
    model: str
    reasoning_effort: str
    temperature: None
    proposed_reply: str
    reply_kind: str
    reason_code: str
    trusted_fact_ids: list[str]
    used_fact_ids: list[str]
    factual_claims: list[object]
    time_context: object
    used_fact_sources: list[dict[str, str]]
    supplied_images: list[dict[str, object]]
    model_call_count: Literal[1]
    created_at: str
    validated_draft_hash: str


class CurrentReplyDraft(CurrentReplyDraftRequired, total=False):
    """Current draft, preserving absence of an optional provider call ID."""

    call_id: str


class LegacyTestedReplyDraft(TypedDict):
    """Guaranteed identity and decision fields of the frozen tested strategy."""

    schema_version: Literal[1]
    strategy_version: Literal["tested-reply-pipeline-20260817"]
    target_id: str
    thread_id: str
    candidate_source: ReplyLane
    proposed_reply: str
    mode: str
    final_reply_kind: str
    model_call_count: int


class LegacyAIFirstReplyDraft(TypedDict):
    """Guaranteed identity and decision fields of the frozen AI-first strategy."""

    schema_version: Literal[9]
    strategy_version: Literal["ai-first-reply-v3"]
    target_id: str
    thread_id: str
    candidate_source: ReplyLane
    proposed_reply: str
    mode: str
    model_call_count: int


class LegacySingleSolReplyDraft(TypedDict):
    """Shared validated fields of frozen single-Sol draft schema one to four."""

    draft_schema_version: Literal[1, 2, 3, 4]
    strategy_version: Literal["single-sol-reply-20260904"]
    target_id: str
    root_post_id: str
    candidate_source: ReplyLane
    proposed_reply: str
    reply_kind: str
    model_call_count: Literal[1]


HistoricalReplyDraft: TypeAlias = (
    LegacyTestedReplyDraft | LegacyAIFirstReplyDraft | LegacySingleSolReplyDraft
)


class ReceiptReplyContext(TypedDict):
    """The smaller context shape established by current receipt validation."""

    target_id: str
    thread_id: str
    lane: ReplyLane
    target_author_id: str


class MentionPagination(TypedDict):
    """Exact continuation provenance verified before preserving a receipt."""

    base_since_id: str
    next_token: str


class LegacyReceiptReplyContext(TypedDict):
    """Minimum context keys accepted during frozen legacy recovery."""

    target_id: str
    thread_id: str
    lane: ReplyLane


class BotStateRequired(TypedDict):
    """Keys supplied by default_state and retained after candidate normalisation.

    Values which have a specialised validator but no structural guarantee here
    remain object.  The normaliser also preserves unrecognised JSON keys.
    """

    minimum_reader_version: int
    last_seen_mention_id: str | None
    mention_pagination: dict[str, str]
    mention_backlog: dict[str, object]
    mention_backlog_reset_guard: dict[str, object]
    mention_pending_candidates: dict[str, dict[str, object]]
    author_evaluation_quarantines: dict[str, object]
    replied_to_ids: list[str]
    dry_run_seen_mention_ids: list[str]
    skipped_hot_reply_ids: list[str]
    skipped_hot_reply_records: dict[str, object]
    hot_post_reply_since_ids: dict[str, str]
    hot_post_reply_pagination_tokens: dict[str, str]
    hot_post_reply_check_counts: dict[str, int]
    daily_reply_date: str | None
    daily_reply_count: int
    daily_replied_author_ids: list[str]
    daily_replied_author_counts: dict[str, int]
    own_auto_reply_ids: list[str]
    tweet_cache: dict[str, dict[str, object]]
    ai_reply_history: list[dict[str, object]]
    posted_meme_filenames: list[str]
    last_meme_post_epoch: int
    next_meme_post_epoch: int
    meme_schedule_version: int
    next_meme_schedule_mode: object
    next_meme_schedule_date: object
    meme_anchor_quote_post_epoch: int
    last_reply_epoch: int
    last_reply_check_epoch: object
    next_reply_lane_priority: object
    last_main_post_id: str | None
    last_regular_image_filename: str | None
    last_quote_post_epoch: int
    next_quote_post_epoch: int
    recent_own_post_ids: list[str]
    seen_quote_post_ids: list[str]
    replied_to_quote_post_ids: list[str]
    skipped_quote_post_ids: list[str]
    quote_pending_candidates: dict[str, object]
    quote_lookup_pagination_tokens: dict[str, str]
    quote_search_pagination_tokens: dict[str, str]
    quote_lookup_repeated_cursor_suppressions: dict[str, dict[str, object]]
    quote_spam_author_ids: list[str]
    daily_quote_reply_date: str | None
    daily_quote_reply_count: int
    last_quote_tweet_check_epoch: object
    x_error_epochs: list[int]
    x_write_error_epochs: list[int]
    openai_error_epochs: list[int]
    api_cooldown_until_epoch: int
    api_cooldown_reason: object
    x_write_api_cooldown_until_epoch: int
    x_write_api_cooldown_reason: object
    openai_api_cooldown_until_epoch: int
    openai_api_cooldown_reason: object
    quote_x_error_epochs: list[int]
    quote_api_cooldown_until_epoch: int
    quote_api_cooldown_reason: object


class ReceiptCommitIdentity(TypedDict):
    """Validated persisted identity used to reprove receipt retirement."""

    quote_hash: str
    image_basename: str


class BotState(BotStateRequired, total=False):
    """Normalised live state with recognised keys absent from fresh defaults."""

    _confirmed_receipt_commits: dict[str, ReceiptCommitIdentity]
    pending_ai_reply_drafts: dict[str, object]
    reply_evaluation_records: dict[str, dict[str, object]]
    clarification_reply_records: dict[str, dict[str, object]]
    reply_strategy_history: list[dict[str, object]]
    original_regular_posts_since_generated_image: int


class ReplyReceiptCommon(TypedDict):
    """Fields required by the current receipt validator in both lifecycles."""

    schema_version: Literal[4]
    target_id: str
    author_id: str
    candidate_source: ReplyLane
    reply_text: str
    conversation_id: str
    reply_context: ReceiptReplyContext
    reply_epoch: int
    attempt_epoch: int
    daily_reply_date: str
    ai_reply_draft: CurrentReplyDraft


class SendingReplyReceipt(ReplyReceiptCommon):
    """Validated current sending receipt; there is no reply_post_id yet."""

    lifecycle_state: Literal["sending"]


class ConfirmedReplyReceipt(ReplyReceiptCommon):
    """Validated current confirmation; proof is still required for retirement."""

    lifecycle_state: Literal["confirmed"]
    reply_post_id: str
    confirmation_epoch: int


class HistoricalReplyReceipt(TypedDict):
    """Fields guaranteed by frozen schema-two/three receipt validation."""

    target_id: str
    author_id: str
    candidate_source: ReplyLane
    reply_text: str
    conversation_id: str
    reply_context: LegacyReceiptReplyContext
    reply_epoch: int
    ai_reply_draft: CurrentReplyDraft | HistoricalReplyDraft


class HistoricalSendingReplyReceipt(HistoricalReplyReceipt):
    """Schema-three pre-send receipt, including legacy draft recovery."""

    schema_version: Literal[3]
    lifecycle_state: Literal["sending"]


class HistoricalConfirmedReplyReceipt(HistoricalReplyReceipt):
    """Previously confirmed receipt retained for restart compatibility."""

    schema_version: Literal[2, 3]
    reply_post_id: str


class LegacyCurrentReplyCommon(TypedDict):
    """Schema-four fields shared by frozen legacy draft receipts."""

    schema_version: Literal[4]
    target_id: str
    author_id: str
    candidate_source: ReplyLane
    reply_text: str
    conversation_id: str
    reply_context: LegacyReceiptReplyContext
    reply_epoch: int
    attempt_epoch: int
    daily_reply_date: str
    ai_reply_draft: CurrentReplyDraft | HistoricalReplyDraft


class LegacyCurrentSendingReplyReceipt(LegacyCurrentReplyCommon):
    """Schema-four sending record accepted only with frozen legacy draft proof."""

    lifecycle_state: Literal["sending"]


class LegacyCurrentConfirmedReplyReceipt(LegacyCurrentReplyCommon):
    """Schema-four legacy confirmation; source lineage still governs removal."""

    lifecycle_state: Literal["confirmed"]
    reply_post_id: str
    confirmation_epoch: int


ReplyReceiptLoad: TypeAlias = (
    tuple[Literal["absent"], None]
    | tuple[Literal["invalid"], object]
    | tuple[Literal["sending"], SendingReplyReceipt | HistoricalSendingReplyReceipt]
    | tuple[Literal["legacy_sending"], LegacyCurrentSendingReplyReceipt | HistoricalSendingReplyReceipt]
    | tuple[Literal["valid"], ConfirmedReplyReceipt | HistoricalConfirmedReplyReceipt | LegacyCurrentConfirmedReplyReceipt]
)

ValidatedConfirmedReplyReceipt: TypeAlias = (
    ConfirmedReplyReceipt | HistoricalConfirmedReplyReceipt
    | LegacyCurrentConfirmedReplyReceipt
)


class MainPostAttemptCommon(TypedDict):
    """Values shared by current quote and meme attempt lifecycles."""

    schema_version: Literal[5]
    attempt_id: str
    attempt_epoch: int
    payload_revision: int
    payload_sha256: str
    text: str
    text_sha256: str
    media_ids: list[str]
    reply_to_id: str
    made_with_ai: bool


class BoundMemeScheduleState(TypedDict):
    """Snapshot of the six calendar inputs bound before a regular write."""

    last_meme_post_epoch: int
    next_meme_post_epoch: int
    meme_schedule_version: int
    next_meme_schedule_mode: str
    next_meme_schedule_date: str
    meme_anchor_quote_post_epoch: int


class QuoteSelectedIdentity(TypedDict):
    """Exact quote and image identity copied into a regular attempt."""

    quote_hash: str
    line_no: int
    source_line_number: int
    image_basename: str
    image_no: int


class MemeSelectedIdentity(TypedDict):
    """Exact image identity copied into a meme attempt."""

    meme_basename: str


class QuoteRecoveryPlan(TypedDict):
    """Current bound schedule and history projection for a regular post."""

    quote_delay_seconds: int
    meme_delay_seconds: int | None
    quote_history_after: list[str]
    image_history_after: list[str]
    meme_scheduling_enabled: bool
    meme_trigger_after_hour: int
    meme_schedule_version: int
    meme_schedule_before: BoundMemeScheduleState
    schedule_timezone: str


class MemeRecoveryPlan(TypedDict):
    """Current bound schedule projection for a daily meme."""

    next_schedule_mode: Literal["fallback"]
    meme_schedule_version: int
    fallback_hour: int
    fallback_minute: int
    image_summary: str
    schedule_timezone: str


class QuoteMainPostAttemptCommon(MainPostAttemptCommon):
    """Current regular attempt with its validated lane-specific plan."""

    lane: Literal["quote_image"]
    selected_identity: QuoteSelectedIdentity
    recovery_plan: QuoteRecoveryPlan


class MemeMainPostAttemptCommon(MainPostAttemptCommon):
    """Current meme attempt with its validated lane-specific plan."""

    lane: Literal["daily_meme"]
    selected_identity: MemeSelectedIdentity
    recovery_plan: MemeRecoveryPlan


class SendingQuoteMainPostAttempt(QuoteMainPostAttemptCommon):
    """Regular attempt before durable send authorisation is consumed."""

    lifecycle_state: Literal["sending"]


class AttemptingQuoteMainPostAttempt(QuoteMainPostAttemptCommon):
    """Regular attempt after durable send authorisation is consumed."""

    lifecycle_state: Literal["attempting"]


class SendingMemeMainPostAttempt(MemeMainPostAttemptCommon):
    """Meme attempt before durable send authorisation is consumed."""

    lifecycle_state: Literal["sending"]


class AttemptingMemeMainPostAttempt(MemeMainPostAttemptCommon):
    """Meme attempt after durable send authorisation is consumed."""

    lifecycle_state: Literal["attempting"]


SendingMainPostAttempt: TypeAlias = SendingQuoteMainPostAttempt | SendingMemeMainPostAttempt
AttemptingMainPostAttempt: TypeAlias = AttemptingQuoteMainPostAttempt | AttemptingMemeMainPostAttempt
MainPostAttempt: TypeAlias = SendingMainPostAttempt | AttemptingMainPostAttempt


class HistoricalMainPostAttempt(TypedDict):
    """Fields shared by the accepted older quote and meme attempt schemas."""

    schema_version: Literal[2, 3, 4]
    lifecycle_state: Literal["sending", "attempting"]
    lane: MainPostLane
    attempt_id: str
    attempt_epoch: int
    text: str
    media_ids: list[str]
    selected_identity: dict[str, object]
    recovery_plan: dict[str, object]


class PendingMainPostReceipt(TypedDict):
    """Confirmed remote main post still awaiting local schedule persistence."""

    schema_version: Literal[1]
    receipt_type: Literal["confirmed_pending_schedule"]
    post_id: str
    confirmation_epoch: int
    source_attempt: AttemptingMainPostAttempt
    image_summary: str


class RegularPostReceipt(TypedDict):
    """Fields guaranteed by a validated regular quote-image receipt."""

    schema_version: Literal[1, 2, 3]
    post_id: str
    quote_hash: str
    image_basename: str
    quote_post_epoch: int
    next_quote_post_epoch: int


class MemePostReceipt(TypedDict):
    """Fields guaranteed by a validated daily-meme receipt."""

    schema_version: Literal[1, 2]
    post_id: str
    meme_basename: str
    meme_post_epoch: int
    next_meme_post_epoch: int


class CurrentRegularPostReceipt(TypedDict):
    """Complete schema-three regular receipt materialized from a bound plan."""

    schema_version: Literal[3]
    post_id: str
    quote_hash: str
    line_no: int
    source_line_number: int
    text: str
    image_basename: str
    image_no: int
    quote_post_epoch: int
    next_quote_post_epoch: int
    next_meme_post_epoch: int
    meme_schedule_version: int
    next_meme_schedule_mode: str
    next_meme_schedule_date: str
    meme_anchor_quote_post_epoch: int
    meme_schedule_changed_by_quote: bool
    quote_history_after: list[str]
    image_history_after: list[str]
    attempt_id: str
    attempt_payload_sha256: str
    source_attempt: AttemptingQuoteMainPostAttempt
    source_attempt_sha256: str


class CurrentMemePostReceipt(TypedDict):
    """Complete schema-two meme receipt materialized from a bound plan."""

    schema_version: Literal[2]
    post_id: str
    meme_basename: str
    meme_post_epoch: int
    next_meme_post_epoch: int
    next_meme_schedule_date: str
    meme_schedule_version: int
    next_meme_schedule_mode: str
    text: str
    image_summary: str
    attempt_id: str
    attempt_payload_sha256: str
    source_attempt: AttemptingMemeMainPostAttempt
    source_attempt_sha256: str


RegularReceiptLoad: TypeAlias = (
    tuple[Literal["absent"], None]
    | tuple[Literal["invalid"], object]
    | tuple[Literal["sending"], MainPostAttempt | HistoricalMainPostAttempt]
    | tuple[Literal["pending_schedule"], PendingMainPostReceipt]
    | tuple[Literal["valid"], RegularPostReceipt]
)

MemeReceiptLoad: TypeAlias = (
    tuple[Literal["absent"], None]
    | tuple[Literal["invalid"], object]
    | tuple[Literal["sending"], MainPostAttempt | HistoricalMainPostAttempt]
    | tuple[Literal["pending_schedule"], PendingMainPostReceipt]
    | tuple[Literal["valid"], MemePostReceipt]
)
