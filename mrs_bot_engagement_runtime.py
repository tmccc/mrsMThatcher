"""Engagement experiment runtime inputs and local notifications.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def engagement_experiment_envelope_from_receipt(
    receipt: object,
) -> dict | None:
    """Return the experiment envelope from one validated regular receipt."""

    if not isinstance(receipt, dict) or receipt.get("schema_version") != 4:
        return None
    value = receipt.get("engagement_question_experiment")
    return value if isinstance(value, dict) else None


def configured_engagement_question_path(
    raw_path: str,
    *,
    BASE_DIR: Any,
    Path: Any,
) -> Path:
    """Resolve one deployment-local experiment path without writing it."""

    path = Path(raw_path)
    return path if path.is_absolute() else BASE_DIR / path


def current_exact_quote_text_by_sha256(
    *,
    LINES_FILE: Any,
    engagement_question_trial: Any,
) -> dict[str, str]:
    """Load exact quotation bodies without production-text normalisation."""

    source = LINES_FILE.read_bytes().decode("utf-8", errors="strict")
    if "\r" in source:
        raise RuntimeError(
            "engagement experiment requires an LF-only canonical quotation source"
        )
    result: dict[str, str] = {}
    for exact_text in source.split("\n"):
        if not exact_text:
            continue
        quote_id = engagement_question_trial.sha256_text(exact_text)
        if quote_id in result and result[quote_id] != exact_text:
            raise RuntimeError("canonical quotation SHA-256 collision")
        result[quote_id] = exact_text
    return result


def load_engagement_question_runtime_plan(
    *,
    BASE_DIR: Any,
    _get_engagement_question_last_loaded_plan_sha256: Any,
    _set_engagement_question_last_loaded_plan_sha256: Any,
    configured_engagement_question_path: Any,
    current_exact_quote_text_by_sha256: Any,
    engagement_question_experiment_plan_path: Any,
    engagement_question_trial: Any,
    load_receipt_json_no_follow: Any,
    log_event: Any,
) -> tuple[dict, dict, dict[str, str]]:
    """Load and fully validate the immutable mode-0600 live plan."""

    plan_path = configured_engagement_question_path(
        engagement_question_experiment_plan_path
    )
    present, plan_document = load_receipt_json_no_follow(plan_path)
    if not present or not isinstance(plan_document, dict):
        raise RuntimeError(f"engagement experiment plan unavailable: {plan_path}")
    quote_text_by_id = current_exact_quote_text_by_sha256()
    catalogue_path = (
        BASE_DIR
        / "engagement_question_experiment"
        / "approved_question_catalogue.json"
    )
    catalogue, catalogue_sha256 = engagement_question_trial.load_approved_catalogue(
        catalogue_path,
        quote_text_by_id,
    )
    plan = engagement_question_trial.validate_plan_document(
        plan_document,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256,
        quote_text_by_id=quote_text_by_id,
        require_plan_kind="live",
    )
    if _get_engagement_question_last_loaded_plan_sha256() != plan["plan_sha256"]:
        log_event(
            "engagement_question_experiment_plan_loaded",
            experiment_id=plan["experiment_id"],
            plan_sha256=plan["plan_sha256"],
            pair_count=plan["pair_count"],
        )
        _set_engagement_question_last_loaded_plan_sha256(plan["plan_sha256"])
    return plan, catalogue, quote_text_by_id


def publish_pending_engagement_question_notification(
    state: dict,
    *,
    ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS: Any,
    _get_engagement_question_last_notification_failure_post_id: Any,
    _set_engagement_question_last_notification_failure_post_id: Any,
    atomic_write_json: Any,
    configured_engagement_question_path: Any,
    copy: Any,
    engagement_question_notification_output_path: Any,
    engagement_question_trial: Any,
    json_file_matches: Any,
    log: Any,
    log_event: Any,
    now_epoch: Any,
    os: Any,
    save_state: Any,
    stat: Any,
) -> bool:
    """Atomically publish the oldest pending treatment observation."""

    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        return False
    output_setting = engagement_question_notification_output_path
    if not output_setting:
        return False
    post_id = ""
    try:
        identity = engagement_question_trial.pending_treatment_notification(
            experiment_state
        )
        if identity is None:
            return False
        post_id = str(identity.get("post_id") or "")
        document = engagement_question_trial.validate_notification_document(
            copy.deepcopy(identity["document"])
        )
        if (
            engagement_question_trial.canonical_sha256(document)
            != identity["document_sha256"]
        ):
            raise RuntimeError("pending treatment notification identity changed")
        output_path = configured_engagement_question_path(output_setting)
        output_already_matches = json_file_matches(output_path, document)
        if not output_already_matches:
            try:
                output_metadata = os.lstat(output_path)
            except FileNotFoundError:
                output_metadata = None
            if output_metadata is not None:
                if not stat.S_ISREG(output_metadata.st_mode):
                    raise RuntimeError(
                        "treatment notification output is not a regular file"
                    )
                output_age_seconds = now_epoch() - output_metadata.st_mtime
                if (
                    output_age_seconds
                    < ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS
                ):
                    # Home Assistant polls this single-document file every 30
                    # seconds.  Preserve each queued post for two complete poll
                    # intervals before replacing it with the next identity.
                    return False
            atomic_write_json(output_path, document, durable=True)
        if not json_file_matches(output_path, document):
            raise RuntimeError("treatment notification output verification failed")
        state_before_delivery = copy.deepcopy(experiment_state)
        try:
            if not engagement_question_trial.mark_notification_delivered(
                experiment_state,
                post_id,
            ):
                raise RuntimeError(
                    "pending treatment notification was not marked delivered"
                )
            state["engagement_question_experiment"] = experiment_state
            save_state(state, durable=True)
        except Exception:
            experiment_state.clear()
            experiment_state.update(state_before_delivery)
            raise
        _set_engagement_question_last_notification_failure_post_id(None)
        return True
    except Exception:
        log.error(
            "Confirmed treatment notification write failed for post_id=%s",
            post_id,
            exc_info=True,
        )
        if _get_engagement_question_last_notification_failure_post_id() != post_id:
            log_event(
                "engagement_question_treatment_notification_write_failed",
                experiment_id=engagement_question_trial.EXPERIMENT_ID,
                post_id=post_id or None,
            )
            _set_engagement_question_last_notification_failure_post_id(post_id)
        return False
