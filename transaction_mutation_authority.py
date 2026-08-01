#!/usr/bin/env python3
"""Process-local, verifier-backed authority for transaction mutations.

Low-level receipt and journal helpers cannot import the application daemon
without creating a dependency cycle.  Their callers therefore pass this
opaque authority.  Issuance and every use invoke the supplied verifier; the
production verifier proves ownership of the process-lifetime instance lock.

The authority is deliberately process-bound.  It cannot be inherited across
``fork`` and it is not serialisable.  This protects supported callers from
accidentally performing destructive transaction work without proving current
ownership; it is not an operating-system security boundary against arbitrary
same-UID code.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Final


class TransactionMutationAuthorityError(RuntimeError):
    """A destructive transaction mutation lacks current lock authority."""


_CONSTRUCTION_SECRET: Final = object()


class TransactionMutationAuthority:
    """Opaque verifier-backed authority valid only in its issuing process."""

    __slots__ = ("__pid", "__verifier")

    def __init__(
        self,
        secret: object,
        *,
        verifier: Callable[[str], None],
    ) -> None:
        if secret is not _CONSTRUCTION_SECRET:
            raise TransactionMutationAuthorityError(
                "transaction mutation authority cannot be constructed directly"
            )
        self.__pid = os.getpid()
        self.__verifier = verifier

    def _verify(self, operation: str) -> None:
        if os.getpid() != self.__pid:
            raise TransactionMutationAuthorityError(
                f"{operation} refused a transaction authority inherited across fork"
            )
        self.__verifier(operation)

    def __reduce__(self) -> object:
        raise TypeError("transaction mutation authority is not serialisable")


def issue_transaction_mutation_authority(
    verifier: Callable[[str], None],
    *,
    operation: str,
) -> TransactionMutationAuthority:
    """Issue an opaque token after immediately running ``verifier``."""

    if not callable(verifier):
        raise TransactionMutationAuthorityError(
            "transaction mutation authority requires a callable verifier"
        )
    verifier(f"{operation} authority issuance")
    return TransactionMutationAuthority(
        _CONSTRUCTION_SECRET,
        verifier=verifier,
    )


def require_transaction_mutation_authority(
    authority: TransactionMutationAuthority | None,
    *,
    operation: str,
) -> None:
    """Re-run the bound verifier immediately before destructive work."""

    if not isinstance(authority, TransactionMutationAuthority):
        raise TransactionMutationAuthorityError(
            f"{operation} requires verified transaction mutation authority"
        )
    authority._verify(operation)
