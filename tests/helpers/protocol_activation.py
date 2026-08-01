"""Test-only construction of an externally attested protocol activation pair."""

from __future__ import annotations

import os
from pathlib import Path

import remote_write_safety_protocol as protocol
from exact_receipt_retirement import (
    initialise_retirement_ledger,
    retirement_ledger_contract_sha256,
    retirement_ledger_inventory_sha256,
)
from transaction_mutation_authority import issue_transaction_mutation_authority


def initialise_test_retirement_ledgers(parent: Path) -> tuple[Path, ...]:
    """Create the four genesis ledgers in one disposable fixture directory."""

    parent = Path(parent)
    receipt_paths = tuple(
        parent / name for name in protocol.RETIREMENT_LEDGER_RECEIPT_BASENAMES
    )
    authority = issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="isolated test protocol ledger initialisation",
    )
    for receipt_path in receipt_paths:
        initialise_retirement_ledger(
            receipt_path,
            mutation_authority=authority,
        )
    return receipt_paths


def create_test_protocol_activation(path: Path) -> protocol.ProtocolActivationSnapshot:
    """Create a structurally valid activation pair for an isolated fixture.

    Production code has no unaudited new-install writer.  Tests use this
    explicit helper only inside disposable directories; it exercises the
    low-level publication primitive with a syntactically valid established-
    install audit without claiming that an operator attestation occurred.
    """

    path = Path(path)
    if path.name != protocol.ACTIVATION_BASENAME:
        raise ValueError("test activation must use the fixed basename")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd = os.open(path.parent, flags)
    try:
        receipt_paths = initialise_test_retirement_ledgers(path.parent)
        identity = os.fstat(directory_fd)
        audit = protocol.build_established_install_activation_audit_bytes(
            project_device=int(identity.st_dev),
            project_inode=int(identity.st_ino),
            clean_state_attestation_sha256="1" * 64,
            clean_state_attestation_size=1,
            activator_cli_sha256="2" * 64,
            reconciliation_reference="isolated-test-fixture-only",
            retirement_ledger_contract_sha256_value=(
                retirement_ledger_contract_sha256(receipt_paths)
            ),
            retirement_ledger_initial_inventory_sha256=(
                retirement_ledger_inventory_sha256(receipt_paths)
            ),
        )
        return protocol._create_or_revalidate_protocol_activation_at(
            directory_fd,
            activation_audit_bytes=audit,
        )
    finally:
        os.close(directory_fd)
