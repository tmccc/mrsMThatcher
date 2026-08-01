"""Adversarial regression for the receipt-to-X transport boundary.

The driver phases execute in literal Python interpreters.  The send process
validates the lane-owned durable receipt, lets a separate peer process unlink
and directory-fsync that receipt at the last lane-specific validation point,
and replaces ``requests.request`` with a local hard-exit sentinel.  A fresh
interpreter then inspects the same state directory.

The safety assertion is deliberately implementation-neutral: receipt loss may
prevent transport, or transport may proceed only when a restart-persistent
successor still blocks Process 2.  Reaching transport and then restarting with
no barrier reproduces IR-40AB83E-01.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).resolve()
LANES = (
    "regular_quote_image",
    "daily_meme",
    "conversational_reply",
    "historical_context_reply",
)
TRANSPORT_EXIT_CODES = {
    lane: 80 + index for index, lane in enumerate(LANES, start=1)
}
PATH_MUTATIONS = (
    "same_bytes_replacement",
    "different_bytes_replacement",
    "symlink_replacement",
    "directory_replacement",
    "fifo_replacement",
)
SOURCE_MUTATION_FAULTS = tuple(
    f"source_{mutation}" for mutation in PATH_MUTATIONS
)
COMPANION_MUTATION_FAULTS = tuple(
    f"{target}_{mutation}"
    for target in ("journal", "fence")
    for mutation in PATH_MUTATIONS
)
PEER_MUTATION_FAULTS = SOURCE_MUTATION_FAULTS + COMPANION_MUTATION_FAULTS


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_evidence(path: Path, value: object) -> None:
    data = _canonical_bytes(value)
    temporary = path.with_name(f".{path.name}.pending.{os.getpid()}")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError("short evidence write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _driver_environment(state_directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(state_directory),
            "MRS_LOG_FILE": str(state_directory / "ir-40ab83e-driver.log"),
            "PYTHONPATH": str(ROOT),
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "X_BEARER_TOKEN": "dummy",
        }
    )
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONSTARTUP", None)
    return environment


def _run_driver(
    phase: str,
    *,
    lane: str,
    state_directory: Path,
    fault: str = "before_journal",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(DRIVER),
            "--ir-40ab83e-driver",
            phase,
            "--lane",
            lane,
            "--fault",
            fault,
            "--root",
            str(ROOT),
            "--state-directory",
            str(state_directory),
        ),
        cwd=ROOT,
        env=_driver_environment(state_directory),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _import_candidate(root: Path, state_directory: Path):
    root = root.resolve(strict=True)
    state_directory = state_directory.resolve(strict=True)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import mrsMThatcher2 as bot

    if Path(bot.__file__).resolve(strict=True) != root / "mrsMThatcher2.py":
        raise RuntimeError("IR-40AB83E driver imported the wrong candidate")
    if Path(bot.BASE_DIR).resolve(strict=True) != state_directory:
        raise RuntimeError("IR-40AB83E driver imported the wrong state root")
    return bot


def _receipt_path(bot: Any, lane: str) -> Path:
    return {
        "regular_quote_image": Path(bot.REGULAR_POST_RECEIPT_FILE),
        "daily_meme": Path(bot.MEME_POST_RECEIPT_FILE),
        "conversational_reply": Path(bot.CONFIRMED_REPLY_RECEIPT_FILE),
        "historical_context_reply": Path(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        ),
    }[lane]


def _receipt_presence(bot: Any) -> dict[str, bool]:
    return {
        "regular_quote_image": os.path.lexists(bot.REGULAR_POST_RECEIPT_FILE),
        "daily_meme": os.path.lexists(bot.MEME_POST_RECEIPT_FILE),
        "conversational_reply": os.path.lexists(
            bot.CONFIRMED_REPLY_RECEIPT_FILE
        ),
        "historical_context_reply": os.path.lexists(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        ),
    }


def _peer_unlink_and_fsync(
    receipt_path: Path,
    *,
    lane: str,
    state_directory: Path,
    validation_point: str,
) -> None:
    """Use a literal peer process to remove the validated receipt durably."""

    before = os.lstat(receipt_path)
    read_fd, write_fd = os.pipe()
    peer_pid = os.fork()
    if peer_pid == 0:
        try:
            os.close(read_fd)
            receipt_path.unlink()
            _fsync_directory(receipt_path.parent)
            os.write(write_fd, b"receipt-unlinked-and-parent-fsynced")
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(97)

    os.close(write_fd)
    try:
        acknowledgement = os.read(read_fd, 128)
    finally:
        os.close(read_fd)
    waited_pid, status = os.waitpid(peer_pid, 0)
    if waited_pid != peer_pid or not os.WIFEXITED(status):
        raise RuntimeError("receipt-deletion peer did not exit normally")
    if os.WEXITSTATUS(status) != 0:
        raise RuntimeError("receipt-deletion peer failed")
    if acknowledgement != b"receipt-unlinked-and-parent-fsynced":
        raise RuntimeError("receipt-deletion peer did not confirm directory fsync")
    if os.path.lexists(receipt_path):
        raise RuntimeError("receipt-deletion peer left the receipt present")

    _write_evidence(
        state_directory / "peer_deletion.json",
        {
            "directory_fsync_completed": True,
            "lane": lane,
            "parent_pid": os.getpid(),
            "peer_pid": peer_pid,
            "receipt_device": int(before.st_dev),
            "receipt_inode": int(before.st_ino),
            "receipt_path_basename": receipt_path.name,
            "receipt_present_after": False,
            "validation_point": validation_point,
        },
    )


def _peer_remove_successor_at_transport(
    path: Path,
    *,
    lane: str,
    state_directory: Path,
    kind: str,
) -> None:
    """Remove one successor name in a peer while its companion must survive."""

    before = os.lstat(path)
    read_fd, write_fd = os.pipe()
    peer_pid = os.fork()
    if peer_pid == 0:
        try:
            os.close(read_fd)
            path.unlink()
            _fsync_directory(path.parent)
            os.write(write_fd, b"successor-unlinked")
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(98)
    os.close(write_fd)
    try:
        acknowledgement = os.read(read_fd, 64)
    finally:
        os.close(read_fd)
    waited_pid, status = os.waitpid(peer_pid, 0)
    if (
        waited_pid != peer_pid
        or not os.WIFEXITED(status)
        or os.WEXITSTATUS(status) != 0
        or acknowledgement != b"successor-unlinked"
    ):
        raise RuntimeError("successor-deletion peer failed")
    _write_evidence(
        state_directory / f"peer_{kind}_deletion.json",
        {
            "device": int(before.st_dev),
            "directory_fsync_completed": True,
            "inode": int(before.st_ino),
            "kind": kind,
            "lane": lane,
            "path_basename": path.name,
            "present_after": os.path.lexists(path),
        },
    )


def _mode_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISFIFO(mode):
        return "fifo"
    return "other"


def _peer_mutate_and_fsync(
    path: Path,
    *,
    lane: str,
    state_directory: Path,
    target_kind: str,
    mutation: str,
) -> None:
    """Replace one authority pathname in a literal peer without following it."""

    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("peer mutation source is not an ordinary file")
    before_bytes = path.read_bytes()
    before_sha256 = hashlib.sha256(before_bytes).hexdigest()
    read_fd, write_fd = os.pipe()
    peer_pid = os.fork()
    if peer_pid == 0:
        try:
            os.close(read_fd)
            if mutation in {
                "same_bytes_replacement",
                "different_bytes_replacement",
            }:
                replacement = (
                    before_bytes
                    if mutation == "same_bytes_replacement"
                    else b'{"peer_mutation":"different_bytes"}\n'
                )
                temporary = path.with_name(
                    f".{path.name}.peer-replacement.{os.getpid()}"
                )
                descriptor = os.open(
                    temporary,
                    os.O_CREAT
                    | os.O_EXCL
                    | os.O_WRONLY
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                try:
                    written = os.write(descriptor, replacement)
                    if written != len(replacement):
                        raise OSError("short peer replacement write")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, path)
            elif mutation == "symlink_replacement":
                target = path.with_name(
                    f".{path.name}.peer-symlink-target.{os.getpid()}"
                )
                target.write_bytes(before_bytes)
                path.unlink()
                path.symlink_to(target.name)
            elif mutation == "directory_replacement":
                path.unlink()
                path.mkdir(mode=0o700)
            elif mutation == "fifo_replacement":
                path.unlink()
                os.mkfifo(path, mode=0o600)
            else:  # pragma: no cover - guarded by argparse and parametrisation
                raise RuntimeError(f"unsupported peer mutation: {mutation}")
            _fsync_directory(path.parent)
            os.write(write_fd, b"path-mutated-and-parent-fsynced")
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(99)

    os.close(write_fd)
    try:
        acknowledgement = os.read(read_fd, 128)
    finally:
        os.close(read_fd)
    waited_pid, status = os.waitpid(peer_pid, 0)
    if (
        waited_pid != peer_pid
        or not os.WIFEXITED(status)
        or os.WEXITSTATUS(status) != 0
        or acknowledgement != b"path-mutated-and-parent-fsynced"
    ):
        raise RuntimeError("path-mutation peer failed")

    after = os.lstat(path)
    after_kind = _mode_kind(after.st_mode)
    expected_after_kind = {
        "same_bytes_replacement": "regular",
        "different_bytes_replacement": "regular",
        "symlink_replacement": "symlink",
        "directory_replacement": "directory",
        "fifo_replacement": "fifo",
    }[mutation]
    if after_kind != expected_after_kind:
        raise RuntimeError("peer mutation produced the wrong file type")
    if mutation in {"same_bytes_replacement", "different_bytes_replacement"}:
        if (int(after.st_dev), int(after.st_ino)) == (
            int(before.st_dev),
            int(before.st_ino),
        ):
            raise RuntimeError("peer replacement did not change pathname identity")
        after_sha256: str | None = hashlib.sha256(path.read_bytes()).hexdigest()
        if mutation == "same_bytes_replacement" and after_sha256 != before_sha256:
            raise RuntimeError("same-byte peer replacement changed bytes")
        if mutation == "different_bytes_replacement" and after_sha256 == before_sha256:
            raise RuntimeError("different-byte peer replacement preserved bytes")
    else:
        after_sha256 = None

    _write_evidence(
        state_directory / f"peer_mutation_{target_kind}_{mutation}.json",
        {
            "after_device": int(after.st_dev),
            "after_inode": int(after.st_ino),
            "after_kind": after_kind,
            "after_sha256": after_sha256,
            "before_device": int(before.st_dev),
            "before_inode": int(before.st_ino),
            "before_kind": _mode_kind(before.st_mode),
            "before_sha256": before_sha256,
            "directory_fsync_completed": True,
            "lane": lane,
            "mutation": mutation,
            "parent_pid": os.getpid(),
            "path_basename": path.name,
            "peer_pid": peer_pid,
            "target_kind": target_kind,
        },
    )


def _install_receipt_loss_fault(
    bot: Any,
    *,
    lane: str,
    state_directory: Path,
) -> None:
    receipt_path = _receipt_path(bot, lane)
    injected = False

    if lane in {"regular_quote_image", "daily_meme"}:
        real_promote = bot.mark_main_post_attempt_attempting

        def promote_then_lose_receipt(attempt: dict[str, object]):
            nonlocal injected
            attempting = real_promote(attempt)
            if injected:
                raise RuntimeError("receipt-loss fault ran more than once")
            injected = True
            _peer_unlink_and_fsync(
                receipt_path,
                lane=lane,
                state_directory=state_directory,
                validation_point="after_exact_sending_to_attempting_promotion",
            )
            return attempting

        bot.mark_main_post_attempt_attempting = promote_then_lose_receipt
        return

    real_blocker = bot.block_if_ambiguous_remote_post

    def validate_then_lose_receipt(**kwargs: object) -> None:
        nonlocal injected
        real_blocker(**kwargs)
        prepared = (
            kwargs.get("prepared_conversational_reply_receipt")
            if lane == "conversational_reply"
            else kwargs.get("prepared_historical_context_reply_receipt")
        )
        if prepared is None:
            return
        if injected:
            raise RuntimeError("receipt-loss fault ran more than once")
        injected = True
        _peer_unlink_and_fsync(
            receipt_path,
            lane=lane,
            state_directory=state_directory,
            validation_point="after_exact_durable_sending_receipt_validation",
        )

    bot.block_if_ambiguous_remote_post = validate_then_lose_receipt


def _install_post_journal_receipt_loss_fault(
    bot: Any,
    *,
    lane: str,
    state_directory: Path,
) -> None:
    """Remove the lane receipt only after its durable successor is armed."""

    receipt_path = _receipt_path(bot, lane)
    real_consume = bot.consume_transport_authority
    injected = False

    def consume_after_peer_deletion(*args: object, **kwargs: object) -> None:
        nonlocal injected
        if injected:
            raise RuntimeError("post-journal receipt-loss fault ran more than once")
        injected = True
        _peer_unlink_and_fsync(
            receipt_path,
            lane=lane,
            state_directory=state_directory,
            validation_point="after_payload_bound_transport_journal_arm",
        )
        real_consume(*args, **kwargs)

    bot.consume_transport_authority = consume_after_peer_deletion


def _install_post_journal_source_mutation_fault(
    bot: Any,
    *,
    lane: str,
    state_directory: Path,
    mutation: str,
) -> None:
    """Mutate the receipt after its journal is armed but before final consume."""

    receipt_path = _receipt_path(bot, lane)
    real_consume = bot.consume_transport_authority
    injected = False

    def consume_after_peer_mutation(*args: object, **kwargs: object) -> None:
        nonlocal injected
        if injected:
            raise RuntimeError("post-journal source mutation ran more than once")
        injected = True
        _peer_mutate_and_fsync(
            receipt_path,
            lane=lane,
            state_directory=state_directory,
            target_kind="source_receipt",
            mutation=mutation,
        )
        real_consume(*args, **kwargs)

    bot.consume_transport_authority = consume_after_peer_mutation


def _invoke_lane(bot: Any, lane: str, state_directory: Path) -> None:
    """Enter the real public transaction lane without any remote dependency."""

    if lane in {"regular_quote_image", "daily_meme"}:
        from tests.test_unit_helpers import (
            configure_simple_meme_post,
            configure_simple_quote_post,
        )

        monkeypatch = pytest.MonkeyPatch()
        actual_create_post = bot.create_post
        if lane == "regular_quote_image":
            lines_used, images_used, state, *_ = configure_simple_quote_post(
                state_directory,
                monkeypatch,
            )
            quote_hash = bot.quote_text_hash("Good quote.")
            monkeypatch.setattr(
                bot,
                "completed_research_quote_hashes",
                lambda: {quote_hash},
            )
            monkeypatch.setattr(bot, "create_post", actual_create_post)
            bot.post_random_quote(lines_used, images_used, state)
            return

        state, _receipt_path_value = configure_simple_meme_post(
            state_directory,
            monkeypatch,
        )
        bot.post_next_meme(state)
        return

    if lane == "conversational_reply":
        from tests.test_unit_helpers import (
            UNIT_REPLY_REPOSITORY,
            unit_sending_v4_reply_receipt,
        )

        bot.reply_evidence_repository = lambda: UNIT_REPLY_REPOSITORY
        receipt = unit_sending_v4_reply_receipt()
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=receipt,
            reply_text=str(receipt["reply_text"]),
            reply_to_id=str(receipt["target_id"]),
            made_with_ai=False,
            lane="mention",
        )
        return

    from historical_context_formatter import HistoricalContextReplyStore
    from transaction_mutation_authority import issue_transaction_mutation_authority

    store = HistoricalContextReplyStore(
        Path(bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE),
        Path(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE),
        mutation_authority_provider=lambda operation: (
            issue_transaction_mutation_authority(
                lambda _verified_operation: None,
                operation=operation,
            )
        ),
    )
    store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context — IR-40AB83E local adversarial fixture.",
        create_post=bot.create_post,
        now_epoch=lambda: 1_800_000_000,
    )


def _send_phase(
    root: Path,
    state_directory: Path,
    lane: str,
    fault: str,
) -> int:
    bot = _import_candidate(root, state_directory)
    from tests.helpers.protocol_activation import create_test_protocol_activation

    create_test_protocol_activation(
        Path(bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE)
    )
    bot._PRODUCTION_BOOTSTRAPPED = True
    bot._AMBIGUOUS_REMOTE_POST_SEEN = False
    bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    if fault == "before_journal":
        _install_receipt_loss_fault(
            bot,
            lane=lane,
            state_directory=state_directory,
        )
    elif fault in SOURCE_MUTATION_FAULTS:
        _install_post_journal_source_mutation_fault(
            bot,
            lane=lane,
            state_directory=state_directory,
            mutation=fault.removeprefix("source_"),
        )
    elif fault not in COMPANION_MUTATION_FAULTS:
        _install_post_journal_receipt_loss_fault(
            bot,
            lane=lane,
            state_directory=state_directory,
        )

    def hard_exit_at_transport(
        method: str,
        url: str,
        **_kwargs: object,
    ) -> None:
        receipt_path = _receipt_path(bot, lane)
        journal_path = bot.journal_path_for_receipt(receipt_path)
        if fault == "journal_deleted_at_transport":
            _peer_remove_successor_at_transport(
                journal_path,
                lane=lane,
                state_directory=state_directory,
                kind="journal",
            )
        elif fault == "fence_deleted_at_transport":
            _peer_remove_successor_at_transport(
                bot.fence_path_for_journal(journal_path),
                lane=lane,
                state_directory=state_directory,
                kind="fence",
            )
        elif fault in COMPANION_MUTATION_FAULTS:
            target_kind, mutation = fault.split("_", 1)
            journal_path = bot.journal_path_for_receipt(receipt_path)
            target_path = (
                journal_path
                if target_kind == "journal"
                else bot.fence_path_for_journal(journal_path)
            )
            _peer_mutate_and_fsync(
                target_path,
                lane=lane,
                state_directory=state_directory,
                target_kind=target_kind,
                mutation=mutation,
            )
        _write_evidence(
            state_directory / "transport_boundary.json",
            {
                "ambiguity_marker_present": os.path.lexists(
                    bot.AMBIGUOUS_POST_OUTCOME_FILE
                ),
                "ambiguity_successor_present": os.path.lexists(
                    bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
                ),
                "lane": lane,
                "method": str(method),
                "receipt_present": os.path.lexists(receipt_path),
                "transport_pid": os.getpid(),
                "url_path": (
                    "/2/tweets"
                    if str(url).split("?", 1)[0].endswith("/2/tweets")
                    else str(url).split("?", 1)[0]
                ),
            },
        )
        os._exit(TRANSPORT_EXIT_CODES[lane])

    bot.requests.request = hard_exit_at_transport
    try:
        _invoke_lane(bot, lane, state_directory)
    except BaseException as error:
        _write_evidence(
            state_directory / "send_stopped_before_transport.json",
            {
                "error_message": str(error),
                "error_type": type(error).__name__,
                "lane": lane,
                "receipt_presence": _receipt_presence(bot),
                "transport_reached": False,
            },
        )
        return 0
    _write_evidence(
        state_directory / "send_stopped_before_transport.json",
        {
            "error_message": "lane returned without reaching transport",
            "error_type": "UnexpectedLaneReturn",
            "lane": lane,
            "receipt_presence": _receipt_presence(bot),
            "transport_reached": False,
        },
    )
    return 0


def _restart_phase(root: Path, state_directory: Path, lane: str) -> int:
    bot = _import_candidate(root, state_directory)
    initial_seen = bool(bot._AMBIGUOUS_REMOTE_POST_SEEN)
    initial_uncertain = bool(bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN)
    blocking = bool(bot.ambiguous_remote_post_is_blocking())
    direct_preflight_blocked = False
    direct_preflight_error = ""
    try:
        bot.block_if_ambiguous_remote_post()
    except BaseException as error:
        direct_preflight_blocked = True
        direct_preflight_error = type(error).__name__
    value = {
        "ambiguity_marker_present": os.path.lexists(
            bot.AMBIGUOUS_POST_OUTCOME_FILE
        ),
        "ambiguity_successor_present": os.path.lexists(
            bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
        ),
        "blocking": blocking,
        "direct_preflight_blocked": direct_preflight_blocked,
        "direct_preflight_error": direct_preflight_error,
        "initial_durability_uncertain": initial_uncertain,
        "initial_remote_seen": initial_seen,
        "lane": lane,
        "protocol_active": bool(bot.remote_write_safety_protocol_is_active()),
        "receipt_presence": _receipt_presence(bot),
    }
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


@pytest.mark.parametrize(
    "fault",
    (
        "before_journal",
        "after_journal",
        "journal_deleted_at_transport",
        "fence_deleted_at_transport",
    ),
)
@pytest.mark.parametrize("lane", LANES)
def test_receipt_loss_cannot_leave_transport_unbarriered_after_restart(
    lane: str,
    fault: str,
    tmp_path: Path,
) -> None:
    """Either stop before transport or leave a durable Process-2 barrier."""

    state_directory = tmp_path / f"{lane}-{fault}"
    state_directory.mkdir(mode=0o700)
    send = _run_driver(
        "send",
        lane=lane,
        state_directory=state_directory,
        fault=fault,
    )
    peer_evidence_path = state_directory / "peer_deletion.json"
    assert peer_evidence_path.is_file(), (
        "fault injection did not reach the exact validated-receipt boundary: "
        f"stdout={send.stdout!r} stderr={send.stderr!r}"
    )
    peer = json.loads(peer_evidence_path.read_text(encoding="utf-8"))
    assert peer["directory_fsync_completed"] is True
    assert peer["receipt_present_after"] is False
    assert peer["peer_pid"] != peer["parent_pid"]

    transport_path = state_directory / "transport_boundary.json"
    transport_reached = transport_path.is_file()
    transport = (
        json.loads(transport_path.read_text(encoding="utf-8"))
        if transport_reached
        else None
    )
    if transport_reached:
        assert send.returncode == TRANSPORT_EXIT_CODES[lane]
        assert transport is not None
        assert transport["method"] == "POST"
        assert transport["url_path"] == "/2/tweets"
        assert transport["receipt_present"] is False
    else:
        assert send.returncode == 0, (
            f"send driver failed unexpectedly: stdout={send.stdout!r} "
            f"stderr={send.stderr!r}"
        )

    restart = _run_driver(
        "restart",
        lane=lane,
        state_directory=state_directory,
        fault=fault,
    )
    assert restart.returncode == 0, (
        f"restart driver failed: stdout={restart.stdout!r} "
        f"stderr={restart.stderr!r}"
    )
    restart_value = json.loads(restart.stdout.strip().splitlines()[-1])
    assert restart_value["protocol_active"] is True
    restart_barrier_present = bool(
        restart_value["blocking"] and restart_value["direct_preflight_blocked"]
    )

    evidence = {
        "finding_id": "IR-40AB83E-01",
        "fault": fault,
        "lane": lane,
        "peer_deletion": peer,
        "restart": restart_value,
        "safe_result": bool(not transport_reached or restart_barrier_present),
        "send_returncode": send.returncode,
        "transport": transport,
        "transport_reached": transport_reached,
    }
    assert evidence["safe_result"] is True, (
        "IR-40AB83E-01 reproduced: an exact durable receipt was validated, "
        "a peer durably removed it, the X create transport boundary was "
        "reached, and Process 2 found no restart-persistent barrier:\n"
        + json.dumps(evidence, indent=2, sort_keys=True)
    )


@pytest.mark.parametrize("fault", PEER_MUTATION_FAULTS)
@pytest.mark.parametrize("lane", LANES)
def test_peer_path_mutation_at_final_authority_remains_restart_safe(
    lane: str,
    fault: str,
    tmp_path: Path,
) -> None:
    """Path replacement/type mutation cannot erase all durable authority."""

    state_directory = tmp_path / f"{lane}-{fault}"
    state_directory.mkdir(mode=0o700)
    send = _run_driver(
        "send",
        lane=lane,
        state_directory=state_directory,
        fault=fault,
    )
    if fault in SOURCE_MUTATION_FAULTS:
        target_kind = "source_receipt"
        mutation = fault.removeprefix("source_")
    else:
        target_kind, mutation = fault.split("_", 1)
    mutation_path = (
        state_directory / f"peer_mutation_{target_kind}_{mutation}.json"
    )
    assert mutation_path.is_file(), (
        "fault injection did not reach the final authority boundary: "
        f"stdout={send.stdout!r} stderr={send.stderr!r}"
    )
    mutation_value = json.loads(mutation_path.read_text(encoding="utf-8"))
    assert mutation_value["directory_fsync_completed"] is True
    assert mutation_value["peer_pid"] != mutation_value["parent_pid"]
    if mutation.endswith("replacement") and mutation.startswith(
        ("same_bytes", "different_bytes")
    ):
        assert (
            mutation_value["before_device"],
            mutation_value["before_inode"],
        ) != (
            mutation_value["after_device"],
            mutation_value["after_inode"],
        )

    transport_path = state_directory / "transport_boundary.json"
    transport_reached = transport_path.is_file()
    transport = (
        json.loads(transport_path.read_text(encoding="utf-8"))
        if transport_reached
        else None
    )
    if transport_reached:
        assert send.returncode == TRANSPORT_EXIT_CODES[lane]
        assert transport is not None
        assert transport["method"] == "POST"
        assert transport["url_path"] == "/2/tweets"
    else:
        assert send.returncode == 0, (
            f"send driver failed unexpectedly: stdout={send.stdout!r} "
            f"stderr={send.stderr!r}"
        )

    restart = _run_driver(
        "restart",
        lane=lane,
        state_directory=state_directory,
        fault=fault,
    )
    assert restart.returncode == 0, (
        f"restart driver failed: stdout={restart.stdout!r} "
        f"stderr={restart.stderr!r}"
    )
    restart_value = json.loads(restart.stdout.strip().splitlines()[-1])
    assert restart_value["protocol_active"] is True
    restart_barrier_present = bool(
        restart_value["blocking"] and restart_value["direct_preflight_blocked"]
    )
    evidence = {
        "fault": fault,
        "lane": lane,
        "mutation": mutation_value,
        "restart": restart_value,
        "safe_result": bool(not transport_reached or restart_barrier_present),
        "send_returncode": send.returncode,
        "transport": transport,
        "transport_reached": transport_reached,
    }
    assert evidence["safe_result"] is True, (
        "peer mutation erased final transport authority without leaving a "
        "fresh-process durable blocker:\n"
        + json.dumps(evidence, indent=2, sort_keys=True)
    )


def _driver_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir-40ab83e-driver", choices=("send", "restart"))
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument(
        "--fault",
        choices=(
            "before_journal",
            "after_journal",
            "journal_deleted_at_transport",
            "fence_deleted_at_transport",
            *PEER_MUTATION_FAULTS,
        ),
        default="before_journal",
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.ir_40ab83e_driver == "send":
        return _send_phase(
            arguments.root,
            arguments.state_directory,
            arguments.lane,
            arguments.fault,
        )
    return _restart_phase(
        arguments.root,
        arguments.state_directory,
        arguments.lane,
    )


if __name__ == "__main__":
    raise SystemExit(_driver_main(sys.argv[1:]))
