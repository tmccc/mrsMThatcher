"""Engagement experiment opportunities, publication validation and state transitions.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def invalidate_engagement_question_experiment(
    state: dict,
    *,
    code: str,
    recorded_epoch: int,
    exception_class: str | None = None,
    authority_component: str | None = None,
    engagement_question_trial: Any,
    log_event: Any,
    save_state: Any,
) -> None:
    """Durably invalidate a started trial while ordinary posting continues."""

    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        log_event(
            "engagement_question_experiment_invalid",
            experiment_id=engagement_question_trial.EXPERIMENT_ID,
            reason=code,
            started=False,
        )
        return
    if experiment_state.get("status") in {"invalid", "completed"}:
        return
    engagement_question_trial.mark_experiment_invalid(
        experiment_state,
        code=code,
        recorded_epoch=recorded_epoch,
    )
    state["engagement_question_experiment"] = experiment_state
    save_state(state, durable=True)
    event_fields = {
        "experiment_id": engagement_question_trial.EXPERIMENT_ID,
        "plan_sha256": experiment_state["active_plan_sha256"],
        "reason": experiment_state["current_deferral_reason"]["code"],
        "started": True,
    }
    if exception_class is not None:
        event_fields["exception_class"] = exception_class
    if authority_component is not None:
        event_fields["authority_component"] = authority_component
    log_event("engagement_question_experiment_invalid", **event_fields)


def initialise_engagement_question_experiment(
    state: dict,
    *,
    current_epoch: int,
    engagement_question_experiment_enabled: Any,
    engagement_question_trial: Any,
    invalidate_engagement_question_experiment: Any,
    load_engagement_question_runtime_plan: Any,
    log: Any,
    log_event: Any,
    save_state: Any,
) -> tuple[dict | None, dict | None]:
    """Load/bind a configured plan or pause an already-started experiment."""

    raw_state = state.get("engagement_question_experiment")
    if not engagement_question_experiment_enabled and raw_state is None:
        # Source-default parity: no catalogue/plan read and no state creation.
        return None, None
    if isinstance(raw_state, dict) and raw_state.get("status") in {
        "completed",
        "invalid",
    }:
        return None, raw_state
    try:
        plan, _catalogue, _quote_text_by_id = load_engagement_question_runtime_plan()
    except Exception as exc:
        log.error(
            "Engagement-question plan is unavailable or invalid: %s",
            exc,
            exc_info=True,
        )
        invalidate_engagement_question_experiment(
            state,
            code="configured_plan_unavailable_or_invalid",
            recorded_epoch=current_epoch,
        )
        return None, state.get("engagement_question_experiment")

    experiment_state = raw_state if isinstance(raw_state, dict) else None
    if experiment_state is None:
        # The state and reservation begin only when the first pair is actually
        # started on a normal quote opportunity.
        return plan, None
    try:
        engagement_question_trial.validate_experiment_state(
            experiment_state,
            plan=plan,
        )
    except engagement_question_trial.ExperimentValidationError:
        invalidate_engagement_question_experiment(
            state,
            code="active_plan_binding_changed",
            recorded_epoch=current_epoch,
        )
        return None, state.get("engagement_question_experiment")

    if not engagement_question_experiment_enabled:
        changed = engagement_question_trial.set_experiment_paused(
            experiment_state,
            paused=True,
            plan=plan,
        )
        if changed:
            state["engagement_question_experiment"] = experiment_state
            save_state(state, durable=True)
            log_event(
                "engagement_question_experiment_paused",
                experiment_id=experiment_state["experiment_id"],
                plan_sha256=experiment_state["active_plan_sha256"],
                completed_pairs=experiment_state["completed_pair_count"],
            )
        return plan, experiment_state
    if experiment_state.get("status") == "paused":
        engagement_question_trial.set_experiment_paused(
            experiment_state,
            paused=False,
            plan=plan,
        )
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
    return plan, experiment_state


def engagement_question_opportunity(
    state: dict,
    *,
    current_epoch: int,
    engagement_question_experiment_enabled: Any,
    engagement_question_trial: Any,
    initialise_engagement_question_experiment: Any,
    log_event: Any,
    save_state: Any,
) -> tuple[dict | None, dict | None, set[str]]:
    """Return the exact planned member and reservations for this opportunity."""

    plan, experiment_state = initialise_engagement_question_experiment(
        state,
        current_epoch=current_epoch,
    )
    if plan is None:
        return None, None, set()
    if not engagement_question_experiment_enabled:
        reserved = engagement_question_trial.reserved_quote_ids(
            plan,
            experiment_state,
        )
        return plan, None, reserved
    if experiment_state is None:
        experiment_state = engagement_question_trial.new_experiment_state(plan)
        engagement_question_trial.start_next_pair(experiment_state, plan)
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
        log_event(
            "engagement_question_experiment_pair_started",
            experiment_id=experiment_state["experiment_id"],
            plan_sha256=experiment_state["active_plan_sha256"],
            pair_id=experiment_state["active_pair_id"],
            pair_index=experiment_state["current_pair_index"],
        )
    member, reason = engagement_question_trial.member_for_current_opportunity(
        plan,
        experiment_state,
        current_epoch=current_epoch,
    )
    if reason == "pair_start_required":
        engagement_question_trial.start_next_pair(experiment_state, plan)
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
        log_event(
            "engagement_question_experiment_pair_started",
            experiment_id=experiment_state["experiment_id"],
            plan_sha256=experiment_state["active_plan_sha256"],
            pair_id=experiment_state["active_pair_id"],
            pair_index=experiment_state["current_pair_index"],
        )
        member, reason = engagement_question_trial.member_for_current_opportunity(
            plan,
            experiment_state,
            current_epoch=current_epoch,
        )
    if reason is not None:
        member = None
    reserved = engagement_question_trial.reserved_quote_ids(
        plan,
        experiment_state,
    )
    return plan, member, reserved


def resolve_engagement_question_quote_choice(
    member: dict,
    *,
    catalogue: dict,
    HISTORICAL_CONTEXT_RESEARCH_DIR: Any,
    _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT: Any,
    completed_research_quote_hashes: Any,
    engagement_question_trial: Any,
    historical_context_reply: Any,
    load_quote_lines_and_analysis: Any,
    quote_candidate_weight: Any,
    quote_metadata_for_hash: Any,
    quote_text_hash: Any,
) -> tuple[dict, str]:
    """Re-resolve and validate one exact planned canonical quotation."""

    lines, quote_analysis, today_mm_dd = load_quote_lines_and_analysis()
    quote_id = str(member["quote_id"])
    found: tuple[int, str] | None = None
    for line_no, source_line in enumerate(lines):
        exact_text = source_line[:-1] if source_line.endswith("\n") else source_line
        if exact_text.endswith("\r"):
            exact_text = exact_text[:-1]
        if engagement_question_trial.sha256_text(exact_text) == quote_id:
            found = (line_no, exact_text)
            break
    if found is None:
        raise engagement_question_trial.ExperimentValidationError(
            "planned canonical quotation is no longer present"
        )
    line_no, exact_text = found
    if quote_text_hash(exact_text) != quote_id:
        raise engagement_question_trial.ExperimentValidationError(
            "planned canonical identity no longer matches production"
        )
    if quote_id not in completed_research_quote_hashes():
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is no longer runtime attribution eligible"
        )
    from historical_context_formatter import (
        format_context_reply_public,
        load_and_validate_corpus,
        packet_for_posted_quote,
        packet_is_attributed_to_margaret_thatcher,
    )

    if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
        packets, unresolved = load_and_validate_corpus(
            HISTORICAL_CONTEXT_RESEARCH_DIR,
            require_source_role_audit=True,
        )
    else:
        packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    packet = packet_for_posted_quote(
        packets,
        unresolved,
        quote_id,
        exact_text,
    )
    formatted = (
        format_context_reply_public(
            packet,
            maximum_length=int(historical_context_reply["maximum_length"]),
            include_meaning=bool(historical_context_reply["include_meaning"]),
            include_source=bool(historical_context_reply["include_source"]),
            include_verification=bool(
                historical_context_reply["include_verification"]
            ),
        )
        if packet is not None
        and packet_is_attributed_to_margaret_thatcher(packet)
        else None
    )
    if (
        not isinstance(formatted, dict)
        or formatted.get("rendering_mode") != "public"
        or formatted.get("verification_label") != member["verification_label"]
        or formatted.get("source_class") != member["source_class"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned historical context is no longer publicly renderable"
        )
    analysis = quote_metadata_for_hash(quote_analysis, quote_id, exact_text)
    if analysis is None:
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation analysis is unavailable"
        )
    topics = analysis.get("primary_topics")
    if (
        not isinstance(topics, list)
        or not topics
        or topics[0] != member["topic"]
        or engagement_question_trial.quotation_length_band(exact_text)
        != member["quotation_length_band"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation matching metadata changed"
        )
    weight, season_status = quote_candidate_weight(
        analysis,
        today_mm_dd=today_mm_dd,
    )
    if weight <= 0:
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is currently hard-seasonally excluded"
        )
    entry = catalogue["entries"].get(quote_id)
    if not isinstance(entry, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is outside the approved catalogue"
        )
    public_text = engagement_question_trial.validate_complete_public_text(
        exact_quote_text=exact_text,
        catalogue_entry=entry,
        arm=str(member["arm"]),
    )
    if (
        entry["question_body"] != member["approved_question_body"]
        or entry["question_sha256"] != member["approved_question_sha256"]
        or entry["complete_treatment_sha256"]
        != member["complete_treatment_sha256"]
        or entry["complete_treatment_weighted_length"]
        != member["complete_treatment_weighted_length"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned approved question metadata changed"
        )
    return (
        {
            "line_no": line_no,
            "text": exact_text,
            "quote_hash": quote_id,
            "analysis": analysis,
            "weight": weight,
            "season_status": season_status,
        },
        public_text,
    )


def revalidate_engagement_question_publication_authority(
    *,
    state: dict,
    lines_used: set,
    envelope: dict,
    quote_choice: dict,
    public_text: str,
    engagement_experiment_attempt_envelope_is_valid: Any,
    engagement_question_trial: Any,
    load_engagement_question_runtime_plan: Any,
    resolve_engagement_question_quote_choice: Any,
) -> None:
    """Re-resolve one prepared trial member at the remote-write handoff."""

    plan, catalogue, _quote_text_by_id = load_engagement_question_runtime_plan()
    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication lost protected state"
        )
    engagement_question_trial.validate_experiment_state(
        experiment_state,
        plan=plan,
    )
    binding = envelope.get("binding")
    if not isinstance(binding, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication lost its binding"
        )
    pair_index = binding.get("pair_index")
    member_position = binding.get("member_position")
    if (
        type(pair_index) is not int
        or member_position not in {1, 2}
        or experiment_state.get("status") != "active"
        or experiment_state.get("active_pair_id") != binding.get("pair_id")
        or experiment_state.get("current_pair_index") != pair_index
        or experiment_state.get("next_pair_member_position") != member_position
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication state authority changed"
        )
    try:
        pair = plan["pairs"][pair_index]
        plan_member = pair["members"][member_position - 1]
        member = {
            **dict(plan_member),
            "pair_index": pair_index,
            "pair_id": str(pair["pair_id"]),
            "topic": str(pair["topic"]),
            "quotation_length_band": str(pair["quotation_length_band"]),
            "publication_order": str(pair["planned_publication_order"]),
        }
    except (IndexError, KeyError, TypeError) as exc:
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication member is unavailable"
        ) from exc
    quote_id = str(member.get("quote_id") or "")
    if quote_id in lines_used:
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental quotation entered used history"
        )
    current_choice, current_public_text = resolve_engagement_question_quote_choice(
        member,
        catalogue=catalogue,
    )
    if (
        current_public_text != public_text
        or current_choice.get("quote_hash") != quote_choice.get("quote_hash")
        or current_choice.get("line_no") != quote_choice.get("line_no")
        or current_choice.get("text") != quote_choice.get("text")
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental quotation or payload changed"
        )
    rebuilt_binding = engagement_question_trial.build_attempt_binding(
        plan=plan,
        state=experiment_state,
        member=member,
        exact_quote_text=str(current_choice["text"]),
        public_text=current_public_text,
    )
    rebuilt_envelope = {
        "binding": rebuilt_binding,
        "canonical_quote_text": str(current_choice["text"]),
        "approved_question_body": str(member["approved_question_body"]),
        "complete_treatment_sha256": str(
            member["complete_treatment_sha256"]
        ),
        "complete_treatment_weighted_length": int(
            member["complete_treatment_weighted_length"]
        ),
    }
    if rebuilt_envelope != envelope or not engagement_experiment_attempt_envelope_is_valid(
        envelope,
        public_text=public_text,
        quote_hash=quote_id,
        plan=plan,
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication authority changed"
        )


def engagement_question_authority_failure_diagnostic(
    exc: BaseException,
    *,
    ENGAGEMENT_QUESTION_MEMBER_AUTHORITY_KEYS: Any,
    json: Any,
    re: Any,
) -> tuple[str, str]:
    """Return bounded, non-sensitive structured handoff failure detail."""

    raw_class = type(exc).__name__
    exception_class = (
        raw_class
        if len(raw_class) <= 80 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", raw_class)
        else "Exception"
    )
    message_arg = exc.args[0] if exc.args and type(exc.args[0]) is str else ""
    message = message_arg[:240].casefold()
    if isinstance(exc, KeyError):
        missing_key = (
            exc.args[0]
            if len(exc.args) == 1 and type(exc.args[0]) is str
            else None
        )
        component = (
            "member_metadata"
            if missing_key in ENGAGEMENT_QUESTION_MEMBER_AUTHORITY_KEYS
            else "authority_revalidation"
        )
    elif "used histor" in message:
        component = "used_history"
    elif "catalogue" in message:
        component = "catalogue"
    elif "plan" in message:
        component = "plan"
    elif "state" in message:
        component = "protected_state"
    elif "binding" in message:
        component = "publication_binding"
    elif "payload" in message or "public text" in message:
        component = "public_payload"
    elif "quotation" in message or "member" in message:
        component = "publication_member"
    elif isinstance(exc, (OSError, UnicodeError, json.JSONDecodeError)):
        component = "authority_input"
    else:
        component = "authority_revalidation"
    return exception_class, component


def revalidate_or_invalidate_engagement_question_publication(
    *,
    state: dict,
    lines_used: set,
    envelope: dict,
    quote_choice: dict,
    public_text: str,
    engagement_question_authority_failure_diagnostic: Any,
    engagement_question_trial: Any,
    invalidate_engagement_question_experiment: Any,
    log: Any,
    now_epoch: Any,
    revalidate_engagement_question_publication_authority: Any,
) -> None:
    """Fail closed and invalidate a trial whose prepared authority changed."""

    try:
        revalidate_engagement_question_publication_authority(
            state=state,
            lines_used=lines_used,
            envelope=envelope,
            quote_choice=quote_choice,
            public_text=public_text,
        )
    except Exception as exc:
        log.error(
            "Experimental publication authority changed at remote-write handoff",
            exc_info=True,
        )
        exception_class, authority_component = (
            engagement_question_authority_failure_diagnostic(exc)
        )
        invalidate_engagement_question_experiment(
            state,
            code="pre_write_authority_changed",
            recorded_epoch=now_epoch(),
            exception_class=exception_class,
            authority_component=authority_component,
        )
        raise engagement_question_trial.ExperimentValidationError(
            "experimental publication authority changed before remote root posting"
        ) from exc


def defer_engagement_question_member(
    state: dict,
    *,
    code: str,
    recorded_epoch: int,
    engagement_question_trial: Any,
    log_event: Any,
    save_state: Any,
) -> None:
    """Durably record a bounded member deferral without advancing it."""

    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        raise RuntimeError("cannot defer an experiment before it starts")
    engagement_question_trial.record_deferral(
        experiment_state,
        code=code,
        recorded_epoch=recorded_epoch,
    )
    state["engagement_question_experiment"] = experiment_state
    save_state(state, durable=True)
    reason = experiment_state["current_deferral_reason"]
    log_event(
        "engagement_question_experimental_member_deferred",
        experiment_id=experiment_state["experiment_id"],
        plan_sha256=experiment_state["active_plan_sha256"],
        pair_id=reason["pair_id"],
        member_position=reason["member_position"],
        reason=reason["code"],
    )
