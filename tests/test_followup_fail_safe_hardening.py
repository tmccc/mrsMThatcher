from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

import exact_receipt_retirement as retirement
import historical_context_outbox as outbox_module
import mrsMThatcher2 as bot
import mrs_log_digest as digest
import remote_write_safety_protocol as protocol
from remote_write_safety_protocol import (
    ACTIVATION_AUDIT_BASENAME,
    ACTIVATION_BASENAME,
)
from tests.helpers.protocol_activation import create_test_protocol_activation
from transaction_mutation_authority import issue_transaction_mutation_authority

from tests.helpers.installation_fixtures import (
    install_paths,
)


def prepare_context_create(
    *,
    text: str = "test",
    parent_post_id: str = "123",
) -> dict[str, object]:
    """Persist the minimal exact owner used by low-level create tests."""

    receipt: dict[str, object] = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_post_id,
        "quote_id": "a" * 64,
        "reply_text": text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-07-31T12:00:00Z",
        "attempt_number": 1,
    }
    bot.atomic_write_json(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE, receipt)
    return receipt


def create_bound_context_post(
    text: str = "test",
    *,
    receipt: dict[str, object] | None = None,
) -> dict:
    """Call create_post through one exact durable historical-context owner."""

    prepared = receipt or prepare_context_create(text=text)
    return bot.create_post(
        text,
        reply_to_id=str(prepared["parent_post_id"]),
        prepared_historical_context_reply_receipt=prepared,
    )


def armed_context_transport_authority(
    payload: dict[str, object],
) -> bot.TransportAuthority:
    receipt = prepare_context_create(text=str(payload.get("text") or ""))
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="historical_context_reply",
        payload=payload,
        source_validator_id="unit-test-context-binding-v2",
        source_validator=lambda lane, observed, body: bool(
            lane == "historical_context_reply"
            and observed == receipt
            and body == payload
        ),
    )
    return bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )


def test_explicit_initialisation_and_missing_file_matrix(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0
    for name in (
        "bot_state.json",
        "lines_used.json",
        "images_used.json",
        "historical_context_reply_history.json",
        "historical_context_reply_outbox.json",
        ".mrsMThatcher.initialised.json",
    ):
        assert (tmp_path / name).is_file()
    assert not (tmp_path / ".mrsMThatcher.initialising.json").exists()
    assert not os.path.lexists(tmp_path / ACTIVATION_AUDIT_BASENAME)
    assert not os.path.lexists(tmp_path / ACTIVATION_BASENAME)
    assert bot.remote_write_safety_protocol_is_active() is False
    for receipt_path in bot.remote_source_receipt_paths():
        ledger = retirement.inspect_retirement_ledger(receipt_path)
        assert ledger.valid is True
        assert ledger.blocking is False
        assert ledger.state == "idle"
        assert ledger.sequence == 0
    bot.require_established_installation()
    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    for missing in (
        "bot_state.json",
        "lines_used.json",
        "images_used.json",
        "historical_context_reply_history.json",
        "historical_context_reply_outbox.json",
    ):
        path = tmp_path / missing
        content = path.read_bytes()
        path.unlink()
        removed_backups: list[tuple[Path, bytes]] = []
        if missing == "bot_state.json":
            for backup in tmp_path.glob("bot_state.json.bak*"):
                removed_backups.append((backup, backup.read_bytes()))
                backup.unlink()
        with pytest.raises(RuntimeError, match="Required durable"):
            bot.require_established_installation()
        path.write_bytes(content)
        for backup, backup_content in removed_backups:
            backup.write_bytes(backup_content)


def _leave_exact_ledger_exchange(
    monkeypatch: pytest.MonkeyPatch,
    receipt_path: Path,
    *,
    fault: str,
) -> bytes:
    """Drive a real receipt retirement to one crash-left ledger exchange."""

    receipt_bytes = b'{"schema_version":1,"state":"synthetic"}\n'
    receipt_path.write_bytes(receipt_bytes)
    receipt_path.chmod(0o600)
    _ledger_path, exchange_path = retirement.retirement_ledger_paths(
        receipt_path
    )
    authority = issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="focused startup ledger-exchange fault",
    )
    if fault == "exchange_staged":
        original = retirement._stage_new

        def fail_after_stage(directory_fd: int, name: str, data: bytes):
            staged = original(directory_fd, name, data)
            if name == exchange_path.name:
                raise OSError("injected crash after ledger exchange staging")
            return staged

        monkeypatch.setattr(retirement, "_stage_new", fail_after_stage)
    else:
        original = retirement._rename_exchange

        def fail_after_exchange(
            directory_fd: int,
            first_name: str,
            second_name: str,
        ) -> None:
            original(directory_fd, first_name, second_name)
            if {first_name, second_name} == {
                retirement.retirement_ledger_paths(receipt_path)[0].name,
                exchange_path.name,
            }:
                raise OSError("injected crash after ledger atomic exchange")

        monkeypatch.setattr(
            retirement,
            "_rename_exchange",
            fail_after_exchange,
        )
    with pytest.raises(OSError, match="injected crash"):
        retirement.retire_exact_receipt(
            receipt_path,
            receipt_bytes,
            mutation_authority=authority,
        )
    return receipt_bytes


@pytest.mark.parametrize(
    "exchange_state",
    ("exchange_staged", "exchange_committed"),
)
def test_real_main_start_recovers_exact_ledger_exchange_before_establishment(
    exchange_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon completes only the exact ledger exchange before startup."""

    install_paths(monkeypatch, tmp_path, activate_protocol=True)
    receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    activation = bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    audit = activation.with_name(protocol.ACTIVATION_AUDIT_BASENAME)
    activation_before = activation.read_bytes()
    audit_before = audit.read_bytes()
    _leave_exact_ledger_exchange(
        monkeypatch,
        receipt_path,
        fault=exchange_state,
    )
    inspection = retirement.inspect_retirement_ledger(receipt_path)
    assert inspection.valid is True
    assert inspection.blocking is True
    assert inspection.state == exchange_state
    assert retirement.retirement_auxiliary_barrier_exists(receipt_path) is True

    # Restore the injected primitive before exercising the production recovery
    # path, and retain the real authority API with a focused verifier.
    monkeypatch.undo()
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(
        bot,
        "transaction_mutation_authority",
        lambda operation: issue_transaction_mutation_authority(
            lambda _operation: None,
            operation=operation,
        ),
    )

    class EstablishmentObserved(Exception):
        pass

    def observe_establishment() -> None:
        stable = retirement.inspect_retirement_ledger(receipt_path)
        assert stable.valid is True
        assert stable.blocking is False
        assert stable.state == "completed"
        assert retirement.retirement_auxiliary_barrier_exists(receipt_path) is True
        assert bot.ambiguous_remote_post_is_blocking() is True
        raise EstablishmentObserved

    monkeypatch.setattr(
        bot,
        "require_established_installation",
        observe_establishment,
    )
    with pytest.raises(EstablishmentObserved):
        bot.main()

    assert activation.read_bytes() == activation_before
    assert audit.read_bytes() == audit_before
    assert not os.path.lexists(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    assert not os.path.lexists(bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)


@pytest.mark.parametrize(
    "exchange_state",
    ("exchange_staged", "exchange_committed"),
)
def test_literal_fresh_process_recovers_exchange_but_keeps_remote_lanes_blocked(
    exchange_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new interpreter recovers the ledger, never the owning retirement."""

    install_paths(monkeypatch, tmp_path, activate_protocol=True)
    receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    activation = bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    activation_sha256 = hashlib.sha256(activation.read_bytes()).hexdigest()
    _leave_exact_ledger_exchange(
        monkeypatch,
        receipt_path,
        fault=exchange_state,
    )

    root = Path(__file__).resolve().parents[1]
    script = r'''
import hashlib
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
state = Path(sys.argv[2])
sys.path.insert(0, str(root))
import mrsMThatcher2 as bot
import exact_receipt_retirement as retirement

bot._PRODUCTION_BOOTSTRAPPED = True
bot.acquire_instance_lock()
recovered = bot.recover_interrupted_retirement_ledger_exchanges_at_startup()
inspection = retirement.inspect_retirement_ledger(
    bot.REGULAR_POST_RECEIPT_FILE
)
try:
    bot.block_if_ambiguous_remote_post()
except bot.AmbiguousRemotePostOutcome:
    direct_preflight = "blocked"
else:
    direct_preflight = "opened"
print(json.dumps({
    "activation_sha256": hashlib.sha256(
        Path(bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE).read_bytes()
    ).hexdigest(),
    "direct_preflight": direct_preflight,
    "ledger_blocking": inspection.blocking,
    "ledger_state": inspection.state,
    "recovered": [Path(item).name for item in recovered],
    "retirement_still_blocking": bot.remote_receipt_retirement_is_blocking(),
}, sort_keys=True))
'''
    environment = dict(os.environ)
    environment.update(
        {
            "MRS_BASE_DIR": str(tmp_path),
            "MRS_LOG_FILE": str(tmp_path / "literal-ledger-restart.log"),
            "MRS_TEST_MODE": "1",
            "X_ACCESS_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "XAI_API_KEY": "dummy",
            "X_BEARER_TOKEN": "dummy",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
        }
    )
    completed = subprocess.run(
        (sys.executable, "-I", "-c", script, str(root), str(tmp_path)),
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    record = json.loads(completed.stdout.splitlines()[-1])
    assert record == {
        "activation_sha256": activation_sha256,
        "direct_preflight": "blocked",
        "ledger_blocking": False,
        "ledger_state": "completed",
        "recovered": [receipt_path.name],
        "retirement_still_blocking": True,
    }
    assert not os.path.lexists(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    assert not os.path.lexists(bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)


@pytest.mark.parametrize("damage", ("missing", "malformed", "exchange"))
def test_missing_or_unsafe_retirement_ledger_never_opens_startup_or_preflight(
    damage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an exact successor exchange is recoverable; all other damage blocks."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    ledger_path, exchange_path = retirement.retirement_ledger_paths(receipt_path)
    if damage == "missing":
        ledger_path.unlink()
    elif damage == "malformed":
        ledger_path.write_bytes(b'{"document_kind":')
        ledger_path.chmod(0o600)
    else:
        exchange_path.write_bytes(b'{"document_kind":"unrelated"}\n')
        exchange_path.chmod(0o600)

    assert bot.recover_interrupted_retirement_ledger_exchanges_at_startup() == ()
    assert retirement.retirement_ledger_is_blocking(receipt_path) is True
    assert ledger_path in bot.required_installation_files_missing()
    assert bot.remote_write_safety_protocol_is_active() is False
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(RuntimeError, match="Required durable"):
        bot.require_established_installation_after_ledger_recovery()


def test_invalid_activation_cannot_authorise_exact_ledger_exchange_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recoverable exchange is still immutable without current permission."""

    install_paths(monkeypatch, tmp_path, activate_protocol=True)
    receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    _leave_exact_ledger_exchange(
        monkeypatch,
        receipt_path,
        fault="exchange_staged",
    )
    monkeypatch.undo()
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    activation = bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    activation.chmod(0o600)
    activation.write_bytes(b"invalid activation\n")
    activation.chmod(protocol.ACTIVATION_MODE)
    _ledger, exchange = retirement.retirement_ledger_paths(receipt_path)
    exchange_before = exchange.read_bytes()
    monkeypatch.setattr(
        bot,
        "transaction_mutation_authority",
        lambda operation: issue_transaction_mutation_authority(
            lambda _operation: None,
            operation=operation,
        ),
    )

    assert bot.recover_interrupted_retirement_ledger_exchanges_at_startup() == ()
    assert exchange.read_bytes() == exchange_before
    assert retirement.inspect_retirement_ledger(receipt_path).state == (
        "exchange_staged"
    )
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_exact_exchange_recovery_does_not_mask_another_invalid_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One exact recovery may finish, but unrelated ledger damage still aborts."""

    install_paths(monkeypatch, tmp_path, activate_protocol=True)
    recoverable = bot.REGULAR_POST_RECEIPT_FILE
    invalid = bot.MEME_POST_RECEIPT_FILE
    _leave_exact_ledger_exchange(
        monkeypatch,
        recoverable,
        fault="exchange_committed",
    )
    monkeypatch.undo()
    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    invalid_ledger, _invalid_exchange = retirement.retirement_ledger_paths(
        invalid
    )
    invalid_ledger.write_bytes(b'{"document_kind":')
    invalid_ledger.chmod(0o600)
    monkeypatch.setattr(
        bot,
        "transaction_mutation_authority",
        lambda operation: issue_transaction_mutation_authority(
            lambda _operation: None,
            operation=operation,
        ),
    )

    assert bot.recover_interrupted_retirement_ledger_exchanges_at_startup() == (
        recoverable,
    )
    assert retirement.inspect_retirement_ledger(recoverable).blocking is False
    assert retirement.inspect_retirement_ledger(invalid).blocking is True
    with pytest.raises(RuntimeError, match="Required durable"):
        bot.require_established_installation_after_ledger_recovery()
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("basename", "loader"),
    [
        ("bot_state.json", "state"),
        ("lines_used.json", "used"),
        ("images_used.json", "used"),
    ],
)
def test_markerless_established_core_state_never_follows_symlinks(
    basename: str,
    loader: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy compatibility still requires one owned no-follow authority."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0
    source = tmp_path / basename
    external_dir = tmp_path / "symlink-targets"
    external_dir.mkdir()
    target = external_dir / basename
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)

    assert source in bot.required_installation_files_missing()
    with pytest.raises(RuntimeError, match="Required durable"):
        bot.require_established_installation()
    if loader == "state":
        with pytest.raises(bot.UnsafeDurableStateNamespace):
            bot.load_state()
    else:
        with pytest.raises(bot.CorruptUsedHistoryError):
            bot.load_used_set(source)


def test_stable_core_state_reader_rejects_same_inode_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state authority cannot change between its read and final identity check."""

    path = tmp_path / "state.json"
    path.write_text('{"schema_version":1}\n', encoding="utf-8")
    real_read = bot.os.read
    changed = False

    def mutate_after_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        block = real_read(descriptor, size)
        if block and not changed:
            changed = True
            path.write_text('{"schema_version":2}\n', encoding="utf-8")
        return block

    monkeypatch.setattr(bot.os, "read", mutate_after_read)
    with pytest.raises(bot.UnsafeDurableStateNamespace, match="changed while reading"):
        bot.read_stable_owned_json_bytes_no_follow(path)
    assert changed is True


def test_core_state_writers_never_follow_predictable_temporary_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy .tmp names cannot redirect state or used-history publication."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    external = tmp_path / "external-sentinel.txt"
    external.write_text("untouched", encoding="utf-8")
    bot.STATE_FILE.with_suffix(".tmp").symlink_to(external)
    bot.LINES_USED_FILE.with_suffix(".json.tmp").symlink_to(external)

    bot.save_state(bot.default_state(), durable=True)
    bot.save_used_set(bot.LINES_USED_FILE, {"a", "b"}, durable=True)

    assert external.read_text(encoding="utf-8") == "untouched"
    loaded = bot.load_state()
    assert loaded.pop("_state_generation")["sequence"] >= 1
    assert loaded == bot.default_state()
    assert bot.load_used_set(bot.LINES_USED_FILE) == {"a", "b"}


def test_initialisation_acquires_singleton_before_namespace_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent daemon is excluded before fresh-state absence is trusted."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    order: list[str] = []
    original_lstat = os.lstat

    monkeypatch.setattr(
        bot,
        "acquire_instance_lock",
        lambda: order.append("instance_lock"),
    )

    def observed_lstat(path: object, *args: object, **kwargs: object):
        assert order == ["instance_lock"]
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(bot.os, "lstat", observed_lstat)
    assert bot.initialise_installation() == 0


def test_initialisation_cleans_outbox_after_reported_post_replace_failure(
    tmp_path, monkeypatch
):
    """A durable outbox followed by an error cannot strand a partial install."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    real_write = outbox_module._atomic_write_json

    def write_then_report_failure(path, value):
        real_write(path, value)
        raise OSError("injected outbox parent-directory fsync failure")

    monkeypatch.setattr(outbox_module, "_atomic_write_json", write_then_report_failure)
    with pytest.raises(OSError, match="outbox parent-directory fsync"):
        bot.initialise_installation()

    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(outbox_module, "_atomic_write_json", real_write)
    assert bot.initialise_installation() == 0


def test_initialisation_cleans_state_after_reported_post_replace_failure(
    tmp_path, monkeypatch
):
    """The first durable target is also pre-registered for rollback."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    real_fsync_parent = bot.fsync_parent_dir

    def fail_state_parent(path, *, strict=False):
        if path == bot.STATE_FILE:
            raise OSError("injected state parent-directory fsync failure")
        return real_fsync_parent(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", fail_state_parent)
    with pytest.raises(OSError, match="state parent-directory fsync"):
        bot.initialise_installation()

    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(bot, "fsync_parent_dir", real_fsync_parent)
    assert bot.initialise_installation() == 0


def test_initialisation_registers_state_before_meme_schedule_can_persist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Early meme schedule persistence is inside the rollback transaction."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)

    def fail_latest_backup(*_args, **_kwargs):
        raise OSError("injected first state-backup failure")

    monkeypatch.setattr(bot._state_persistence.StateBackups, "copy", fail_latest_backup)
    with pytest.raises(bot.StateBackupWriteError, match="latest backup"):
        bot.initialise_installation()

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("daily_memes_enabled", (False, True))
def test_initialisation_sentinel_precedes_first_data_write(
    daily_memes_enabled: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable in-progress sentinel exists before either state-write path."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", daily_memes_enabled)
    first_write_observations: list[dict[str, object]] = []
    sentinel_parent_syncs: list[Path] = []
    real_atomic_write = bot.atomic_write_json
    real_fsync_parent = bot.fsync_parent_dir

    def observe_sentinel_write(path, value, *, durable=False):
        if Path(path) == bot.INSTALLATION_IN_PROGRESS_FILE:
            assert durable is True
        return real_atomic_write(path, value, durable=durable)

    def observe_parent_sync(path, *, strict=False):
        result = real_fsync_parent(path, strict=strict)
        if Path(path) == bot.INSTALLATION_IN_PROGRESS_FILE:
            assert strict is True
            sentinel_parent_syncs.append(Path(path))
        return result

    def hard_exit_at_first_data_write(*_args, **_kwargs):
        sentinel = json.loads(
            bot.INSTALLATION_IN_PROGRESS_FILE.read_text(encoding="utf-8")
        )
        first_write_observations.append(sentinel)
        assert sentinel["state"] == "initialising"
        assert sentinel_parent_syncs == [bot.INSTALLATION_IN_PROGRESS_FILE]
        assert not bot.STATE_FILE.exists()
        raise SystemExit(73)

    monkeypatch.setattr(bot, "atomic_write_json", observe_sentinel_write)
    monkeypatch.setattr(bot, "fsync_parent_dir", observe_parent_sync)
    monkeypatch.setattr(bot, "save_state", hard_exit_at_first_data_write)
    with pytest.raises(SystemExit, match="73"):
        bot.initialise_installation()

    assert len(first_write_observations) == 1
    assert bot.INSTALLATION_IN_PROGRESS_FILE.is_file()
    assert not bot.STATE_FILE.exists()
    with pytest.raises(RuntimeError, match="initialising"):
        bot.require_established_installation()


def test_interrupted_new_initialisation_remains_fail_closed_and_legacy_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new sentinel blocks partial state without rejecting legacy state."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    real_save_quote_used_hashes = bot.save_quote_used_hashes

    def hard_exit_before_lines(*_args, **_kwargs):
        raise SystemExit(73)

    monkeypatch.setattr(bot, "save_quote_used_hashes", hard_exit_before_lines)
    with pytest.raises(SystemExit, match="73"):
        bot.initialise_installation()

    assert bot.INSTALLATION_IN_PROGRESS_FILE.is_file()
    assert bot.STATE_FILE.is_file()
    with pytest.raises(RuntimeError, match="initialising"):
        bot.require_established_installation()
    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    # Build a complete current-schema installation, then model the deployed
    # marker-less legacy layout by removing only its new marker.  Exercise the
    # actual production-mode state, history and outbox readers rather than
    # treating pathname presence alone as compatibility evidence.
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    install_paths(monkeypatch, legacy_root, activate_protocol=False)
    monkeypatch.setattr(
        bot,
        "save_quote_used_hashes",
        real_save_quote_used_hashes,
    )
    assert bot.initialise_installation() == 0
    bot.INSTALLATION_MARKER_FILE.unlink()
    monkeypatch.setattr(bot, "TEST_MODE", False)
    bot.require_established_installation()
    loaded_state = bot.load_state()
    assert loaded_state["next_quote_post_epoch"] > 0
    assert loaded_state["daily_reply_count"] == 0
    assert bot.historical_context_reply_store().history() == {
        "schema_version": 1,
        "items": {},
    }
    assert bot.historical_context_outbox_store().snapshot()["obligations"] == {}


def test_initialisation_cleanup_continues_past_unexpected_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One non-file residue cannot mask failure or strand other state files."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    real_atomic_write = bot.atomic_write_json

    def replace_marker_with_directory(path, value, *, durable=False):
        if Path(path) == bot.INSTALLATION_MARKER_FILE:
            Path(path).mkdir()
            raise OSError("injected marker publication failure")
        return real_atomic_write(path, value, durable=durable)

    monkeypatch.setattr(bot, "atomic_write_json", replace_marker_with_directory)
    with pytest.raises(OSError, match="marker publication failure") as raised:
        bot.initialise_installation()

    assert any(
        "IsADirectoryError" in detail
        for detail in raised.value.initialisation_cleanup_failures
    )
    assert list(tmp_path.iterdir()) == [bot.INSTALLATION_MARKER_FILE]
    assert bot.INSTALLATION_MARKER_FILE.is_dir()
    with pytest.raises(RuntimeError, match="Required durable"):
        bot.require_established_installation()


def test_production_factories_reject_disappeared_history_and_outbox(
    tmp_path, monkeypatch
):
    """Production wiring never converts established-state loss to empty state."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.unlink()
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.unlink()
    monkeypatch.setattr(bot, "TEST_MODE", False)

    with pytest.raises(RuntimeError, match="history is missing"):
        bot.historical_context_reply_store().history()
    with pytest.raises(outbox_module.OutboxValidationError, match="outbox is missing"):
        bot.historical_context_outbox_store().snapshot()
    with pytest.raises(RuntimeError, match="Required durable"):
        bot.require_established_installation()


@pytest.mark.parametrize(
    "barrier_attribute",
    (
        "AMBIGUOUS_POST_OUTCOME_FILE",
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
    ),
)
def test_initialisation_rejects_dangling_safety_barrier_namespace(
    barrier_attribute: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dangling barrier symlink is existing state, not an absent pathname."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    barrier = Path(getattr(bot, barrier_attribute))
    barrier.symlink_to(tmp_path / "missing-barrier-target")

    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    assert barrier.is_symlink()


@pytest.mark.parametrize(
    "namespace_name",
    (
        bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE).name,
        bot.fence_path_for_journal(
            bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE)
        ).name,
        bot.MEDIA_UPLOAD_RECEIPT_FILE.name,
        bot.media_fence_path_for_receipt(bot.MEDIA_UPLOAD_RECEIPT_FILE).name,
        f"{bot.JOURNAL_STAGING_PREFIX}focused-token",
        f"{bot.JOURNAL_RETIREMENT_PREFIX}{'a' * 64}",
        f"{bot.MEDIA_TRANSITION_PREFIX}focused-token",
        f"{bot.MEDIA_RETIREMENT_GUARD_PREFIX}focused-token",
    ),
)
def test_initialisation_rejects_every_remote_transport_namespace(
    namespace_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial remote transaction can never become a fresh installation."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    orphan = tmp_path / namespace_name
    orphan.write_text("orphan\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="partially established"):
        bot.initialise_installation()

    assert orphan.read_bytes() == b"orphan\n"
    assert not bot.INSTALLATION_MARKER_FILE.exists()


def test_initialisation_refuses_preexisting_activation_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash-left audit is existing installation state, not a fresh root."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    audit = tmp_path / ACTIVATION_AUDIT_BASENAME
    audit.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    assert audit.read_bytes() == b"{}\n"


def test_initialisation_never_uses_the_unaudited_activation_shortcut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New durable state remains write-disabled pending stopped activation."""

    install_paths(monkeypatch, tmp_path, activate_protocol=False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0

    assert not os.path.lexists(tmp_path / ACTIVATION_AUDIT_BASENAME)
    assert not os.path.lexists(tmp_path / ACTIVATION_BASENAME)
    assert bot.remote_write_safety_protocol_is_active() is False


def test_remote_preflight_rejects_activation_pair_torn_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pair namespace race cannot open the actual shared remote preflight."""

    install_paths(monkeypatch, tmp_path, activate_protocol=True)
    real_inspect = protocol._inspect_stable_regular_at

    def inspect_then_remove_sentinel(
        directory_fd: int,
        basename: str,
        **kwargs,
    ):
        inspected = real_inspect(directory_fd, basename, **kwargs)
        if basename == protocol.ACTIVATION_AUDIT_BASENAME:
            os.unlink(protocol.ACTIVATION_BASENAME, dir_fd=directory_fd)
        return inspected

    monkeypatch.setattr(
        protocol,
        "_inspect_stable_regular_at",
        inspect_then_remove_sentinel,
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="protocol is not durably activated",
    ):
        bot.require_remote_operation_unpaused("synthetic remote lane")

    assert not os.path.lexists(tmp_path / ACTIVATION_BASENAME)
    assert os.path.lexists(tmp_path / ACTIVATION_AUDIT_BASENAME)


def test_state_backup_is_an_existing_recovery_candidate(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    (tmp_path / "lines_used.json").write_text("[]")
    (tmp_path / "images_used.json").write_text("[]")
    (tmp_path / "bot_state.json.bak1").write_text("{}")
    bot.historical_context_reply_store(
        allow_missing_history=True
    ).initialise_empty_history()
    bot.historical_context_outbox_store().initialise_empty()
    bot.require_established_installation()


@pytest.mark.parametrize(
    "value,valid",
    [
        (True, False), (False, False), (1, True), (0, True), (1.0, True),
        (1.5, False), ("1", False), ("2026-07-11 12:00", False), ("2026-07-11 12:00+00:00", True),
        (None, False), (math.nan, False), (math.inf, False), (-1, False),
        (bot.MAX_REASONABLE_STATE_EPOCH + 1, False),
    ],
)
def test_control_timestamp_types(value, valid):
    if valid:
        assert type(bot.parse_control_time(value)) is int
    else:
        with pytest.raises(ValueError):
            bot.parse_control_time(value)


@pytest.mark.parametrize("value", [None, True, 123, 1.5, [], {}, "", " model "])
def test_string_config_requires_clean_json_string(value):
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("XAI_MODEL", value, "grok-4.3")
    assert bot._coerce_local_config_value("XAI_MODEL", "grok-test", "grok-4.3") == "grok-test"


def test_control_cache_detects_atomic_replace_same_mtime(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": True}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {"signature": None, "data": {}, "has_valid": False, "failure_signature": None})
    assert bot.load_control()["disable_all"] is True
    old = path.stat()
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps({"disable_all": False}))
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(replacement, path)
    assert bot.load_control()["disable_all"] is False


def test_explicit_canonical_log_expands_only_numeric_rotations(tmp_path):
    current = tmp_path / "mrsMThatcher.log"
    current.write_text("")
    for name in ("mrsMThatcher.log.1", "mrsMThatcher.log.3", "mrsMThatcher.log.selftest", "other.log.1"):
        (tmp_path / name).write_text("")
    paths = digest.resolve_explicit_logs([Path("mrsMThatcher.log"), current.with_name("mrsMThatcher.log.1")], tmp_path)
    assert [path.name for path in paths] == ["mrsMThatcher.log", "mrsMThatcher.log.1", "mrsMThatcher.log.3"]


def test_digest_execution_lock_is_nonblocking_and_released(tmp_path):
    lock = tmp_path / "digest.lock"
    with digest.digest_execution_lock(lock):
        with pytest.raises(RuntimeError, match="Another digest process"):
            with digest.digest_execution_lock(lock):
                pass
    with digest.digest_execution_lock(lock):
        pass


def test_digest_main_releases_lock_after_failure(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    log = project / "mrsMThatcher.log"
    log.write_text("")
    monkeypatch.setattr(digest, "run_digest", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("render failed")))
    args = ["--project-dir", str(project), "--state-file", "resume.json", str(log)]
    with pytest.raises(RuntimeError, match="render failed"):
        digest.main(args)
    lock = project / "resume.json.lock"
    with digest.digest_execution_lock(lock):
        pass


def test_no_state_stdout_run_does_not_take_digest_lock(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    log = project / "mrsMThatcher.log"
    log.write_text("")
    monkeypatch.setattr(digest, "run_digest", lambda *_a, **_k: 7)
    assert digest.main(["--project-dir", str(project), "--no-state", str(log)]) == 7
    assert not (project / ".mrs_log_digest_state.json.lock").exists()


def test_offline_digest_runway_keeps_historical_defaults():
    expected = {
        "POST_SLEEP_MIN": bot.POST_SLEEP_MIN,
        "POST_SLEEP_MAX": bot.POST_SLEEP_MAX,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
        "ENABLE_GENERATED_IMAGE_POOL": False,
    }
    assert digest.RUNWAY_CONFIG_DEFAULTS == expected


def test_ambiguous_remote_post_creates_durable_blocker(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_a, **_k: (_ for _ in ()).throw(bot.AmbiguousRemotePostOutcome("timeout", service="x")),
    )
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        create_bound_context_post()
    marker = json.loads((tmp_path / "ambiguous_post_outcome.json").read_text())
    assert marker["outcome"] == "ambiguous_remote_post"
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Unreconciled ambiguous"):
        bot.block_if_ambiguous_remote_post()


def test_ambiguous_remote_post_blocks_process_when_marker_write_fails(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    calls = 0

    def ambiguous_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise bot.AmbiguousRemotePostOutcome("timeout", service="x")

    monkeypatch.setattr(bot, "x_request", ambiguous_request)
    receipt = prepare_context_create(text="first")
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage unavailable")),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="timeout"):
        create_bound_context_post("first", receipt=receipt)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="in-process remote-write safety latch"):
        create_bound_context_post("first", receipt=receipt)

    assert calls == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_x_server_error_on_post_creates_durable_ambiguity_barrier(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_paths(monkeypatch, tmp_path)

    class Response:
        text = '{"detail":"upstream unavailable"}'
        headers: dict[str, str] = {}

        def __init__(self, status: int) -> None:
            self.status_code = status

    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: Response(status_code))

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        create_bound_context_post()

    assert caught.value.status_code == status_code
    marker = json.loads((tmp_path / "ambiguous_post_outcome.json").read_text(encoding="utf-8"))
    assert marker["outcome"] == "ambiguous_remote_post"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize("status_code", [400, 403, 429])
def test_x_client_error_without_provider_contract_creates_ambiguity_barrier(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_paths(monkeypatch, tmp_path)

    class Response:
        text = '{"detail":"request rejected"}'
        headers: dict[str, str] = {}

        def __init__(self, status: int) -> None:
            self.status_code = status

    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: Response(status_code))

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        create_bound_context_post()

    assert caught.value.status_code == status_code
    marker = json.loads(
        (tmp_path / "ambiguous_post_outcome.json").read_text(encoding="utf-8")
    )
    assert marker["outcome"] == "ambiguous_remote_post"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "lane",
    [
        "regular_quote",
        "meme",
        "mention",
        "quote_tweet",
        "historical_context",
        "direct_create",
        "direct_media_upload",
    ],
)
@pytest.mark.parametrize(
    "barrier_kind",
    [
        "durable_marker",
        "confirmed_persistence_in_process",
        "durability_uncertain",
    ],
)
def test_existing_ambiguity_marker_blocks_each_lane_before_preparation(
    lane: str,
    barrier_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter

    install_paths(monkeypatch, tmp_path)
    if barrier_kind == "durable_marker":
        (tmp_path / "ambiguous_post_outcome.json").write_text("{}\n", encoding="utf-8")
    elif barrier_kind == "confirmed_persistence_in_process":
        monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)
    else:
        monkeypatch.setattr(
            bot,
            "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN",
            True,
        )
    calls: list[str] = []

    def prepared(name: str):
        calls.append(name)
        pytest.fail(f"{name} preparation must not run after an ambiguous post")

    if lane == "regular_quote":
        monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *_args: prepared("receipt reconciliation"))
        invoke = lambda: bot.post_random_quote(set(), set(), bot.default_state())
    elif lane == "meme":
        monkeypatch.setattr(bot, "both_main_post_receipts_exist", lambda: prepared("meme receipt check"))
        invoke = lambda: bot.post_next_meme(bot.default_state())
    elif lane == "mention":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "get_mentions", lambda *_args: prepared("mention fetch"))
        invoke = lambda: bot.maybe_reply_to_mentions(bot.default_state())
    elif lane == "quote_tweet":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda *_args: prepared("quote lookup"))
        invoke = lambda: bot.maybe_reply_to_quote_tweets(bot.default_state())
    elif lane == "historical_context":
        monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
        monkeypatch.setattr(
            historical_context_formatter,
            "load_and_validate_corpus",
            lambda *_args: prepared("historical research load"),
        )
        invoke = lambda: bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )
    elif lane == "direct_create":
        monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: prepared("X request"))
        receipt = prepare_context_create()
        invoke = lambda: create_bound_context_post(receipt=receipt)
    else:
        monkeypatch.setattr(bot, "upload_media_v2", lambda *_args, **_kwargs: prepared("media upload"))
        monkeypatch.setattr(bot, "upload_media_v1_1", lambda *_args, **_kwargs: prepared("media upload fallback"))
        invoke = lambda: bot.upload_media(
            str(tmp_path / "image.png"),
            lane="quote_image",
        )

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Unreconciled"):
        invoke()

    assert calls == []


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"not json", "non-JSON"), (b"[]", "JSON object")],
)
def test_success_status_malformed_body_is_ambiguous_only_for_writes(
    monkeypatch,
    tmp_path,
    body,
    message,
):
    install_paths(monkeypatch, tmp_path)
    response = bot.requests.Response()
    response.status_code = 200
    response._content = body
    monkeypatch.setattr(bot.requests, "request", lambda *args, **kwargs: response)

    payload = {"text": "test"}
    authority = armed_context_transport_authority(payload)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match=message):
        bot.x_request(
            "POST",
            "/2/tweets",
            ambiguous_write=True,
            json=payload,
            _remote_write_authorization=authority,
        )
    with pytest.raises(bot.ApiError, match=message) as exc_info:
        bot.x_request("GET", "/2/users/me")
    monkeypatch.setattr(bot, "X_BEARER_TOKEN", "test-token")
    with pytest.raises(bot.ApiError, match=message) as bearer_exc_info:
        bot.x_bearer_request("GET", "/2/users/me")

    assert type(exc_info.value) is bot.ApiError
    assert type(bearer_exc_info.value) is bot.ApiError


@pytest.mark.parametrize(
    "page",
    [
        {"data": {}},
        {"data": ["not-an-object"]},
        {"includes": []},
        {"includes": {"users": {"id": "1"}}},
        {"includes": {"media": "bad"}},
        {"meta": []},
    ],
)
def test_paginated_get_rejects_malformed_page_sections(page):
    with pytest.raises(bot.ApiError, match="malformed paginated response"):
        bot.x_paginated_get(
            lambda _path, _params: page,
            "/2/test",
            {},
            max_pages=1,
            label="test",
        )


@pytest.mark.parametrize("data", [[], "not-an-object", 7])
def test_get_tweet_by_id_rejects_malformed_data(monkeypatch, data):
    monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: {"data": data})

    with pytest.raises(bot.ApiError, match="malformed tweet data"):
        bot.get_tweet_by_id("123")
