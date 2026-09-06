"""Global remote-write barrier checks.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def unresolved_conversational_reply_receipt_is_blocking(
    *,
    load_confirmed_reply_receipt: Any,
) -> bool:
    """Return whether a reply receipt forbids another remote write."""
    status, _receipt = load_confirmed_reply_receipt()
    return status != "absent"


def unresolved_main_post_attempt_is_blocking(
    *,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
) -> bool:
    """Return whether a main-post attempt forbids another remote write."""
    regular_status, _regular = load_regular_post_receipt()
    meme_status, _meme = load_meme_post_receipt()
    return regular_status != "absent" or meme_status != "absent"


def remote_write_safety_incident_is_latched(
    *,
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN: Any,
    _AMBIGUOUS_REMOTE_POST_SEEN: Any,
) -> bool:
    """Return whether process memory requires every remote operation to stop."""
    return (
        _AMBIGUOUS_REMOTE_POST_SEEN
        or _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    )


def remote_write_safety_protocol_is_active(
    *,
    ProtocolActivationError: Any,
    REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE: Any,
    inspect_protocol_activation: Any,
    remote_receipt_retirement_is_blocking: Any,
) -> bool:
    """Return whether the exact restart-persistent protocol is activated.

    Absence, malformed bytes, unsafe metadata and inspection failure all mean
    that no remote-write lane may open.  This deliberately durable negative
    condition survives arbitrary process loss without relying on Python
    globals or an ambiguity marker which another process could remove.
    """
    try:
        inspect_protocol_activation(
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
        )
    except (ProtocolActivationError, OSError):
        return False
    return not remote_receipt_retirement_is_blocking()


def historical_context_receipt_path_present_or_unsafe(
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    log: Any,
    os: Any,
) -> bool:
    """Treat any historical-context receipt namespace entry as blocking."""

    try:
        os.lstat(HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE)
    except FileNotFoundError:
        return False
    except Exception:
        log.critical(
            "The historical-context receipt namespace cannot be inspected; "
            "treating every remote write as blocked",
            exc_info=True,
        )
        return True
    return True


def historical_context_outbox_remote_attempt_is_blocking(
    *,
    prepared_receipt: dict | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
    allow_local_reconciliation_parent_id: str | None = None,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    Path: Any,
    hashlib: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    log: Any,
) -> bool:
    """Treat every possibly transmitted outbox attempt as a global barrier.

    The outbox can outlive a lost source receipt and transport journal.  An
    explicit ``False`` phase proves only local pre-transport work; ``True`` or
    an absent legacy phase means the remote create may have started and must
    block every unrelated remote-write lane until reconciliation.
    """

    try:
        snapshot = historical_context_outbox_store().snapshot()
        obligations = snapshot.get("obligations")
        if not isinstance(obligations, dict):
            raise RuntimeError("historical-context outbox has no obligations map")
        exact_prepared_identity: dict[str, object] | None = None
        exact_prepared_attempt_required = bool(
            prepared_transport_authority is not None
            and prepared_transport_authority.lane == "historical_context_reply"
            and prepared_transport_authority.lifecycle_state == "attempting"
        )
        exact_prepared_attempt_observed = False
        if (
            prepared_transport_authority is not None
            and prepared_transport_authority.lane == "historical_context_reply"
            and prepared_transport_authority.lifecycle_state == "attempting"
            and Path(prepared_transport_authority.journal_path).absolute()
            == journal_path_for_receipt(
                HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            ).absolute()
        ):
            from historical_context_formatter import HistoricalContextReplyStore

            state = inspect_transport_state(
                Path(prepared_transport_authority.journal_path)
            )
            loaded = historical_context_reply_store()._load_receipt_safely()
            if (
                not state.errors
                and state.classification == "attempting_pair"
                and state.journal is not None
                and state.fence is not None
                and state.journal.sha256
                == prepared_transport_authority.journal_sha256
                and state.fence.sha256
                == prepared_transport_authority.fence_sha256
                and state.journal.document.get("transaction_id")
                == prepared_transport_authority.transaction_id
                and state.fence.document.get("transaction_id")
                == prepared_transport_authority.transaction_id
                and state.journal.document.get("source_receipt")
                == state.fence.document.get("source_receipt")
                and isinstance(loaded, tuple)
                and len(loaded) == 2
            ):
                receipt, receipt_bytes = loaded
                source = state.journal.document.get("source_receipt")
                receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
                if (
                    isinstance(receipt, dict)
                    and type(receipt_bytes) is bytes
                    and isinstance(source, dict)
                    and HistoricalContextReplyStore._valid_sending_receipt(
                        receipt
                    )
                    and (
                        prepared_receipt is None
                        or receipt == prepared_receipt
                    )
                    and source.get("basename")
                    == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.name
                    == prepared_transport_authority.source_receipt_basename
                    and source.get("sha256") == receipt_sha256
                ):
                    exact_prepared_identity = {
                        "parent_post_id": receipt.get("parent_post_id"),
                        "quote_id": receipt.get("quote_id"),
                        "source_receipt_sha256": receipt_sha256,
                        "source_receipt_attempt_number": receipt.get(
                            "attempt_number"
                        ),
                    }
        for obligation in obligations.values():
            context_reply = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if (
                isinstance(context_reply, dict)
                and context_reply.get("state") == "context_reply_attempting"
                and context_reply.get("remote_transaction_started") is not False
            ):
                if (
                    context_reply.get("remote_transaction_started") is True
                    and exact_prepared_identity is not None
                    and obligation.get("parent_post_id")
                    == exact_prepared_identity["parent_post_id"]
                    and context_reply.get("quote_id")
                    == exact_prepared_identity["quote_id"]
                    and context_reply.get("source_receipt_sha256")
                    == exact_prepared_identity["source_receipt_sha256"]
                    and context_reply.get("source_receipt_attempt_number")
                    == exact_prepared_identity[
                        "source_receipt_attempt_number"
                    ]
                ):
                    # The ordinary checks below still prove the exact source
                    # receipt and armed journal pair. This exception is only
                    # for that one currently executing context attempt.
                    exact_prepared_attempt_observed = True
                    continue
                if (
                    allow_local_reconciliation_parent_id is not None
                    and obligation.get("parent_post_id")
                    == str(allow_local_reconciliation_parent_id)
                ):
                    # The outbox worker may inspect and reconcile this one
                    # already-attempting parent while every remote boundary
                    # remains barred. Any second risky row still blocks entry.
                    continue
                return True
        return bool(
            exact_prepared_attempt_required
            and not exact_prepared_attempt_observed
        )
    except Exception:
        log.critical(
            "The historical-context outbox cannot prove that no remote-started "
            "attempt remains; treating every remote write as blocked",
            exc_info=True,
        )
        return True


def historical_context_outbox_remote_attempt_parent_for_local_reconciliation(
    *,
    historical_context_outbox_store: Any,
) -> str | None:
    """Return the sole risky outbox parent eligible for local-only recovery."""

    try:
        snapshot = historical_context_outbox_store().snapshot()
        obligations = snapshot.get("obligations")
        if not isinstance(obligations, dict):
            return None
        parents = [
            str(obligation.get("parent_post_id"))
            for obligation in obligations.values()
            if isinstance(obligation, dict)
            and isinstance(obligation.get("context_reply"), dict)
            and obligation["context_reply"].get("state")
            == "context_reply_attempting"
            and obligation["context_reply"].get("remote_transaction_started")
            is not False
            and str(obligation.get("parent_post_id") or "")
        ]
    except Exception:
        return None
    return parents[0] if len(parents) == 1 else None


def historical_context_receipt_parent_for_local_reconciliation(
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
) -> str | None:
    """Return the parent bound by a stable no-follow transaction receipt."""

    from historical_context_formatter import HistoricalContextReplyStore

    return HistoricalContextReplyStore.receipt_parent_for_safe_local_reconciliation(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )


def exact_historical_context_sending_receipt_matches(
    receipt: dict | None,
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    json: Any,
    os: Any,
    stat: Any,
) -> bool:
    """Match the owning sending receipt by schema, identity and exact bytes."""

    if receipt is None:
        return False
    from historical_context_formatter import HistoricalContextReplyStore

    if not HistoricalContextReplyStore._valid_sending_receipt(receipt):
        return False
    expected = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path = HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(expected)
        ):
            return False
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            return False
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            total = 0
            while total <= len(expected):
                chunk = os.read(descriptor, len(expected) + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after_read = os.fstat(descriptor)
            after_path = os.lstat(path)
        finally:
            os.close(descriptor)
    except Exception:
        return False
    return bool(
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and opened.st_uid == os.geteuid()
        and stat.S_IMODE(opened.st_mode) == 0o600
        and opened.st_dev == before.st_dev == after_read.st_dev == after_path.st_dev
        and opened.st_ino == before.st_ino == after_read.st_ino == after_path.st_ino
        and opened.st_size == before.st_size == after_read.st_size == after_path.st_size
        and opened.st_ctime_ns
        == before.st_ctime_ns
        == after_read.st_ctime_ns
        == after_path.st_ctime_ns
        and opened.st_mtime_ns
        == before.st_mtime_ns
        == after_read.st_mtime_ns
        == after_path.st_mtime_ns
        and after_path.st_uid == os.geteuid()
        and stat.S_IMODE(after_path.st_mode) == 0o600
        and b"".join(chunks) == expected
    )


def block_if_remote_write_safety_incident_latched(
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    AmbiguousRemotePostOutcome: Any,
    REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE: Any,
    remote_write_safety_incident_is_latched: Any,
    remote_write_safety_marker_path_present_or_unsafe: Any,
    remote_write_safety_protocol_is_active: Any,
) -> None:
    """Fail before remote work when a marker or either process latch exists."""
    if not remote_write_safety_protocol_is_active():
        raise AmbiguousRemotePostOutcome(
            "Remote-write safety protocol is not durably activated; every "
            "remote operation remains blocked: "
            f"{REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE}",
            service="x",
        )
    marker_exists = remote_write_safety_marker_path_present_or_unsafe()
    # Re-read after the namespace probe: observing or failing to inspect the
    # marker seeds both process latches, and a concurrent signal-path latch
    # must not be lost to a stale pre-probe snapshot.
    incident_latched = remote_write_safety_incident_is_latched()
    if not incident_latched and not marker_exists:
        return
    if marker_exists:
        raise AmbiguousRemotePostOutcome(
            "Unreconciled ambiguous/confirmed-persistence remote-write safety "
            "barrier blocks further posting: "
            f"{AMBIGUOUS_POST_OUTCOME_FILE} or "
            f"{AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE}",
            service="x",
        )
    raise AmbiguousRemotePostOutcome(
        "Unreconciled in-process remote-write safety latch or marker-"
        "durability uncertainty blocks further posting",
        service="x",
    )


def remote_write_transport_journal_is_blocking(
    *,
    remote_write_transport_journal_paths: Any,
    transport_journal_is_blocking: Any,
) -> bool:
    """Return whether any valid, invalid, or torn transport journal exists."""

    return any(
        transport_journal_is_blocking(path)
        for path in remote_write_transport_journal_paths()
    )


def remote_receipt_retirement_is_blocking(
    *,
    remote_source_receipt_paths: Any,
    retirement_auxiliary_barrier_exists: Any,
    retirement_ledger_is_blocking: Any,
) -> bool:
    """Return whether any source-receipt retirement is incomplete or unsafe."""

    return any(
        retirement_auxiliary_barrier_exists(path)
        or retirement_ledger_is_blocking(path)
        for path in remote_source_receipt_paths()
    )


def block_if_remote_receipt_retirement_exists(
    *,
    AmbiguousRemotePostOutcome: Any,
    remote_receipt_retirement_is_blocking: Any,
) -> None:
    """Fail closed while a receipt-removal transaction remains unfinished."""

    if remote_receipt_retirement_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An incomplete source-receipt retirement blocks every remote-write lane",
            service="x",
        )


def block_if_remote_write_transport_journal_exists(
    *,
    AmbiguousRemotePostOutcome: Any,
    remote_write_transport_journal_is_blocking: Any,
) -> None:
    """Fail closed before unrelated remote work while a journal is unresolved."""

    if remote_write_transport_journal_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An unresolved payload-bound transport journal blocks every other "
            "remote-write lane",
            service="x",
        )


def confirmed_main_receipt_is_sole_local_recovery_barrier(
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
    log: Any,
    receipt_namespace_entry_exists: Any,
    remote_write_transport_journal_paths: Any,
    transport_journal_is_blocking: Any,
    verify_lane_transport_source_lineage_if_present: Any,
) -> bool:
    """Return whether one main receipt may finish under its confirmed journal.

    This is intentionally narrower than ignoring the global journal barrier.
    It exists so the regular and meme entry points can perform local recovery
    when called directly, just as the daemon's pre-barrier reconciler does.
    """

    source_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    present = [
        path for path in source_paths if receipt_namespace_entry_exists(path)
    ]
    if len(present) != 1 or present[0] not in {
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
    }:
        return False
    owner = present[0]
    status, receipt = (
        load_regular_post_receipt()
        if owner == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status not in {"pending_schedule", "valid"} or receipt is None:
        return False
    blocking_states = [
        (path, inspect_transport_state(path))
        for path in remote_write_transport_journal_paths()
        if transport_journal_is_blocking(path)
    ]
    if not blocking_states:
        # Only legacy full receipts can legitimately predate a journal.
        return status == "valid"
    if len(blocking_states) != 1:
        return False
    path, state = blocking_states[0]
    if not (
        path.absolute() == journal_path_for_receipt(owner).absolute()
        and state.classification == "confirmed_pair"
    ):
        return False
    lane = "quote_image" if owner == REGULAR_POST_RECEIPT_FILE else "daily_meme"
    try:
        return verify_lane_transport_source_lineage_if_present(
            receipt_path=owner,
            receipt=receipt,
            lane=lane,
            post_id=str(receipt.get("post_id") or ""),
        )
    except Exception:
        log.critical(
            "A confirmed main-post recovery receipt failed source-lineage "
            "validation before local reconciliation",
            exc_info=True,
        )
        return False


def remote_media_upload_receipt_is_blocking(
    *,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    media_upload_receipt_is_blocking: Any,
) -> bool:
    """Return whether a valid, invalid, or torn media transaction exists."""

    return media_upload_receipt_is_blocking(MEDIA_UPLOAD_RECEIPT_FILE)


def block_if_remote_media_upload_receipt_exists(
    *,
    AmbiguousRemotePostOutcome: Any,
    remote_media_upload_receipt_is_blocking: Any,
) -> None:
    """Fail closed before unrelated work while a media upload is unresolved."""

    if remote_media_upload_receipt_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An unresolved media-upload receipt blocks every remote-write lane",
            service="x",
        )


def block_if_ambiguous_remote_post(
    *,
    prepared_conversational_reply_receipt: dict | None = None,
    prepared_historical_context_reply_receipt: dict | None = None,
    prepared_main_post_attempt: dict | None = None,
    allow_confirmed_pending_schedule_reconciliation: bool = False,
    allow_historical_context_receipt_reconciliation: bool = False,
    allow_historical_context_outbox_reconciliation_parent_id: str | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
    AmbiguousRemotePostOutcome: Any,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    InvalidMemePostReceipt: Any,
    InvalidRegularPostReceipt: Any,
    MEME_POST_RECEIPT_FILE: Any,
    Path: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    block_if_remote_media_upload_receipt_exists: Any,
    block_if_remote_receipt_retirement_exists: Any,
    block_if_remote_write_safety_incident_latched: Any,
    block_if_remote_write_transport_journal_exists: Any,
    confirmed_main_receipt_is_sole_local_recovery_barrier: Any,
    current_main_post_attempt_is_semantically_valid: Any,
    exact_historical_context_sending_receipt_matches: Any,
    historical_context_outbox_remote_attempt_is_blocking: Any,
    historical_context_receipt_parent_for_local_reconciliation: Any,
    historical_context_receipt_path_present_or_unsafe: Any,
    inspect_transport_state: Any,
    load_confirmed_reply_receipt: Any,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
    remote_write_transport_journal_paths: Any,
    transport_journal_is_blocking: Any,
) -> None:
    """Refuse posting while a remote-write safety incident is unresolved."""
    prepared_receipt_count = sum(
        item is not None
        for item in (
            prepared_conversational_reply_receipt,
            prepared_historical_context_reply_receipt,
            prepared_main_post_attempt,
        )
    )
    if prepared_receipt_count > 1:
        raise AmbiguousRemotePostOutcome(
            "One remote write cannot be authorised by multiple transaction "
            "receipts or attempt records",
            service="x",
        )
    local_main_reconciliation_authorised = bool(
        allow_confirmed_pending_schedule_reconciliation
        and confirmed_main_receipt_is_sole_local_recovery_barrier()
    )
    block_if_remote_write_safety_incident_latched()
    if historical_context_outbox_remote_attempt_is_blocking(
        prepared_receipt=prepared_historical_context_reply_receipt,
        prepared_transport_authority=prepared_transport_authority,
        allow_local_reconciliation_parent_id=(
            allow_historical_context_outbox_reconciliation_parent_id
        ),
    ):
        raise AmbiguousRemotePostOutcome(
            "A historical-context outbox attempt may have reached remote "
            "transport and blocks every remote-write lane",
            service="x",
        )
    block_if_remote_receipt_retirement_exists()
    if prepared_transport_authority is None:
        if not local_main_reconciliation_authorised:
            block_if_remote_write_transport_journal_exists()
    else:
        expected_journal = Path(prepared_transport_authority.journal_path)
        state = inspect_transport_state(expected_journal)
        unrelated = [
            path
            for path in remote_write_transport_journal_paths()
            if path.absolute() != expected_journal.absolute()
            and transport_journal_is_blocking(path)
        ]
        if (
            unrelated
            or prepared_transport_authority.lifecycle_state != "prepared"
            or state.classification != "prepared_pair"
            or state.journal is None
            or state.fence is None
            or state.journal.document.get("transaction_id")
            != prepared_transport_authority.transaction_id
            or state.journal.sha256
            != prepared_transport_authority.journal_sha256
            or state.fence.sha256 != prepared_transport_authority.fence_sha256
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared tweet authority is not the sole exact transport barrier",
                service="x",
            )
    block_if_remote_media_upload_receipt_exists()

    regular_status, regular_receipt = load_regular_post_receipt()
    meme_status, meme_receipt = load_meme_post_receipt()
    prepared_main_authorized = prepared_main_post_attempt is None
    blocking_main_receipts = [
        ("regular", regular_status, regular_receipt),
        ("meme", meme_status, meme_receipt),
    ]
    for lane_name, status, receipt in blocking_main_receipts:
        if status == "absent":
            continue
        if (
            status in {"pending_schedule", "valid"}
            and local_main_reconciliation_authorised
        ):
            # Main-lane entry points may pass this narrow exception solely to
            # reach their local receipt reconciler.  Every remote-create
            # preflight, including the later preflight in those same lanes,
            # retains the default fail-closed behaviour.
            continue
        if status == "invalid":
            if lane_name == "regular":
                raise InvalidRegularPostReceipt(
                    f"Invalid regular-post receipt blocks posting: "
                    f"{REGULAR_POST_RECEIPT_FILE}"
                )
            raise InvalidMemePostReceipt(
                f"Invalid meme-post receipt blocks posting: {MEME_POST_RECEIPT_FILE}"
            )
        if (
            status == "sending"
            and prepared_main_post_attempt is not None
            and receipt == prepared_main_post_attempt
            and current_main_post_attempt_is_semantically_valid(
                prepared_main_post_attempt
            )
            and prepared_main_post_attempt.get("lifecycle_state")
            == (
                "attempting"
                if prepared_transport_authority is not None
                else "sending"
            )
        ):
            prepared_main_authorized = True
            continue
        raise AmbiguousRemotePostOutcome(
            "An unresolved main-post sending, confirmed pending-schedule, or "
            "invalid receipt blocks further posting",
            service="x",
        )
    if not prepared_main_authorized:
        raise AmbiguousRemotePostOutcome(
            "Prepared main-post attempt is not the exact durable sending receipt",
            service="x",
        )

    status, receipt = load_confirmed_reply_receipt()
    if prepared_conversational_reply_receipt is not None:
        if not (
            status == "sending"
            and receipt == prepared_conversational_reply_receipt
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared conversational-reply receipt is not the exact "
                "durable sending receipt",
                service="x",
            )
    elif status != "absent":
        raise AmbiguousRemotePostOutcome(
            "An unresolved conversational-reply source receipt blocks "
            f"further posting: {CONFIRMED_REPLY_RECEIPT_FILE}",
            service="x",
        )

    if not historical_context_receipt_path_present_or_unsafe():
        if prepared_historical_context_reply_receipt is not None:
            raise AmbiguousRemotePostOutcome(
                "Prepared historical-context receipt is not durably present",
                service="x",
            )
        return
    if (
        allow_historical_context_receipt_reconciliation
        and historical_context_receipt_parent_for_local_reconciliation()
        is not None
    ):
        # This exception reaches only the outbox worker's local receipt
        # reconciler.  The worker must re-run the ordinary fail-closed check
        # before claiming or transmitting any other context attempt.
        return
    if exact_historical_context_sending_receipt_matches(
        prepared_historical_context_reply_receipt
    ):
        return
    raise AmbiguousRemotePostOutcome(
        "An unresolved or unsafe historical-context reply receipt blocks "
        f"further posting: {HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE}",
        service="x",
    )


def ambiguous_remote_post_is_blocking(
    *,
    historical_context_outbox_remote_attempt_is_blocking: Any,
    historical_context_receipt_path_present_or_unsafe: Any,
    log: Any,
    remote_media_upload_receipt_is_blocking: Any,
    remote_receipt_retirement_is_blocking: Any,
    remote_write_safety_incident_is_latched: Any,
    remote_write_safety_marker_path_present_or_unsafe: Any,
    remote_write_safety_protocol_is_active: Any,
    remote_write_transport_journal_is_blocking: Any,
    unresolved_conversational_reply_receipt_is_blocking: Any,
    unresolved_main_post_attempt_is_blocking: Any,
) -> bool:
    """Return the global write barrier state without starting any remote work."""
    if not remote_write_safety_protocol_is_active():
        return True
    if remote_write_safety_incident_is_latched():
        return True
    if remote_write_safety_marker_path_present_or_unsafe():
        return True
    if historical_context_outbox_remote_attempt_is_blocking():
        return True
    if remote_write_transport_journal_is_blocking():
        return True
    if remote_media_upload_receipt_is_blocking():
        return True
    if remote_receipt_retirement_is_blocking():
        return True
    if historical_context_receipt_path_present_or_unsafe():
        return True
    try:
        return (
            unresolved_main_post_attempt_is_blocking()
            or unresolved_conversational_reply_receipt_is_blocking()
        )
    except Exception:
        log.critical(
            "A remote-write receipt cannot be inspected; treating all remote "
            "writes as blocked",
            exc_info=True,
        )
        return True
