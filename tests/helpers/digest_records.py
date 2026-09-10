"""Synthetic digest log records with fixed timestamps and current digest lookups."""

from __future__ import annotations

from datetime import datetime, timedelta
import json

import mrs_log_digest as digest


BASE = datetime(2026, 7, 25, 9, 0, 0)


def record(
    offset: int,
    level: str,
    source: str,
    message: str,
    *,
    line: int = 1,
) -> digest.Record:
    return digest.Record(
        ts=BASE + timedelta(seconds=offset),
        level=level,
        src=source,
        line=line,
        msg=message,
        path="fixture.log",
        ordinal=offset + 1,
    )


def traceback(message: str, exception: str) -> str:
    return (
        f"{message}\n"
        "Traceback (most recent call last):\n"
        '  File "/srv/mrsMThatcher2.py", line 100, in worker\n'
        f"{exception}"
    )


def loaded_gate(offset: int) -> digest.Record:
    return record(
        offset,
        "INFO",
        "log_event",
        'EVENT {"event":"historical_context_semantic_gate","status":"loaded",'
        '"policy_version":"gate-v1","ledger_sha256":"ledger",'
        '"projection_sha256":"projection","blocked_quote_count":21}',
    )


def event(kind, **values):
    return {"kind": kind, "time": "2026-07-15 12:00:00", **values}


def structured_record(offset, payload, *, level="INFO"):
    return digest.Record(
        datetime(2026, 9, 4, 12) + timedelta(seconds=offset),
        level,
        "log_event",
        offset + 1,
        "EVENT " + json.dumps(payload, sort_keys=True),
        "mrsMThatcher.log",
        offset + 1,
    )


def _normal_main_post_record() -> digest.Record:
    return record(
        0,
        "INFO",
        "log_event",
        "EVENT "
        + json.dumps(
            {
                "event": "main_post_posted",
                "lane": "quote_image",
                "post_id": "123",
                "quote_hash": "a" * 64,
                "image_hash": "b" * 64,
                "image_basename": "t01.jpg",
            }
        ),
    )
