"""Focused coverage for stopped external confirmation of published replies."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import pytest

import exact_receipt_retirement as receipt_retirement
import remote_write_transport_journal as journal
import remote_write_safety_protocol as safety_protocol
from tests.helpers.protocol_activation import create_test_protocol_activation
from tools import reconcile_remote_write_safety_marker as reconcile
from transaction_mutation_authority import (
    TransactionMutationAuthorityError,
    issue_transaction_mutation_authority,
)


TARGET_ID = "2092250326669668442"
POST_ID = "2092250943345557810"
CONVERSATION_ID = "2091959575670899194"
CONFIRMATION_EPOCH = 1_787_666_471
ATTEMPT_EPOCH = 1_787_666_470
REPLY_TEXT = (
    "Resolve gives political ideas force, but they must still be judged by "
    "whether they address people’s real concerns."
)
TEXT_SHA256 = hashlib.sha256(REPLY_TEXT.encode("utf-8")).hexdigest()

# Exact credential-free source bytes retained as an acceptance fixture from the
# incident audit.  Base64 keeps the canonical JSON whitespace and escapes
# byte-for-byte stable while avoiding any dependency on production state.
AUDITED_SOURCE_RECEIPT_BYTES = base64.b64decode(
    """
ewogICJhaV9yZXBseV9kcmFmdCI6IHsKICAgICJhcHByb3ZhbF9oYXNoIjogImYxZWNjMWI4YWM0ZGZmNTBlZWE2
YWY5MGJkNjdiOTM1ZmM5YmJmMmVlNGRkM2U4ZGMxODA0ZWY4NDFlYmI0ZGEiLAogICAgImNhbmRpZGF0ZV9zb3Vy
Y2UiOiAibWVudGlvbiIsCiAgICAiY2xhaW1fcmlza19jYXRlZ29yaWVzIjogW10sCiAgICAiY29udGV4dF9oYXNo
IjogIjdiNGIwMDk4OTI1MWY2NDQ2YWNjNGE5OWVjMjc5NjE4YzVhNzc1NDE2ZGMxODcyMWFjOWEwNjljOTc5NjFj
M2YiLAogICAgImNvbnRyaWJ1dGlvbl9oYXNoIjogImE2ZmE1ZjY1YzgxNDY4MDc1ZDdlZDE2MjFjNDBhNmZmY2Qw
N2IxMzc0OWNiOTg2NTBlNzZmY2E4N2Q1NzgxNjIiLAogICAgImNyZWF0aW9uX3RpbWUiOiAiMjAyNi0wOC0yNVQx
NDowMToxMC4xNzI5MzZaIiwKICAgICJldmlkZW5jZV9pZHMiOiBudWxsLAogICAgImZhY3R1YWxfY2xhaW1zIjog
W10sCiAgICAiZmluYWxfcmVwbHlfa2luZCI6ICJ1bmtub3duIiwKICAgICJtb2RlIjogIm9waW5pb25fb3JfcHJp
bmNpcGxlIiwKICAgICJtb2RlbF9jYWxsX2NvdW50IjogMiwKICAgICJwcm9wb3NlZF9yZXBseSI6ICJSZXNvbHZl
IGdpdmVzIHBvbGl0aWNhbCBpZGVhcyBmb3JjZSwgYnV0IHRoZXkgbXVzdCBzdGlsbCBiZSBqdWRnZWQgYnkgd2hl
dGhlciB0aGV5IGFkZHJlc3MgcGVvcGxlXHUyMDE5cyByZWFsIGNvbmNlcm5zLiIsCiAgICAicmVwbHlfcmVxdWly
ZW1lbnQiOiAiZ2VuZXJhbCIsCiAgICAicmV2aWV3ZXJfdmVyZGljdCI6ICJhcHByb3ZlIiwKICAgICJyZXZpc2lv
bl9jb3VudCI6IDAsCiAgICAicm91dGVfc291cmNlIjogInhhaV9nYXRlIiwKICAgICJzY2hlbWFfdmVyc2lvbiI6
IDEsCiAgICAic3RyYXRlZ3lfdmVyc2lvbiI6ICJ0ZXN0ZWQtcmVwbHktcGlwZWxpbmUtMjAyNjA4MTciLAogICAg
InRhcmdldF9pZCI6ICIyMDkyMjUwMzI2NjY5NjY4NDQyIiwKICAgICJ0aHJlYWRfaWQiOiAiMjA5MTk1OTU3NTY3
MDg5OTE5NCIsCiAgICAidG9uZSI6ICJ1bmtub3duIiwKICAgICJ0cnVzdGVkX2ZhY3RfaWRzX3N1cHBsaWVkIjog
WwogICAgICAiMDIyMDlkZDNmYTAwYzUzZmFjZTAyMjAyZjIzN2VjNjFhYWRhYTc2NjM5ZWJiNmM0N2ZhNTI1MDcz
OWU5YjUzOCIsCiAgICAgICIwMmY2Yjg5MGE3ZDNiN2ZmNTBjMmQyN2YwOWYyMmZlZjFjNDBhZWM1MWMyOGRmOWJl
NDU5NzRlNjE1OGE0MzU2IiwKICAgICAgIjAzNmY2MGVmMzU3OWY0Njc2MmMxMDAwMWVhNzNkNGYxMDUwMDdkMzVj
MmNjZTUzNjk5MTE1MGZlZjE3OGVjNzMiLAogICAgICAiMDRlMmJlMDkyYTA5MmRlZjQzZjFiOWY1ZmNkMmQxOTY5
YTUzYmM1OWRhNjAxZjNkNmIxYzNjNDM5YzNiYTk3MiIsCiAgICAgICIwYjlhMDdjODQwMzkxZTBhMWUyNzk3NTYx
YjU3MTYyMGIxNDJmMTdmYjQ1YzBlNzYxZmQzOTE1YjMzM2RhZmU5IiwKICAgICAgIjEzNGEwNmEyMzFlNTBjOGEz
ODY3MjIzNDE4ZWI5YWM2NDY1NzNkMGIzNWZjMzgyNjExYjkwNWVmYWNiMTJlZGYiLAogICAgICAiMTM1ZTk2M2Ix
N2Q3NTY4MGY5MzcyNGMzYTVhY2NhNDUxZGZiNDczNTIyOTM2NzQwOTI2YWFkNWQzZTFiNTBjOCIsCiAgICAgICIy
MGM2NDE5MDBlN2ZjN2Y1OTZiNzM1Yjc3YmI3ZmI2MzZiODYwOTU4ZWFhMDQyOTYwZTRmNTJjYzFlMzJhMmU3IiwK
ICAgICAgIjJmNjY2NGIyZTM3MThmMDRlMjM2ZGUyMmEwZjkwYjMwODhkZDE5MzViZGExMmJjYzIzZjFkNGFjYzRi
Y2M1NGEiLAogICAgICAiMzRlMjIzMjRmZTQzMmQ3ZWUzMzI3MDM1ZTQ4Y2UxZDU3ZDU4ODNhMDUyNTM5ZTM5NzIx
MzIxZTE1MGFmYTIzYyIsCiAgICAgICIzNzc4MTY3MjMwNjZhNzNkNjE0OTk4MzI4ZDgyZTE4ZDlhYjdlNjY1MWU2
OTdmMmQwM2FjNDE4MTVhMzliMTUzIiwKICAgICAgIjNjYjdmZmI5MDJjY2FhZDZiMjQ0OTAwNTY5NThmZjBkNjVj
Nzg5MWYxY2EwYjNhZGQ2ZDUzZWFhMWUxNWZkZTEiLAogICAgICAiNGVhNjU0NWU1YzdiM2IwNWQyZjVmMTM2NmQ1
OGNmYjNlMjMwODQyNTY2OTJkYmE3ZWYzYjRkNGI0YzIwOGI4ZCIsCiAgICAgICI3YWUwMTE1NDM3MjllMWU2NjM5
M2JmMGMzYWMzMjQ5NDI3ZGEwZGU1ZTc5YTJjZjY1ZTIzMzk0YTliNWUxYjFlIiwKICAgICAgIjk5YTg4ZWEyZmIz
MzNhZWQxNGIxMDEzZTMxMjkzMjQxOTgwNWFkZDIxNmQ4NTQzMjNjYjUwOGQyZTJhMDU3OTAiLAogICAgICAiOWQx
ZWZkMTIwM2Q4ZDQ4OTQ4NTY3MmJjYmI3ZmY1OWVlYmQ1NzM2MzFjNDliOWNmYWVmNWZlZWZjMWEwM2IyZiIsCiAg
ICAgICJiMjkxNWIwYTk4YzQ3ZmRlNGZhMjIxM2UxZjg1YWU1Y2Q4MWFjNDgwNjNiMWZhYjAwMmZhNGMzZjJjMGRm
YmRlIiwKICAgICAgImI4NzA2Y2E0NmM3YjMwYzQ0NTY4YzA5OThhMjBmMzJiZTZkZWExNWNiMGI5OWJiNTQ3NWE5
ZmQ3ODBjZDhjYTgiLAogICAgICAiY2M0MTMyYWFlY2ZkOTg2YmQ4NDEwN2I4Mjc5N2JmMWI2NzliZDZkMmRjOWEy
YjQyNGI4NjhmZDJhYjhhZTc2OSIsCiAgICAgICJjZjQ1YThjZjAwMjZiNDM4OGM5NDE0Y2M4ZGE5ZTU1ZjUwYjc1
ZWRlMTBkNTllMmNmZjI1ODJjMGY2OWIxMDA4IiwKICAgICAgImQ1NWViN2RlMmMxM2MzOTc5NDJmZDM3ZGUzNjgy
ZGJlYzhlNjgyNWFjYjM1MDBjZDE5NzExODIxMDY0NzBjNTgiLAogICAgICAiZTM0MzE1ZGRmZDZkZGU5MGY1MTQ1
ODY1MmViOGYyZjA3YWRlYTMwMzEzMWRlZDczNGYwNmM4OGM1YzY1NDhlYiIsCiAgICAgICJlZTJiNDQ3NDI0MzIx
NTkxMTM4YWM2NmJhNTYzZDIxMDA1ZmJkYmI0ODc4NzhmYTE2M2M3Zjg0ZWFhOTJhNzNmIiwKICAgICAgImYyOGNj
ZjQyYmJlMjRlNTdjMzk2YzZiN2I2YzViZjYxNWUyYmYxNjA2ZGFhZTgxZmUxNzU1NDUzMzUzZmQzZGYiCiAgICBd
LAogICAgInRydXN0ZWRfZmFjdHNfaGFzaCI6ICIzMGU5MzhiYmI1ODU2YmNkNjBiNWQzM2UxNDAyZjg0OTg2MWQw
YmNiNTRlNDQwNGY5ZGY2Y2JmYTIxYjhjYmRkIiwKICAgICJ0cnVzdGVkX2ZhY3RzX3N1cHBsaWVkX2NvdW50Ijog
MjQsCiAgICAidXNlZF9mYWN0X2NvdW50IjogInVua25vd24iLAogICAgInVzZWRfZmFjdF9pZHMiOiBudWxsCiAg
fSwKICAiYXR0ZW1wdF9lcG9jaCI6IDE3ODc2NjY0NzAsCiAgImF1dGhvcl9pZCI6ICIxNDkxODA2MDM5MjEzMTU0
MzA1IiwKICAiY2FuZGlkYXRlX3NvdXJjZSI6ICJtZW50aW9uIiwKICAiY29udmVyc2F0aW9uX2lkIjogIjIwOTE5
NTk1NzU2NzA4OTkxOTQiLAogICJkYWlseV9yZXBseV9kYXRlIjogIjIwMjYtMDgtMjUiLAogICJsaWZlY3ljbGVf
c3RhdGUiOiAic2VuZGluZyIsCiAgInJlcGx5X2NvbnRleHQiOiB7CiAgICAiY2xhcmlmaWNhdGlvbl9yZXF1ZXN0
IjogbnVsbCwKICAgICJjdXJyZW50X2RhdGUiOiAiMjAyNi0wOC0yNSIsCiAgICAiaW5jb21pbmdfY29udHJpYnV0
aW9uIjogIkBNcnNNVGhhdGNoZXIgRGUgYWNlZWEgXHUwMjE5aSBudSBhIHB1dHV0IHRyYWR1c1x1MDEwMyBpbiBp
ZGVpbGUgZWkgcGVudHJ1IGNcdTAxMDMgYSBmb3N0IGRlIGZpZXIgIVx1ZDgzZFx1ZGM0ZiIsCiAgICAibGFuZSI6
ICJtZW50aW9uIiwKICAgICJwYXJlbnRfdGhyZWFkIjogWwogICAgICB7CiAgICAgICAgImF1dGhvcl9yb2xlIjog
ImFjY291bnQiLAogICAgICAgICJwb3N0X2lkIjogIjIwOTE5NTk1NzU2NzA4OTkxOTQiLAogICAgICAgICJ0ZXh0
IjogIldlIGFyZSBub3QgaW4gcG9saXRpY3MgdG8gaWdub3JlIHBlb3BsZSdzIHdvcnJpZXMsIHdlIGFyZSBpbiBw
b2xpdGljcyB0byBkZWFsIHdpdGggdGhlbS4iCiAgICAgIH0KICAgIF0sCiAgICAicXVvdGVkX3Bvc3QiOiBudWxs
LAogICAgInRhcmdldF9pZCI6ICIyMDkyMjUwMzI2NjY5NjY4NDQyIiwKICAgICJ0aHJlYWRfaWQiOiAiMjA5MTk1
OTU3NTY3MDg5OTE5NCIKICB9LAogICJyZXBseV9lcG9jaCI6IDE3ODc2NjY0NzAsCiAgInJlcGx5X3RleHQiOiAi
UmVzb2x2ZSBnaXZlcyBwb2xpdGljYWwgaWRlYXMgZm9yY2UsIGJ1dCB0aGV5IG11c3Qgc3RpbGwgYmUganVkZ2Vk
IGJ5IHdoZXRoZXIgdGhleSBhZGRyZXNzIHBlb3BsZVx1MjAxOXMgcmVhbCBjb25jZXJucy4iLAogICJzY2hlbWFf
dmVyc2lvbiI6IDQsCiAgInRhcmdldF9pZCI6ICIyMDkyMjUwMzI2NjY5NjY4NDQyIgp9Cg==
"""
)
AUDITED_SOURCE_RECEIPT_SHA256 = (
    "0b424058cf024ed95a9127a6ec3094c4e75b8841f791ffd330a1af8eb626a80a"
)
AUDITED_TRANSACTION_ID = (
    "5834fd5ece90661181736d9e3bc835a2f5bfdc2ec953a7d133d5074d1de1aae8"
)
AUDITED_PAYLOAD_SHA256 = (
    "d8c819d999fcaaa7c7f1332274e8b3f724d8d7cce6b27a38ec7a14ab04312c87"
)
AUDITED_PENDING_MENTION = {
    "author_id": "1491806039213154305",
    "conversation_id": CONVERSATION_ID,
    "created_at": "2026-08-25T13:58:44.000Z",
    "edit_history_tweet_ids": [TARGET_ID],
    "entities": {
        "mentions": [
            {
                "end": 13,
                "id": "961002152582885377",
                "start": 0,
                "username": "MrsMThatcher",
            }
        ]
    },
    "id": TARGET_ID,
    "referenced_tweets": [
        {"id": CONVERSATION_ID, "type": "replied_to"}
    ],
    "text": (
        "@MrsMThatcher De aceea și nu a putut tradusă in ideile ei pentru "
        "că a fost de fier !👏"
    ),
}


def _authority():
    return issue_transaction_mutation_authority(
        lambda _operation: None,
        operation="external adoption focused test",
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _source_receipt() -> dict[str, object]:
    return {
        "schema_version": 4,
        "lifecycle_state": "sending",
        "target_id": TARGET_ID,
        "author_id": "1491806039213154305",
        "candidate_source": "mention",
        "conversation_id": CONVERSATION_ID,
        "reply_text": REPLY_TEXT,
        "reply_context": {
            "target_id": TARGET_ID,
            "target_author_id": "1491806039213154305",
            "thread_id": CONVERSATION_ID,
            "lane": "mention",
        },
        "ai_reply_draft": {
            "target_id": TARGET_ID,
            "thread_id": CONVERSATION_ID,
            "candidate_source": "mention",
            "proposed_reply": REPLY_TEXT,
        },
        "attempt_epoch": ATTEMPT_EPOCH,
        "reply_epoch": ATTEMPT_EPOCH,
        "daily_reply_date": "2026-08-25",
    }


def _identity(path: Path) -> tuple[int, ...]:
    value = os.lstat(path)
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_gid),
        int(value.st_size),
        int(value.st_ctime_ns),
        int(value.st_mtime_ns),
    )


def _namespace_snapshot(paths: list[Path]) -> dict[str, tuple[tuple[int, ...], bytes]]:
    result: dict[str, tuple[tuple[int, ...], bytes]] = {}
    for path in paths:
        if path.exists() or path.is_symlink():
            metadata = os.lstat(path)
            data = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
            result[str(path)] = (_identity(path), data)
    return result


@dataclass
class Incident:
    project: Path
    evidence: Path
    source_path: Path
    journal_path: Path
    fence_path: Path
    marker_path: Path
    marker_bytes: bytes
    source_bytes: bytes
    payload_bytes: bytes
    transaction_id: str
    journal_snapshot: journal.JournalSnapshot
    fence_snapshot: journal.JournalSnapshot

    def low_level_kwargs(self) -> dict[str, object]:
        source_stat = self.source_path.stat()
        return {
            "path": self.journal_path,
            "receipt_path": self.source_path,
            "mutation_authority": _authority(),
            "expected_transaction_id": self.transaction_id,
            "expected_lane": "conversational_reply",
            "expected_source_receipt_bytes": self.source_bytes,
            "expected_source_receipt_sha256": hashlib.sha256(
                self.source_bytes
            ).hexdigest(),
            "expected_source_receipt_device": int(source_stat.st_dev),
            "expected_source_receipt_inode": int(source_stat.st_ino),
            "expected_source_receipt_ctime_ns": int(source_stat.st_ctime_ns),
            "expected_source_receipt_size": int(source_stat.st_size),
            "expected_source_validator_id": journal.LANE_SOURCE_VALIDATOR_ID,
            "expected_payload_bytes": self.payload_bytes,
            "expected_payload_sha256": hashlib.sha256(
                self.payload_bytes
            ).hexdigest(),
            "expected_reply_target_id": TARGET_ID,
            "expected_journal_sha256": self.journal_snapshot.sha256,
            "expected_journal_device": self.journal_snapshot.device,
            "expected_journal_inode": self.journal_snapshot.inode,
            "expected_journal_ctime_ns": self.journal_snapshot.ctime_ns,
            "expected_journal_size": len(self.journal_snapshot.data),
            "expected_fence_sha256": self.fence_snapshot.sha256,
            "expected_fence_device": self.fence_snapshot.device,
            "expected_fence_inode": self.fence_snapshot.inode,
            "expected_fence_ctime_ns": self.fence_snapshot.ctime_ns,
            "expected_fence_size": len(self.fence_snapshot.data),
            "confirmed_post_id": POST_ID,
            "confirmation_epoch": CONFIRMATION_EPOCH,
            "prepared_audit_basename": (
                f"external_transport_confirmation.{self.transaction_id}."
                f"{POST_ID}.prepared.json"
            ),
            "prepared_audit_sha256": "a" * 64,
            "evidence_archive_basename": (
                f"external_transport_confirmation.{self.transaction_id}."
                f"{POST_ID}.evidence.json"
            ),
            "evidence_sha256": hashlib.sha256(
                self.evidence.read_bytes()
            ).hexdigest(),
        }

    def tool_kwargs(self, *, check_only: bool = True) -> dict[str, object]:
        source_stat = self.source_path.stat()
        marker_stat = self.marker_path.stat()
        return {
            "project_root": self.project,
            "expected_marker_sha256": hashlib.sha256(
                self.marker_bytes
            ).hexdigest(),
            "expected_marker_device": int(marker_stat.st_dev),
            "expected_marker_inode": int(marker_stat.st_ino),
            "expected_marker_ctime_ns": int(marker_stat.st_ctime_ns),
            "expected_marker_size": int(marker_stat.st_size),
            "expected_source_receipt_basename": self.source_path.name,
            "expected_source_receipt_sha256": hashlib.sha256(
                self.source_bytes
            ).hexdigest(),
            "expected_source_receipt_device": int(source_stat.st_dev),
            "expected_source_receipt_inode": int(source_stat.st_ino),
            "expected_source_receipt_ctime_ns": int(source_stat.st_ctime_ns),
            "expected_source_receipt_size": int(source_stat.st_size),
            "expected_source_lifecycle": "sending",
            "expected_candidate_lane": "mention",
            "expected_target_id": TARGET_ID,
            "expected_text_sha256": TEXT_SHA256,
            "expected_transaction_id": self.transaction_id,
            "expected_transport_lane": "conversational_reply",
            "expected_canonical_payload_sha256": hashlib.sha256(
                self.payload_bytes
            ).hexdigest(),
            "expected_journal_sha256": self.journal_snapshot.sha256,
            "expected_journal_device": self.journal_snapshot.device,
            "expected_journal_inode": self.journal_snapshot.inode,
            "expected_journal_ctime_ns": self.journal_snapshot.ctime_ns,
            "expected_journal_size": len(self.journal_snapshot.data),
            "expected_fence_sha256": self.fence_snapshot.sha256,
            "expected_fence_device": self.fence_snapshot.device,
            "expected_fence_inode": self.fence_snapshot.inode,
            "expected_fence_ctime_ns": self.fence_snapshot.ctime_ns,
            "expected_fence_size": len(self.fence_snapshot.data),
            "confirmed_post_id": POST_ID,
            "confirmation_epoch": CONFIRMATION_EPOCH,
            "external_evidence_path": self.evidence,
            "expected_external_evidence_sha256": hashlib.sha256(
                self.evidence.read_bytes()
            ).hexdigest(),
            "reconciliation_reference": "authenticated-x-read-reviewed-published",
            "confirm_external_publication_reviewed": True,
            "confirm_offline_reconciliation_complete": not check_only,
            "check_only": check_only,
            "now": lambda: CONFIRMATION_EPOCH,
        }


def _external_cli_arguments(
    incident: Incident,
    *,
    check_only: bool,
    include_offline_acknowledgement: bool = True,
) -> list[str]:
    values = incident.tool_kwargs(check_only=check_only)
    arguments = [
        "--project-root",
        os.fspath(values["project_root"]),
        "--expected-marker-sha256",
        str(values["expected_marker_sha256"]),
        "--adopt-externally-confirmed-reply",
    ]
    options = (
        ("expected_marker_device", "expected-marker-device"),
        ("expected_marker_inode", "expected-marker-inode"),
        ("expected_marker_ctime_ns", "expected-marker-ctime-ns"),
        ("expected_marker_size", "expected-marker-size"),
        ("expected_source_receipt_basename", "expected-source-receipt-basename"),
        ("expected_source_receipt_sha256", "expected-source-receipt-sha256"),
        ("expected_source_receipt_device", "expected-source-receipt-device"),
        ("expected_source_receipt_inode", "expected-source-receipt-inode"),
        ("expected_source_receipt_ctime_ns", "expected-source-receipt-ctime-ns"),
        ("expected_source_receipt_size", "expected-source-receipt-size"),
        ("expected_source_lifecycle", "expected-source-lifecycle"),
        ("expected_candidate_lane", "expected-candidate-lane"),
        ("expected_target_id", "expected-target-id"),
        ("expected_text_sha256", "expected-text-sha256"),
        ("expected_transaction_id", "expected-transport-transaction-id"),
        ("expected_transport_lane", "expected-transport-lane"),
        ("expected_canonical_payload_sha256", "expected-canonical-payload-sha256"),
        ("expected_journal_sha256", "expected-journal-sha256"),
        ("expected_journal_device", "expected-journal-device"),
        ("expected_journal_inode", "expected-journal-inode"),
        ("expected_journal_ctime_ns", "expected-journal-ctime-ns"),
        ("expected_journal_size", "expected-journal-size"),
        ("expected_fence_sha256", "expected-fence-sha256"),
        ("expected_fence_device", "expected-fence-device"),
        ("expected_fence_inode", "expected-fence-inode"),
        ("expected_fence_ctime_ns", "expected-fence-ctime-ns"),
        ("expected_fence_size", "expected-fence-size"),
        ("confirmed_post_id", "confirmed-post-id"),
        ("confirmation_epoch", "confirmation-epoch"),
        ("external_evidence_path", "external-evidence-path"),
        ("expected_external_evidence_sha256", "expected-external-evidence-sha256"),
        ("reconciliation_reference", "reconciliation-reference"),
    )
    for key, option in options:
        value = values[key]
        arguments.extend(
            (
                f"--{option}",
                os.fspath(value) if isinstance(value, os.PathLike) else str(value),
            )
        )
    arguments.append("--confirm-external-publication-reviewed")
    if check_only:
        arguments.append("--check-only")
    elif include_offline_acknowledgement:
        arguments.append("--confirm-offline-reconciliation-complete")
    return arguments


def build_incident(
    tmp_path: Path,
    *,
    source_bytes: bytes | None = None,
    activate_protocol: bool = False,
) -> Incident:
    journal.reset_consumed_authorities_for_tests()
    project = tmp_path / "bot"
    project.mkdir()
    (project / reconcile.LOCK_BASENAME).write_text(
        "pid=2147483647\n",
        encoding="utf-8",
    )
    if activate_protocol:
        create_test_protocol_activation(
            project / safety_protocol.ACTIVATION_BASENAME
        )
    source = _source_receipt() if source_bytes is None else json.loads(source_bytes)
    source_bytes = _canonical(source) if source_bytes is None else source_bytes
    source_path = project / reconcile.CONFIRMED_REPLY_RECEIPT_BASENAME
    _write_private(source_path, source_bytes)
    payload = {
        "text": str(source["reply_text"]),
        "reply": {"in_reply_to_tweet_id": str(source["target_id"])},
    }
    binding = journal.bind_transport_source(
        receipt_path=source_path,
        expected_receipt=source,
        expected_receipt_bytes=source_bytes,
        lane="conversational_reply",
        payload=payload,
        validator_id=journal.LANE_SOURCE_VALIDATOR_ID,
        validator=lambda *_args: True,
    )
    prepared = journal.begin_transport_transaction(
        receipt_path=source_path,
        source_binding=binding,
    )
    journal_path = journal.journal_path_for_receipt(source_path)
    journal.arm_transport_transaction(
        journal_path,
        prepared,
        mutation_authority=_authority(),
    )
    state = journal.inspect_transport_state(journal_path)
    assert state.classification == "attempting_pair"
    assert state.journal is not None and state.fence is not None
    marker_document = {
        "made_with_ai": False,
        "media_ids": [],
        "outcome": "ambiguous_remote_post",
        "recorded_at_epoch": CONFIRMATION_EPOCH,
        "reply_to_id": str(source["target_id"]),
        "schema_version": 1,
        "text_sha256": hashlib.sha256(
            str(source["reply_text"]).encode("utf-8")
        ).hexdigest(),
    }
    marker_bytes = _canonical(marker_document)
    marker_path = project / reconcile.MARKER_BASENAME
    _write_private(marker_path, marker_bytes)
    os.link(marker_path, project / reconcile.RESTART_BARRIER_BASENAME)
    evidence = tmp_path / "reviewed-x-evidence.json"
    _write_private(evidence, b'{"published":true,"read_only":true}\n')
    return Incident(
        project=project,
        evidence=evidence,
        source_path=source_path,
        journal_path=journal_path,
        fence_path=journal.fence_path_for_journal(journal_path),
        marker_path=marker_path,
        marker_bytes=marker_bytes,
        source_bytes=source_bytes,
        payload_bytes=journal.canonical_json_bytes(payload),
        transaction_id=str(state.journal.document["transaction_id"]),
        journal_snapshot=state.journal,
        fence_snapshot=state.fence,
    )


def _crash_external_adoption_during_journal_exchange(
    incident: Incident,
    *,
    after_exchange: bool,
) -> Path:
    """Lose a child process at one real ``RENAME_EXCHANGE`` boundary."""

    real_exchange = journal._rename_exchange
    child = os.fork()
    if child == 0:
        def interrupted_exchange(
            directory_fd: int,
            first: str,
            second: str,
        ) -> None:
            if after_exchange:
                real_exchange(directory_fd, first, second)
                os.fsync(directory_fd)
            os._exit(73)

        journal._rename_exchange = interrupted_exchange
        try:
            reconcile.adopt_externally_confirmed_reply_offline(
                **incident.tool_kwargs(check_only=False)
            )
        except BaseException:
            os._exit(74)
        os._exit(75)
    _pid, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    staging = sorted(
        path
        for path in incident.project.iterdir()
        if path.name.startswith(journal.JOURNAL_STAGING_PREFIX)
    )
    assert len(staging) == 1
    return staging[0]


def test_low_level_exact_adoption_and_repeat_are_confirmed_and_idempotent(
    tmp_path: Path,
) -> None:
    incident = build_incident(tmp_path)
    first = journal.adopt_externally_confirmed_transport_transaction(
        **incident.low_level_kwargs()
    )
    assert first.disposition == "first_adoption"
    assert first.previous_classification == "attempting_pair"
    assert first.confirmed.post_id == POST_ID
    assert first.confirmed.confirmation_epoch == CONFIRMATION_EPOCH
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "confirmed_pair"

    repeated = journal.adopt_externally_confirmed_transport_transaction(
        **incident.low_level_kwargs()
    )
    assert repeated.disposition == "already_adopted"
    assert repeated.journal_after.sha256 == first.journal_after.sha256


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("expected_transaction_id", "b" * 64),
        ("expected_reply_target_id", "2092250326669668443"),
        ("expected_payload_sha256", "b" * 64),
        ("expected_source_receipt_sha256", "b" * 64),
        ("expected_source_receipt_device", 1),
        ("expected_source_receipt_inode", 1),
        ("expected_source_receipt_ctime_ns", 1),
        ("expected_source_receipt_size", 1),
        ("expected_journal_sha256", "b" * 64),
        ("expected_journal_device", 1),
        ("expected_journal_inode", 1),
        ("expected_journal_ctime_ns", 1),
        ("expected_journal_size", 1),
        ("expected_fence_sha256", "b" * 64),
        ("expected_fence_device", 1),
        ("expected_fence_inode", 1),
        ("expected_fence_ctime_ns", 1),
        ("expected_fence_size", 1),
    ),
)
def test_low_level_wrong_reviewed_binding_refuses_without_mutation(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    incident = build_incident(tmp_path)
    before = _namespace_snapshot(
        [incident.source_path, incident.journal_path, incident.fence_path]
    )
    values = incident.low_level_kwargs()
    values[field] = replacement
    with pytest.raises(journal.TransportJournalError):
        journal.adopt_externally_confirmed_transport_transaction(**values)
    assert _namespace_snapshot(
        [incident.source_path, incident.journal_path, incident.fence_path]
    ) == before


def test_low_level_repeat_conflicts_refuse(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    values = incident.low_level_kwargs()
    journal.adopt_externally_confirmed_transport_transaction(**values)
    confirmed_before = _namespace_snapshot(
        [incident.source_path, incident.journal_path, incident.fence_path]
    )
    for field, replacement in (
        ("confirmed_post_id", "2092250943345557811"),
        ("confirmation_epoch", CONFIRMATION_EPOCH + 1),
        ("prepared_audit_sha256", "b" * 64),
        ("evidence_sha256", "b" * 64),
    ):
        changed = dict(values)
        changed["mutation_authority"] = _authority()
        changed[field] = replacement
        with pytest.raises(journal.TransportJournalError):
            journal.adopt_externally_confirmed_transport_transaction(**changed)
        assert _namespace_snapshot(
            [incident.source_path, incident.journal_path, incident.fence_path]
        ) == confirmed_before


def test_low_level_requires_valid_process_bound_mutation_authority(
    tmp_path: Path,
) -> None:
    incident = build_incident(tmp_path)
    values = incident.low_level_kwargs()
    values["mutation_authority"] = None
    with pytest.raises(TransactionMutationAuthorityError):
        journal.adopt_externally_confirmed_transport_transaction(**values)


def test_low_level_revalidates_authority_at_each_destructive_boundary(
    tmp_path: Path,
) -> None:
    incident = build_incident(tmp_path)
    operations: list[str] = []
    authority = issue_transaction_mutation_authority(
        operations.append,
        operation="external adoption boundary-count test",
    )
    values = incident.low_level_kwargs()
    values["mutation_authority"] = authority

    journal.adopt_externally_confirmed_transport_transaction(**values)

    assert operations[1:] == [
        "external transport confirmation adoption inspection",
        "external transport confirmation journal transition",
        "external transport confirmation atomic exchange",
        "external transport confirmation displaced cleanup",
        "external transport confirmation exact unlink displaced cleanup",
    ]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_low_level_rejects_fork_inherited_authority(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    values = incident.low_level_kwargs()
    inherited = values["mutation_authority"]
    child = os.fork()
    if child == 0:
        try:
            values["mutation_authority"] = inherited
            journal.adopt_externally_confirmed_transport_transaction(**values)
        except TransactionMutationAuthorityError:
            os._exit(0)
        except BaseException:
            os._exit(2)
        os._exit(1)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "attempting_pair"


@pytest.mark.parametrize(
    "unsafe",
    ("clear", "prepared", "staging", "guard", "incomplete", "invalid"),
)
def test_low_level_refuses_unsafe_transport_classifications(
    tmp_path: Path,
    unsafe: str,
) -> None:
    incident = build_incident(tmp_path)
    if unsafe == "clear":
        incident.journal_path.unlink()
        incident.fence_path.unlink()
    elif unsafe == "prepared":
        prepared = dict(incident.fence_snapshot.document)
        prepared["document_kind"] = (
            "mrsMThatcher_remote_write_transport_journal"
        )
        incident.journal_path.write_bytes(journal.canonical_json_bytes(prepared))
    elif unsafe == "staging":
        (incident.project / f"{journal.JOURNAL_STAGING_PREFIX}fixture").write_text(
            "unsafe",
            encoding="utf-8",
        )
    elif unsafe == "guard":
        (incident.project / f"{journal.JOURNAL_RETIREMENT_PREFIX}{'a' * 64}").write_text(
            "unsafe",
            encoding="utf-8",
        )
    elif unsafe == "incomplete":
        incident.fence_path.unlink()
    else:
        incident.fence_path.write_bytes(b"not-json\n")
    with pytest.raises(journal.TransportJournalError):
        journal.adopt_externally_confirmed_transport_transaction(
            **incident.low_level_kwargs()
        )


def test_low_level_refuses_unavailable_transport_directory(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    missing = tmp_path / "missing"
    values = incident.low_level_kwargs()
    values["path"] = missing / journal.JOURNAL_BASENAME
    values["receipt_path"] = missing / reconcile.CONFIRMED_REPLY_RECEIPT_BASENAME

    with pytest.raises(journal.TransportJournalError):
        journal.adopt_externally_confirmed_transport_transaction(**values)


def test_low_level_never_invokes_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    incident = build_incident(tmp_path)
    monkeypatch.setattr(
        journal.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail("network request was invoked"),
    )
    result = journal.adopt_externally_confirmed_transport_transaction(
        **incident.low_level_kwargs()
    )
    assert result.confirmed.post_id == POST_ID


def test_check_only_preserves_every_byte_and_identity(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    paths = [
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        incident.evidence,
        incident.project / reconcile.LOCK_BASENAME,
    ]
    before = _namespace_snapshot(paths)
    result = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=True)
    )
    assert result.execution == "check_only"
    assert result.adoption_state == "first_adoption"
    assert result.planned_transport_classification == "confirmed_pair"
    assert result.check_only_no_mutation is True
    assert _namespace_snapshot(paths) == before
    assert not (incident.project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_cli_check_only_prints_structured_plan_without_mutation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    incident = build_incident(tmp_path)
    observed_paths = [
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        incident.evidence,
    ]
    before = _namespace_snapshot(observed_paths)

    assert reconcile.main(
        _external_cli_arguments(incident, check_only=True)
    ) == 0
    captured = capfd.readouterr()
    result = json.loads(captured.out)

    assert captured.err == ""
    assert result["execution"] == "check_only"
    assert result["adoption_state"] == "first_adoption"
    assert result["planned_transport_classification"] == "confirmed_pair"
    assert result["new_transport"]["classification"] == "confirmed_pair"
    assert result["confirmed_post_id"] == POST_ID
    assert _namespace_snapshot(observed_paths) == before


def test_cli_apply_refuses_missing_second_acknowledgement_without_mutation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    incident = build_incident(tmp_path)
    observed_paths = [
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        incident.evidence,
    ]
    before = _namespace_snapshot(observed_paths)

    assert reconcile.main(
        _external_cli_arguments(
            incident,
            check_only=False,
            include_offline_acknowledgement=False,
        )
    ) == 2
    captured = capfd.readouterr()

    assert captured.out == ""
    assert "--confirm-offline-reconciliation-complete" in captured.err
    assert _namespace_snapshot(observed_paths) == before


@pytest.mark.parametrize(
    "external_options",
    (
        ("--check-only",),
        ("--confirm-external-publication-reviewed",),
        ("--external-evidence-path", "/tmp/reviewed-evidence.json"),
        ("--expected-journal-sha256", "a" * 64),
    ),
)
def test_media_mode_rejects_every_external_reply_option_before_mutation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    external_options: tuple[str, ...],
) -> None:
    from tests.test_remote_write_safety_marker_reconciliation import (
        media_cli_command,
        media_incident_installation,
    )

    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    arguments = media_cli_command(project, marker_bytes, receipt, fence)[2:]
    arguments.extend(
        (
            "--confirm-no-tweet-create-attempted",
            "--confirm-unattached-media-abandoned",
            *external_options,
        )
    )
    observed_paths = [
        project / reconcile.MARKER_BASENAME,
        project / reconcile.RESTART_BARRIER_BASENAME,
        project / reconcile.MEDIA_RECEIPT_BASENAME,
        project / reconcile.MEDIA_FENCE_BASENAME,
        project / reconcile.LOCK_BASENAME,
    ]
    before = _namespace_snapshot(observed_paths)

    assert reconcile.main(arguments) == 2
    captured = capfd.readouterr()

    assert captured.out == ""
    assert "external-reply options" in captured.err
    assert "--reconcile-unattached-media-upload" in captured.err
    assert _namespace_snapshot(observed_paths) == before
    assert not (project / reconcile.DEFAULT_ARCHIVE_BASENAME).exists()


def test_valid_media_mode_remains_supported_after_option_separation(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from tests.test_remote_write_safety_marker_reconciliation import (
        media_cli_command,
        media_incident_installation,
    )

    project, marker_bytes, receipt, fence = media_incident_installation(tmp_path)
    arguments = media_cli_command(project, marker_bytes, receipt, fence)[2:]
    arguments.extend(
        (
            "--confirm-no-tweet-create-attempted",
            "--confirm-unattached-media-abandoned",
        )
    )

    assert reconcile.main(arguments) == 0
    captured = capfd.readouterr()
    result = json.loads(captured.out)

    assert captured.err == ""
    assert result["operation"] == "offline_unattached_media_upload_archive"
    assert not (project / reconcile.MEDIA_RECEIPT_BASENAME).exists()
    assert not (project / reconcile.MEDIA_FENCE_BASENAME).exists()
    assert (project / reconcile.MARKER_BASENAME).read_bytes() == marker_bytes


def test_apply_orders_durable_audits_before_marker_retirement(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    observed: list[tuple[str, str, bool, bool]] = []

    def observe(stage: str) -> None:
        state = journal.inspect_transport_state(incident.journal_path)
        observed.append(
            (
                stage,
                state.classification,
                incident.marker_path.exists(),
                (
                    incident.project / reconcile.RESTART_BARRIER_BASENAME
                ).exists(),
            )
        )

    values = incident.tool_kwargs(check_only=False)
    values["_fault_injector"] = observe
    result = reconcile.adopt_externally_confirmed_reply_offline(**values)
    assert observed == [
        ("evidence_archived", "attempting_pair", True, True),
        ("prepared_audit_published", "attempting_pair", True, True),
        ("transport_confirmed", "confirmed_pair", True, True),
        ("completed_audit_published", "confirmed_pair", True, True),
        ("before_marker_archival", "confirmed_pair", True, True),
    ]
    assert result.final_transport_classification == "confirmed_pair"
    assert result.final_marker_presence == {
        reconcile.MARKER_BASENAME: False,
        reconcile.RESTART_BARRIER_BASENAME: False,
    }
    assert incident.source_path.exists()


@pytest.mark.parametrize(
    "crash_stage",
    (
        "evidence_archived",
        "prepared_audit_published",
        "transport_confirmed",
        "completed_audit_published",
    ),
)
def test_crash_boundaries_remain_blocking_and_resume(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    incident = build_incident(tmp_path)

    class InjectedCrash(RuntimeError):
        pass

    def crash(stage: str) -> None:
        if stage == crash_stage:
            raise InjectedCrash(stage)

    values = incident.tool_kwargs(check_only=False)
    values["_fault_injector"] = crash
    with pytest.raises(InjectedCrash, match=crash_stage):
        reconcile.adopt_externally_confirmed_reply_offline(**values)
    assert incident.marker_path.exists()
    assert (incident.project / reconcile.RESTART_BARRIER_BASENAME).exists()
    assert incident.source_path.exists()
    if crash_stage in {"transport_confirmed", "completed_audit_published"}:
        assert journal.inspect_transport_state(
            incident.journal_path
        ).classification == "confirmed_pair"
    else:
        assert journal.inspect_transport_state(
            incident.journal_path
        ).classification == "attempting_pair"

    resumed = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert resumed.adoption_state == "resumed"
    assert resumed.final_transport_classification == "confirmed_pair"
    assert not incident.marker_path.exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("after_exchange", (False, True))
def test_real_process_loss_inside_journal_exchange_is_exactly_resumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_exchange: bool,
) -> None:
    incident = build_incident(tmp_path)

    def forbid_remote(*_args, **_kwargs):
        pytest.fail("offline torn-transition recovery attempted a network call")

    monkeypatch.setattr(journal.requests, "request", forbid_remote)
    monkeypatch.setattr(journal.requests, "post", forbid_remote)
    staging = _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=after_exchange,
    )
    state = journal.inspect_transport_state(incident.journal_path)
    assert state.classification == "lifecycle_transition_in_progress"
    assert state.blocking is True
    assert state.staging_names == (staging.name,)
    assert state.retirement_guard_names == ()
    assert state.journal is not None and state.fence is not None

    staging_document = json.loads(staging.read_bytes())
    expected_phase = (
        "resumable_after_transport_exchange"
        if after_exchange
        else "resumable_before_transport_exchange"
    )
    if after_exchange:
        assert state.journal.document["lifecycle_state"] == "confirmed"
        assert state.journal.document["remote_post_id"] == POST_ID
        assert staging.read_bytes() == incident.journal_snapshot.data
        staging_stat = staging.stat()
        assert (
            int(staging_stat.st_dev),
            int(staging_stat.st_ino),
        ) == (
            incident.journal_snapshot.device,
            incident.journal_snapshot.inode,
        )
    else:
        assert state.journal.data == incident.journal_snapshot.data
        assert staging_document["lifecycle_state"] == "confirmed"
        assert staging_document["remote_post_id"] == POST_ID
    confirmed_generation_inode = (
        state.journal.inode if after_exchange else int(staging.stat().st_ino)
    )

    archive = incident.project / reconcile.DEFAULT_ARCHIVE_BASENAME
    observed_paths = [
        incident.evidence,
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        staging,
        *sorted(archive.iterdir()),
    ]
    before_check = _namespace_snapshot(observed_paths)
    checked = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=True)
    )
    assert checked.execution == "check_only"
    assert checked.adoption_state == expected_phase
    assert checked.final_transport_classification == (
        "lifecycle_transition_in_progress"
    )
    assert checked.planned_transport_classification == "confirmed_pair"
    assert checked.check_only_no_mutation is True
    assert _namespace_snapshot(observed_paths) == before_check

    resumed = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert resumed.adoption_state == expected_phase
    assert resumed.final_transport_classification == "confirmed_pair"
    assert resumed.confirmed_post_id == POST_ID
    assert not staging.exists()
    assert not incident.marker_path.exists()
    assert not (
        incident.project / reconcile.RESTART_BARRIER_BASENAME
    ).exists()
    assert incident.source_path.read_bytes() == incident.source_bytes
    assert json.loads(incident.source_path.read_bytes())["lifecycle_state"] == (
        "sending"
    )
    final_state = journal.inspect_transport_state(incident.journal_path)
    assert final_state.classification == "confirmed_pair"
    assert final_state.journal is not None
    assert final_state.journal.inode == confirmed_generation_inode
    assert final_state.journal.document["remote_post_id"] == POST_ID
    assert (incident.project / resumed.completed_audit_path).is_file()
    assert (incident.project / str(resumed.marker_archive_path)).is_file()
    assert (incident.project / str(resumed.marker_audit_path)).is_file()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("after_exchange", (False, True))
def test_low_level_torn_resume_revalidates_each_destructive_boundary(
    tmp_path: Path,
    after_exchange: bool,
) -> None:
    incident = build_incident(tmp_path)
    staging = _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=after_exchange,
    )
    live_document = json.loads(incident.journal_path.read_bytes())
    staged_document = json.loads(staging.read_bytes())
    confirmed_document = (
        live_document
        if live_document["lifecycle_state"] == "confirmed"
        else staged_document
    )
    external_binding = confirmed_document["external_confirmation"]
    operations: list[str] = []
    authority = issue_transaction_mutation_authority(
        operations.append,
        operation="torn external adoption boundary test",
    )
    values = incident.low_level_kwargs()
    values.update(
        {
            "mutation_authority": authority,
            "prepared_audit_basename": external_binding[
                "prepared_audit_basename"
            ],
            "prepared_audit_sha256": external_binding[
                "prepared_audit_sha256"
            ],
            "evidence_archive_basename": external_binding[
                "evidence_archive_basename"
            ],
            "evidence_sha256": external_binding["evidence_sha256"],
        }
    )

    result = journal.adopt_externally_confirmed_transport_transaction(**values)

    common = [
        "external transport confirmation adoption inspection",
        "external transport confirmation journal transition",
    ]
    if after_exchange:
        expected = [
            *common,
            "external transport confirmation existing displaced cleanup",
            "external transport confirmation exact unlink displaced cleanup",
        ]
    else:
        expected = [
            *common,
            "external transport confirmation outstanding exchange",
            "external transport confirmation resumed atomic exchange",
            "external transport confirmation post-exchange displaced cleanup",
            "external transport confirmation exact unlink displaced cleanup",
        ]
    assert operations[1:] == expected
    assert result.disposition == (
        "resumable_after_transport_exchange"
        if after_exchange
        else "resumable_before_transport_exchange"
    )
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "confirmed_pair"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize(
    "conflict",
    ("another_post", "extra_staging", "prepared_audit", "evidence_archive"),
)
def test_conflicting_torn_external_confirmation_refuses_without_cleanup(
    tmp_path: Path,
    conflict: str,
) -> None:
    incident = build_incident(tmp_path)
    staging = _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=False,
    )
    evidence_name, prepared_name, _completed_name = (
        reconcile._external_adoption_archive_names(
            incident.transaction_id,
            POST_ID,
        )
    )
    archive = incident.project / reconcile.DEFAULT_ARCHIVE_BASENAME
    if conflict == "another_post":
        replacement = json.loads(staging.read_bytes())
        replacement["remote_post_id"] = str(int(POST_ID) + 1)
        _write_private(staging, _canonical(replacement))
    elif conflict == "extra_staging":
        extra = incident.project / (
            journal.JOURNAL_STAGING_PREFIX + "f" * 32
        )
        _write_private(extra, staging.read_bytes())
    elif conflict == "prepared_audit":
        prepared = archive / prepared_name
        document = json.loads(prepared.read_bytes())
        document["reconciliation_reference"] = "conflicting-review"
        prepared.chmod(0o600)
        prepared.write_bytes(_canonical(document))
        prepared.chmod(0o400)
    else:
        evidence_archive = archive / evidence_name
        evidence_archive.chmod(0o600)
        evidence_archive.write_bytes(b'{"published":false}\n')
        evidence_archive.chmod(0o400)

    observed_paths = [
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        *sorted(
            path
            for path in incident.project.iterdir()
            if path.name.startswith(journal.JOURNAL_STAGING_PREFIX)
        ),
        *sorted(archive.iterdir()),
    ]
    before = _namespace_snapshot(observed_paths)
    with pytest.raises(reconcile.ExternalReplyAdoptionError):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=True)
        )
    assert _namespace_snapshot(observed_paths) == before
    assert incident.marker_path.exists()
    assert incident.source_path.read_bytes() == incident.source_bytes


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("unsafe_metadata", ("symlink", "fifo", "mode", "links"))
def test_unsafe_torn_staging_entry_refuses_without_cleanup(
    tmp_path: Path,
    unsafe_metadata: str,
) -> None:
    incident = build_incident(tmp_path)
    staging = _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=False,
    )
    staged_bytes = staging.read_bytes()
    if unsafe_metadata == "symlink":
        target = tmp_path / "staging-target.json"
        _write_private(target, staged_bytes)
        staging.unlink()
        staging.symlink_to(target)
    elif unsafe_metadata == "fifo":
        staging.unlink()
        os.mkfifo(staging, 0o600)
    elif unsafe_metadata == "mode":
        staging.chmod(0o640)
    else:
        os.link(staging, tmp_path / "second-staging-link.json")

    observed_paths = [
        incident.marker_path,
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
        incident.source_path,
        incident.journal_path,
        incident.fence_path,
        staging,
    ]
    before = _namespace_snapshot(observed_paths)
    with pytest.raises(reconcile.ExternalReplyAdoptionError):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=True)
        )
    assert _namespace_snapshot(observed_paths) == before
    assert incident.marker_path.exists()
    assert incident.source_path.read_bytes() == incident.source_bytes


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_torn_staging_identity_change_during_check_only_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = build_incident(tmp_path)
    staging = _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=False,
    )
    real_inspect = journal._inspect_exact_external_confirmation_transition
    inspections = 0

    def inspect_then_change(**kwargs):
        nonlocal inspections
        layout = real_inspect(**kwargs)
        inspections += 1
        if inspections == 1:
            metadata = staging.stat()
            os.utime(
                staging,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1),
            )
        return layout

    monkeypatch.setattr(
        journal,
        "_inspect_exact_external_confirmation_transition",
        inspect_then_change,
    )
    with pytest.raises(
        reconcile.ExternalReplyAdoptionError,
        match="layout changed",
    ):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=True)
        )
    assert inspections == 2
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "lifecycle_transition_in_progress"
    assert incident.marker_path.exists()
    assert incident.source_path.read_bytes() == incident.source_bytes


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_resumed_transport_can_repeat_exactly_and_refuses_conflicting_result(
    tmp_path: Path,
) -> None:
    incident = build_incident(tmp_path)
    _crash_external_adoption_during_journal_exchange(
        incident,
        after_exchange=True,
    )

    def stop_after_transport(stage: str) -> None:
        if stage == "transport_confirmed":
            raise RuntimeError("retain markers after resumed transport")

    values = incident.tool_kwargs(check_only=False)
    values["_fault_injector"] = stop_after_transport
    with pytest.raises(RuntimeError, match="retain markers"):
        reconcile.adopt_externally_confirmed_reply_offline(**values)
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "confirmed_pair"
    assert incident.marker_path.exists()

    check = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=True)
    )
    assert check.adoption_state == "resumable_after_confirmed_transition"
    for field, replacement in (
        ("confirmed_post_id", str(int(POST_ID) + 1)),
        ("confirmation_epoch", CONFIRMATION_EPOCH + 1),
        ("expected_external_evidence_sha256", "b" * 64),
    ):
        conflicting = incident.tool_kwargs(check_only=True)
        conflicting[field] = replacement
        with pytest.raises(reconcile.ExternalReplyAdoptionError):
            reconcile.adopt_externally_confirmed_reply_offline(**conflicting)

    repeated = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert repeated.adoption_state == "resumed"
    assert repeated.final_transport_classification == "confirmed_pair"


def test_conflicting_resume_and_archive_collision_refuse(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)

    def crash(stage: str) -> None:
        if stage == "prepared_audit_published":
            raise RuntimeError("prepared crash")

    values = incident.tool_kwargs(check_only=False)
    values["_fault_injector"] = crash
    with pytest.raises(RuntimeError, match="prepared crash"):
        reconcile.adopt_externally_confirmed_reply_offline(**values)

    conflicting = incident.tool_kwargs(check_only=False)
    conflicting["confirmed_post_id"] = "2092250943345557811"
    with pytest.raises(reconcile.ExternalReplyAdoptionError, match="conflicting"):
        reconcile.adopt_externally_confirmed_reply_offline(**conflicting)

    evidence_name, _prepared_name, _completed_name = (
        reconcile._external_adoption_archive_names(incident.transaction_id, POST_ID)
    )
    archive = incident.project / reconcile.DEFAULT_ARCHIVE_BASENAME
    evidence_archive = archive / evidence_name
    evidence_archive.chmod(0o600)
    evidence_archive.write_bytes(b"different\n")
    evidence_archive.chmod(0o400)
    with pytest.raises(reconcile.ExternalReplyAdoptionError):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=False)
        )


def test_exact_existing_archive_bytes_are_resumed_idempotently(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)

    def crash(stage: str) -> None:
        if stage == "completed_audit_published":
            raise RuntimeError("completed crash")

    values = incident.tool_kwargs(check_only=False)
    values["_fault_injector"] = crash
    with pytest.raises(RuntimeError, match="completed crash"):
        reconcile.adopt_externally_confirmed_reply_offline(**values)
    check = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=True)
    )
    assert check.adoption_state == "already_complete"
    result = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert result.adoption_state == "resumed"


@pytest.mark.parametrize(
    "entry",
    ("project", "marker", "source", "journal", "fence", "evidence"),
)
def test_symlinked_boundaries_refuse(
    tmp_path: Path,
    entry: str,
) -> None:
    incident = build_incident(tmp_path)
    values = incident.tool_kwargs(check_only=True)
    if entry == "project":
        alias = tmp_path / "bot-link"
        alias.symlink_to(incident.project, target_is_directory=True)
        values["project_root"] = alias
    elif entry == "evidence":
        target = tmp_path / "evidence-target"
        target.write_bytes(incident.evidence.read_bytes())
        target.chmod(0o600)
        incident.evidence.unlink()
        incident.evidence.symlink_to(target)
    else:
        path = {
            "marker": incident.marker_path,
            "source": incident.source_path,
            "journal": incident.journal_path,
            "fence": incident.fence_path,
        }[entry]
        target = tmp_path / f"{entry}-target"
        target.write_bytes(path.read_bytes())
        target.chmod(stat.S_IMODE(path.stat().st_mode))
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(reconcile.MarkerReconciliationError):
        reconcile.adopt_externally_confirmed_reply_offline(**values)


@pytest.mark.parametrize("entry", ("marker", "source", "journal", "fence", "evidence"))
def test_special_file_boundaries_refuse(tmp_path: Path, entry: str) -> None:
    incident = build_incident(tmp_path)
    values = incident.tool_kwargs(check_only=True)
    path = {
        "marker": incident.marker_path,
        "source": incident.source_path,
        "journal": incident.journal_path,
        "fence": incident.fence_path,
        "evidence": incident.evidence,
    }[entry]
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(reconcile.MarkerReconciliationError):
        reconcile.adopt_externally_confirmed_reply_offline(**values)


def test_running_daemon_lock_boundary_refuses(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    descriptor = os.open(incident.project, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(reconcile.BotStillRunningError):
            reconcile.adopt_externally_confirmed_reply_offline(
                **incident.tool_kwargs(check_only=True)
            )
    finally:
        os.close(descriptor)


def test_evidence_change_during_check_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = build_incident(tmp_path)
    real_revalidate = reconcile._revalidate_external_evidence
    changed = False

    def change_then_revalidate(evidence):
        nonlocal changed
        if not changed:
            changed = True
            incident.evidence.write_bytes(b'{"published":false}\n')
            incident.evidence.chmod(0o600)
        real_revalidate(evidence)

    monkeypatch.setattr(
        reconcile,
        "_revalidate_external_evidence",
        change_then_revalidate,
    )
    with pytest.raises(reconcile.ExternalReplyAdoptionError, match="changed"):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=True)
        )


def test_group_writable_external_evidence_refuses(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    incident.evidence.chmod(0o660)

    with pytest.raises(
        reconcile.UnsafeReconciliationPathError,
        match="group/world writable",
    ):
        reconcile.adopt_externally_confirmed_reply_offline(
            **incident.tool_kwargs(check_only=True)
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("expected_external_evidence_sha256", "b" * 64),
        ("confirmed_post_id", "not-a-post"),
        ("confirmation_epoch", 1),
        ("confirm_external_publication_reviewed", False),
        ("expected_source_lifecycle", "confirmed"),
    ),
)
def test_cli_preconditions_refuse(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    incident = build_incident(tmp_path)
    values = incident.tool_kwargs(check_only=True)
    values[field] = replacement
    with pytest.raises(reconcile.MarkerReconciliationError):
        reconcile.adopt_externally_confirmed_reply_offline(**values)


def test_apply_requires_both_acknowledgements(tmp_path: Path) -> None:
    incident = build_incident(tmp_path)
    values = incident.tool_kwargs(check_only=False)
    values["confirm_offline_reconciliation_complete"] = False
    with pytest.raises(reconcile.ExternalReplyAdoptionError, match="acknowledgement"):
        reconcile.adopt_externally_confirmed_reply_offline(**values)


def test_exact_audited_incident_fixture_reaches_confirmed_pair(
    tmp_path: Path,
) -> None:
    assert hashlib.sha256(AUDITED_SOURCE_RECEIPT_BYTES).hexdigest() == (
        AUDITED_SOURCE_RECEIPT_SHA256
    )
    incident = build_incident(
        tmp_path,
        source_bytes=AUDITED_SOURCE_RECEIPT_BYTES,
    )
    assert incident.transaction_id == AUDITED_TRANSACTION_ID
    assert hashlib.sha256(incident.payload_bytes).hexdigest() == (
        AUDITED_PAYLOAD_SHA256
    )

    result = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    confirmed = journal.inspect_confirmed_transport_transaction(
        incident.journal_path
    )

    assert result.transaction_id == AUDITED_TRANSACTION_ID
    assert result.target_id == TARGET_ID
    assert result.confirmed_post_id == POST_ID
    assert result.final_transport_classification == "confirmed_pair"
    assert confirmed.post_id == POST_ID
    assert confirmed.confirmation_epoch == CONFIRMATION_EPOCH


def test_offline_adoption_then_existing_startup_recovers_without_x_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the ordinary startup consumer completes the adopted transaction."""

    import mrsMThatcher2 as bot
    from mrs_log_digest import remote_write_safety_snapshot

    incident = build_incident(tmp_path, activate_protocol=True)
    offline = reconcile.adopt_externally_confirmed_reply_offline(
        **incident.tool_kwargs(check_only=False)
    )
    assert offline.final_transport_classification == "confirmed_pair"
    assert incident.source_path.exists()

    regular_receipt = incident.project / "regular_post_receipt.json"
    meme_receipt = incident.project / "meme_post_receipt.json"
    historical_receipt = incident.project / "historical_context_reply_receipt.json"
    state_path = incident.project / "bot_state.json"
    monkeypatch.setattr(bot, "BASE_DIR", incident.project)
    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular_receipt)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", meme_receipt)
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        incident.source_path,
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        historical_receipt,
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        incident.project / reconcile.MARKER_BASENAME,
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        incident.project / reconcile.RESTART_BARRIER_BASENAME,
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        incident.project / safety_protocol.ACTIVATION_BASENAME,
    )
    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", incident.project / "remote_media_upload_receipt.json")
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_args: True)
    monkeypatch.setattr(
        bot,
        "validate_pending_mention_candidate_authority",
        lambda *_args, **_kwargs: (True, False),
    )
    monkeypatch.setattr(
        bot,
        "transaction_mutation_authority",
        lambda _operation: _authority(),
    )

    def forbid_remote(*_args, **_kwargs):
        pytest.fail("startup recovery attempted an X request or duplicate post")

    monkeypatch.setattr(bot, "x_request", forbid_remote)
    monkeypatch.setattr(bot, "create_post", forbid_remote)
    monkeypatch.setattr(bot.requests, "request", forbid_remote)
    monkeypatch.setattr(bot.requests, "post", forbid_remote)

    state: dict[str, object] = {}
    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        state,
    )

    assert recovered == {
        "historical_context": False,
        "conversational_reply": True,
        "regular": False,
        "meme": False,
    }
    assert state["replied_to_ids"] == [TARGET_ID]
    assert state["own_auto_reply_ids"] == [POST_ID]
    assert state["last_reply_epoch"] == CONFIRMATION_EPOCH
    assert state_path.exists()
    assert not incident.source_path.exists()
    assert journal.inspect_transport_state(
        incident.journal_path
    ).classification == "clear"
    assert not incident.marker_path.exists()
    assert not (
        incident.project / reconcile.RESTART_BARRIER_BASENAME
    ).exists()

    for source in (
        regular_receipt,
        meme_receipt,
        incident.source_path,
        historical_receipt,
    ):
        ledger = receipt_retirement.inspect_retirement_ledger(source)
        assert (ledger.valid, ledger.blocking) == (True, False)

    safety = remote_write_safety_snapshot(incident.project)
    assert safety["transport"]["classification"] == "clear"
    assert safety["ready_for_remote_writes"] is True
    assert len(safety["retirement_ledgers"]) == 4
    assert all(
        row["valid"] is True and row["blocking"] is False
        for row in safety["retirement_ledgers"]
    )
