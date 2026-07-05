#!/usr/bin/env python3
"""
Summarise MrsMThatcher bot logs into a compact, ChatGPT-friendly digest.

Examples:
  # First run in the bot log directory, with an explicit starting point:
  cd /disks/disk1/etc/mrsMThatcher
  ./mrs_log_digest.py --since "2026-06-25 08:00" > digest.md

  # Later runs automatically resume after the last log timestamp previously analysed:
  ./mrs_log_digest.py > digest.md

  # JSON output:
  ./mrs_log_digest.py --json > digest.json

By default this expects to be run in the directory containing mrsMThatcher*.log*
files. It stores its resume timestamp in .mrs_log_digest_state.json.

No third-party dependencies.

Enhanced v7: keeps the v6 meme-scheduler reporting and fixes config
back-scan across multiple/rotated log files. Earlier v5/v6 scanned log files
in path order, so an older rotated file could overwrite newer Config values.
v7 sorts all candidate Config records chronologically before applying them.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<src>[^:]+):(?P<line>\d+) - (?P<msg>.*)$"
)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value += " 00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise SystemExit(f"Could not parse datetime: {value!r}. Use e.g. '2026-06-25 08:00'.")


def dt_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def read_resume_data(state_file: Path) -> Dict[str, Any]:
    """Read the digest resume file.

    The timestamp is used for auto-resume. Newer versions also keep the last
    observed bot state/config so short quiet windows can still show budget and
    priority context.
    """
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARNING: could not read state file {state_file}: {e}", file=sys.stderr)
        return {}


def save_resume_time(state_file: Path, last_ts: datetime, records: List["Record"], report: Dict[str, Any], logs: List[Path]) -> None:
    old = read_resume_data(state_file)

    latest_state = merge_context(
        report.get("latest_state") or {},
        old.get("last_known_latest_state") or {},
    )
    latest_config = merge_context(
        report.get("latest_config") or {},
        old.get("last_known_latest_config") or {},
    )

    # Persist clean context only; _carried_forward/_filled_from_previous are
    # rendering annotations for this run, not durable bot facts.
    latest_state_clean = strip_internal_context_markers(latest_state)
    latest_config_clean = strip_internal_context_markers(latest_config)
    boundary_fingerprints = [
        record_fingerprint(record)
        for record in records
        if record.ts == last_ts
    ]

    data = {
        "last_log_entry_time": dt_text(last_ts),
        "last_log_entry_fingerprints": boundary_fingerprints,
        "last_run_record_count": report.get("summary", {}).get("record_count"),
        "last_run_time_start": report.get("summary", {}).get("time_start"),
        "last_run_time_end": report.get("summary", {}).get("time_end"),
        "last_run_logs": [str(p) for p in logs],
        "last_known_latest_state": latest_state_clean,
        "last_known_latest_config": latest_config_clean,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(state_file)


def discover_logs(directory: Path, pattern: str) -> List[Path]:
    paths = []
    for p in directory.glob(pattern):
        if not p.is_file():
            continue
        # Avoid accidentally ingesting digest outputs or state files if a broad pattern is used.
        name = p.name.lower()
        if name.endswith(".json") or name.endswith(".md") or "digest" in name:
            continue
        paths.append(p)
    # Deterministic order; the records are later sorted by timestamp anyway.
    return sorted(paths, key=lambda p: p.name)


def epoch_to_human(value: Any) -> Optional[str]:
    try:
        n = int(value)
    except Exception:
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S")


def int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except Exception:
        return None


def cooldown_state_text(until_epoch: Any, state_time_text: Any) -> str:
    until = int_or_none(until_epoch)
    state_time = parse_dt(state_time_text) if state_time_text else None
    if not until or not state_time:
        return ""
    return "active" if int(state_time.timestamp()) < until else "expired"


@dataclass(frozen=True)
class Record:
    ts: datetime
    level: str
    src: str
    line: int
    msg: str
    path: str
    ordinal: int


def record_fingerprint(record: Record) -> str:
    body = "\x1f".join(
        [
            dt_text(record.ts),
            record.level,
            record.src,
            str(record.line),
            record.msg,
        ]
    )
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def iter_records(path: Path) -> Iterable[Record]:
    current: Optional[Dict[str, Any]] = None
    ordinal = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = LOG_RE.match(line)
            if m:
                if current is not None:
                    yield Record(**current)
                ordinal += 1
                current = {
                    "ts": datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"),
                    "level": m.group("level"),
                    "src": m.group("src").strip(),
                    "line": int(m.group("line")),
                    "msg": m.group("msg"),
                    "path": str(path),
                    "ordinal": ordinal,
                }
            elif current is not None:
                current["msg"] += "\n" + line
            else:
                # Ignore leading junk before first timestamp.
                pass

    if current is not None:
        yield Record(**current)


def read_records(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Record]:
    seen = set()
    out: List[Record] = []
    for path in paths:
        if not path.exists():
            print(f"WARNING: missing log file: {path}", file=sys.stderr)
            continue
        for r in iter_records(path):
            if since:
                if since_exclusive:
                    if r.ts <= since:
                        continue
                elif r.ts < since:
                    continue
            if until and r.ts > until:
                continue
            # Logs are often uploaded with overlap; dedupe exact records.
            key = (r.ts, r.level, r.src, r.line, r.msg)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    out.sort(key=lambda r: (r.ts, r.path, r.ordinal))
    return out


def summarize_input_files(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []

    for path in paths:
        summary: Dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "size": None,
            "mtime": None,
            "total_records": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "records_after_since": 0,
            "records_in_window": 0,
        }

        if not path.exists():
            summaries.append(summary)
            continue

        try:
            stat = path.stat()
            summary["size"] = stat.st_size
            summary["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass

        for record in iter_records(path):
            summary["total_records"] += 1
            ts_text = dt_text(record.ts)
            if summary["first_timestamp"] is None:
                summary["first_timestamp"] = ts_text
            summary["last_timestamp"] = ts_text

            after_since = True
            if since is not None:
                after_since = record.ts > since if since_exclusive else record.ts >= since
            if after_since:
                summary["records_after_since"] += 1

            selected = after_since
            if until is not None and record.ts > until:
                selected = False
            if selected:
                summary["records_in_window"] += 1

        summaries.append(summary)

    return summaries


def lit(value: str) -> str:
    """Parse a Python repr string when possible, otherwise return raw."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("'\"")


def short(value: Any, n: int) -> str:
    if value is None:
        return ""
    s = str(value).replace("\n", "\\n")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


INTERNAL_CONTEXT_KEYS = {
    "_carried_forward",
    "_filled_from_previous",
    "_filled_from_log_backscan",
    "_carried_from_log_backscan",
    "_log_backscan_timestamp",
    "_partial",
}


def strip_internal_context_markers(value: Any) -> Any:
    """Remove digest-only annotations before persisting context."""
    if isinstance(value, dict):
        return {
            k: strip_internal_context_markers(v)
            for k, v in value.items()
            if k not in INTERNAL_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [strip_internal_context_markers(v) for v in value]
    return value


def merge_context(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing/None fields in current from previous, preserving current values.

    v3 only carried state/config forward when the whole object was absent. v4
    merges per field, so a quiet or partial window can still show reply budgets
    from the last known config while using the current state snapshot.
    """
    cur = dict(current or {})
    prev = strip_internal_context_markers(previous or {})
    if not prev:
        return cur

    if not cur:
        cur = dict(prev)
        cur["_carried_forward"] = True
        return cur

    filled = False
    for k, v in prev.items():
        if k in INTERNAL_CONTEXT_KEYS:
            continue
        if k not in cur or cur.get(k) is None:
            cur[k] = v
            filled = True
    if filled:
        cur["_filled_from_previous"] = True
    return cur




def extract_config_pairs(msg: str) -> Dict[str, str]:
    """Return KEY=VALUE pairs from a bot Config log message."""
    if not msg.startswith("Config: "):
        return {}
    body = msg[len("Config: "):]
    return {key: val.strip() for key, val in re.findall(r"([A-Z0-9_]+)=([^\s]+)", body)}


def merge_context_from_log_backscan(
    current: Dict[str, Any],
    previous: Dict[str, Any],
    *,
    backscan_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fill missing config fields from earlier records in the same log files.

    This is deliberately separate from saved-state carry-forward: it means an
    incremental digest can recover the latest startup Config values even when
    .mrs_log_digest_state.json has no stored config yet.
    """
    cur = dict(current or {})
    prev = strip_internal_context_markers(previous or {})
    if not prev:
        return cur

    ts_text = dt_text(backscan_ts) if backscan_ts else None
    if not cur:
        cur = dict(prev)
        cur["_carried_from_log_backscan"] = True
        if ts_text:
            cur["_log_backscan_timestamp"] = ts_text
        return cur

    filled = False
    for k, v in prev.items():
        if k in INTERNAL_CONTEXT_KEYS:
            continue
        if k not in cur or cur.get(k) is None:
            cur[k] = v
            filled = True
    if filled:
        cur["_filled_from_log_backscan"] = True
        if ts_text:
            cur["_log_backscan_timestamp"] = ts_text
    return cur


def find_latest_config_before(paths: List[Path], before: Optional[datetime]) -> Tuple[Dict[str, str], Optional[datetime]]:
    """Scan earlier log records for the latest known Config values before a cutoff.

    Config is emitted as multiple `Config: KEY=VALUE` records at startup. v7
    collects matching records from *all* log files, de-duplicates them, then
    sorts chronologically before applying values. This matters with rotated logs:
    path/glob order is not guaranteed to be chronological, and an older rotated
    file must never overwrite newer config from the live log.
    """
    if before is None:
        return {}, None

    seen = set()
    candidates: List[Record] = []
    for path in paths:
        if not path.exists():
            continue
        for r in iter_records(path):
            if r.ts >= before:
                continue
            if not extract_config_pairs(r.msg):
                continue
            key = (r.ts, r.level, r.src, r.line, r.msg)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(r)

    candidates.sort(key=lambda r: (r.ts, r.path, r.ordinal))

    configs: Dict[str, str] = {}
    latest_ts: Optional[datetime] = None
    for r in candidates:
        configs.update(extract_config_pairs(r.msg))
        latest_ts = r.ts

    return configs, latest_ts

def try_parse_response_id_text(msg: str) -> Tuple[Optional[str], Optional[str]]:
    marker = "response="
    if marker not in msg:
        return None, None
    raw = msg.split(marker, 1)[1].strip()
    try:
        data = ast.literal_eval(raw)
        d = data.get("data") or {}
        return str(d.get("id")) if d.get("id") is not None else None, d.get("text")
    except Exception:
        m = re.search(r"'id': '([^']+)'", raw)
        return (m.group(1) if m else None), None


def try_parse_json_object_from_msg(msg: str) -> Optional[Dict[str, Any]]:
    start = msg.find("{")
    if start < 0:
        return None
    raw = msg[start:]
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def parse_partial_state_from_msg(msg: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction from log_json_debug state dumps that may be truncated."""
    if "State being saved:" not in msg and "Loaded state:" not in msg:
        return None
    keys = [
        "api_cooldown_reason", "api_cooldown_until_epoch",
        "quote_api_cooldown_reason", "quote_api_cooldown_until_epoch",
        "daily_quote_reply_count", "daily_quote_reply_date",
        "daily_reply_count", "daily_reply_date",
        "last_main_post_id", "last_meme_post_epoch", "last_quote_post_epoch",
        "last_quote_tweet_check_epoch", "last_reply_epoch", "last_seen_mention_id",
        "next_meme_post_epoch", "next_meme_schedule_mode", "next_meme_schedule_date",
        "meme_anchor_quote_post_epoch", "meme_schedule_version", "next_quote_post_epoch",
        "next_reply_lane_priority", "skipped_hot_reply_ids",
    ]
    out: Dict[str, Any] = {"_partial": True}
    for key in keys:
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*("(?:\\.|[^"])*"|-?\d+|true|false|null)', msg)
        if not m:
            continue
        raw = m.group(1)
        try:
            out[key] = json.loads(raw)
        except Exception:
            out[key] = raw.strip('"')

    # Count arrays only when their full array appears before truncation.
    for key in ("quote_spam_author_ids", "posted_meme_filenames", "recent_own_post_ids"):
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*(\[[\s\S]*?\])\s*,?\n\s*"', msg)
        if m:
            try:
                val = json.loads(m.group(1))
                out[key] = val
            except Exception:
                pass
    return out if len(out) > 1 else None


def analyse(records: List[Record], max_text: int = 280) -> Dict[str, Any]:
    stats = Counter()
    events: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    self_test_errors: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []
    handled_api_restrictions: List[Dict[str, Any]] = []
    cooldown_active: List[Dict[str, Any]] = []
    lifecycle: List[Dict[str, Any]] = []
    routine_skip_counts = Counter()
    configs: Dict[str, str] = {}
    latest_state: Optional[Dict[str, Any]] = None
    latest_state_ts: Optional[datetime] = None

    pending_quote: Dict[str, Any] = {}
    pending_meme: Dict[str, Any] = {}
    pending_mention: Dict[str, Any] = {}
    pending_qt: Dict[str, Any] = {}
    last_created_post: Dict[str, Any] = {}

    def add_event(kind: str, ts: datetime, **kwargs: Any) -> None:
        ev = {"time": ts.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        for k, v in kwargs.items():
            if isinstance(v, str):
                ev[k] = short(v, max_text)
            else:
                ev[k] = v
        events.append(ev)
        stats[kind] += 1

    for r in records:
        msg = r.msg

        # Lifecycle/config/state
        if msg == "Bot starting" or msg == "Bot started successfully" or "Bot stopped by KeyboardInterrupt" in msg:
            lifecycle.append({"time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "level": r.level, "message": msg.splitlines()[0]})

        config_pairs = extract_config_pairs(msg)
        if config_pairs:
            configs.update(config_pairs)

        if msg.startswith("State being saved:") or msg.startswith("Loaded state:"):
            state = try_parse_json_object_from_msg(msg) or parse_partial_state_from_msg(msg)
            if state is not None:
                latest_state = state
                latest_state_ts = r.ts

        is_self_test_error = (
            msg.startswith("SELFTEST FAIL:")
            or msg.startswith("Self-test finished with ")
            or ("Missing X credentials." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
            or ("ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
        )
        is_handled_reply_restriction = (
            "reply not allowed" in msg.lower()
            or "marking quote tweet as skipped without consuming reply quota" in msg.lower()
            or "not allowed to reply" in msg.lower()
            or "author has restricted who can reply" in msg.lower()
        )

        # Error/warning collection. Exclude routine KeyboardInterrupt, expected
        # self-test failures, and handled target restrictions from operational errors.
        if is_self_test_error:
            self_test_errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
            })
        elif is_handled_reply_restriction and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            # The raw X API 403 is classified below. Follow-up warnings such as
            # "marking skipped without consuming quota" are expected handling.
            pass
        elif r.level in {"ERROR", "CRITICAL"} or (r.level == "WARNING" and "Bot stopped by KeyboardInterrupt" not in msg):
            errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
            })

        if ("API cooldown active" in msg or "due to API cooldown" in msg or "Skipping quote-tweet check due to API cooldown" in msg or "Skipping mention check due to API cooldown" in msg):
            stats["cooldown_mentions"] += 1
        m = re.search(r"API cooldown active until ([^:]+:\d{2}:\d{2}): (.+)$", msg)
        if m:
            cooldown_active.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "until": m.group(1).strip(),
                "reason": m.group(2).strip(),
            })
        m = re.search(r"Entering API cooldown after (429|repeated errors) until (.+)$", msg)
        if m:
            add_event("api_cooldown_entered", r.ts, reason=m.group(1), until=m.group(2).strip())
            continue
        m = re.search(r"Migrated legacy pickle file (.+) to JSON file (.+)$", msg)
        if m:
            add_event("used_history_migrated", r.ts, legacy_file=m.group(1).strip(), json_file=m.group(2).strip())
            continue
        m = re.search(r"Normalized used-history JSON ordering in (.+)$", msg)
        if m:
            add_event("used_history_normalized", r.ts, json_file=m.group(1).strip())
            continue
        x_error_match = None
        if r.src in {"x_request", "x_bearer_request"}:
            x_error_match = re.search(r"^X(?: bearer)? API error (\d+):", msg)
        if x_error_match:
            stats["x_api_errors"] += 1
            service = "X bearer" if "X bearer API error" in msg else "X OAuth"
            endpoint = "quote_tweets" if service == "X bearer" else "mentions/hot-post"
            status_code = x_error_match.group(1)
            if status_code == "403" and is_handled_reply_restriction:
                endpoint = "post/reply"
            api_error = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "service": service,
                "endpoint": endpoint,
                "status": status_code,
                "message": short(msg, 240),
            }
            if status_code == "403" and is_handled_reply_restriction:
                handled_api_restrictions.append(api_error)
            else:
                api_errors.append(api_error)
            if status_code == "503":
                stats[f"x_api_503_{endpoint.replace('/', '_').replace('-', '_')}"] += 1
            elif status_code == "429":
                stats["x_api_429_rate_limit"] += 1
        if r.src in {"ask_grok_for_reply", "xai_request"} and msg.startswith("xAI error"):
            stats["xai_errors"] += 1
            m = re.search(r"xAI error (\d+):", msg)
            api_errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "service": "xAI",
                "endpoint": "chat",
                "status": m.group(1) if m else "",
                "message": short(msg, 240),
            })
        if api_errors:
            if msg.startswith("Rate Limit:"):
                api_errors[-1]["rate_limit"] = msg.split(":", 1)[1].strip()
            elif msg.startswith("Remaining:"):
                api_errors[-1]["remaining"] = msg.split(":", 1)[1].strip()
            else:
                m = re.search(r"Recorded (?:quote/)?x API error\. status_code=(\d+) errors_in_window=(\d+/\d+)", msg)
                if m:
                    api_errors[-1]["errors_in_window"] = m.group(2)
        if "Traceback" in msg:
            stats["tracebacks"] += 1

        # General quiet counters.
        if "No mentions returned" in msg:
            stats["no_mentions_checks"] += 1
        if "Starting mention reply check" in msg:
            stats["mention_checks"] += 1
        if msg.startswith("Fetching mentions."):
            stats["mention_fetch_attempts"] += 1
        if "Starting quote-tweet reply check" in msg:
            stats["quote_tweet_checks"] += 1
        if msg == "Due to check mentions":
            stats["normal_lane_due_checks"] += 1
        if msg == "Due to check quote tweets":
            stats["quote_lane_due_checks"] += 1

        # Hot-post reply watch / alternating-lane diagnostics.
        if "Hot-post reply check loaded" in msg:
            stats["hot_post_reply_watch_loads"] += 1
        if "/2/tweets/search/recent" in msg:
            stats["hot_post_recent_search_calls"] += 1
        m = re.search(r"Fetched (\d+) hot-post conversation candidate\(s\) for post_id=(\d+)", msg)
        if m:
            stats["hot_post_recent_search_successes"] += 1
            add_event(
                "hot_post_search_result",
                r.ts,
                original_post_id=m.group(2),
                candidates=int(m.group(1)),
            )
            continue
        m = re.search(r"Hot-post reply check returning (\d+) candidate\(s\)", msg)
        if m:
            stats["hot_post_reply_candidate_batches"] += 1
            stats["hot_post_reply_candidates_returned"] += int(m.group(1))
        if "Quote-tweet check is due, but normal/hot-post reply lane has priority" in msg:
            stats["priority_forced_normal_before_quote"] += 1
        if "Normal/hot-post reply lane posted; next reply-lane priority=quote" in msg:
            stats["priority_flipped_to_quote"] += 1
        if "Quote-tweet reply lane posted; next reply-lane priority=normal" in msg:
            stats["priority_flipped_to_normal"] += 1
        if "Normal/hot-post reply lane did not post; quote-tweet lane may use this slot" in msg:
            stats["priority_normal_first_refusal_no_post"] += 1
        m = re.search(r"Quote-tweet check status=([a-z_]+)", msg)
        if m:
            status = m.group(1)
            stats[f"quote_tweet_status_{status}"] += 1
            if status != "posted":
                stats["quote_tweet_checks_no_post"] += 1

        # Quote/image posts.
        m = re.search(r"Selected line_no=(\d+) text=(.*)$", msg, re.S)
        if m:
            pending_quote["line_no"] = int(m.group(1))
            pending_quote["text"] = lit(m.group(2))
            continue

        m = re.search(r"Posting quote/image\. line_no=(\d+) image_no=(\d+) image=(.*)$", msg)
        if m:
            pending_quote.update({
                "start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "line_no": int(m.group(1)),
                "image_no": int(m.group(2)),
                "image": m.group(3).strip(),
            })
            continue

        if msg.startswith("Quote text="):
            pending_quote["text"] = lit(msg.split("=", 1)[1])
            continue

        m = re.search(r"Quote/image posted successfully\. posted_id=(\d+)", msg)
        if m:
            add_event(
                "quote_image_posted",
                r.ts,
                post_id=m.group(1),
                line_no=pending_quote.get("line_no"),
                image_no=pending_quote.get("image_no"),
                text=pending_quote.get("text", ""),
                image=pending_quote.get("image", ""),
            )
            pending_quote = {}
            continue

        # Daily meme posts.
        if msg.startswith("Posting meme image:"):
            pending_meme = {"start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "image": msg.split(":", 1)[1].strip()}
            continue
        if msg.startswith("Meme image summary for cache:"):
            pending_meme["summary"] = lit(msg.split(":", 1)[1])
            continue
        m = re.search(r"Daily meme posted successfully\. posted_id=(\d+) file=(.+)$", msg)
        if m:
            add_event(
                "daily_meme_posted",
                r.ts,
                post_id=m.group(1),
                file=m.group(2).strip(),
                summary=pending_meme.get("summary", ""),
                image=pending_meme.get("image", ""),
            )
            pending_meme = {}
            continue

        # Created X post: remember it so reply/post events can attach if needed.
        if "Created X post successfully" in msg:
            post_id, post_text = try_parse_response_id_text(msg)
            last_created_post = {"time": r.ts, "post_id": post_id, "post_text": post_text}
            stats["created_x_posts"] += 1
            continue

        # Normal mention lane, including synthetic hot-post reply candidates.
        m = re.search(r"Considering (mention|hot_post_reply) id=(\d+) author_id=([^\s]+) text=(.*)$", msg, re.S)
        if m:
            source = m.group(1)
            id_key = "mention_id" if source == "mention" else "hot_post_reply_id"
            pending_mention = {
                "source": source,
                id_key: m.group(2),
                "mention_id": m.group(2),  # kept for backward-compatible post/reply matching
                "author_id": m.group(3),
                "incoming_text": lit(m.group(4)),
                "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            }
            continue

        m = re.search(r"Generated reply to mention (\d+): (.*)$", msg, re.S)
        if m:
            if pending_mention.get("mention_id") != m.group(1):
                pending_mention = {"mention_id": m.group(1), "source": "unknown"}
            pending_mention["reply"] = lit(m.group(2))
            pending_mention["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            continue

        m = re.search(r"Recorded and cached own auto-reply id=(\d+)", msg)
        if m and pending_mention:
            pending_mention["reply_post_id"] = m.group(1)
            continue

        if msg == "Reply posted successfully" and pending_mention:
            if not pending_mention.get("reply_post_id") and last_created_post.get("post_id"):
                pending_mention["reply_post_id"] = last_created_post.get("post_id")
            source = pending_mention.get("source", "mention")
            if source == "hot_post_reply":
                data = dict(pending_mention)
                data.pop("mention_id", None)
                data.pop("source", None)
                add_event("hot_post_reply_posted", r.ts, **data)
            else:
                data = dict(pending_mention)
                data.pop("source", None)
                add_event("mention_reply_posted", r.ts, **data)
            pending_mention = {}
            continue

        m = re.search(r"No usable reply generated for (mention|hot_post_reply) (\d+)", msg)
        if m:
            source = m.group(1)
            if source == "hot_post_reply":
                add_event(
                    "hot_post_reply_grok_skip",
                    r.ts,
                    hot_post_reply_id=m.group(2),
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                )
            else:
                add_event(
                    "mention_grok_skip",
                    r.ts,
                    mention_id=m.group(2),
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                )
            pending_mention = {}
            continue

        m = re.search(r"Skipping (mention|hot_post_reply) (\d+): (.*)$", msg)
        if m:
            source, ident, reason = m.group(1), m.group(2), m.group(3).strip()
            if source == "hot_post_reply":
                if "already replied/skipped" in reason:
                    routine_skip_counts["hot_post_reply_already_handled"] += 1
                else:
                    add_event(
                        "hot_post_reply_skipped",
                        r.ts,
                        hot_post_reply_id=ident,
                        author_id=pending_mention.get("author_id"),
                        incoming_text=pending_mention.get("incoming_text", ""),
                        reason=reason,
                    )
            else:
                add_event(
                    "mention_skipped",
                    r.ts,
                    mention_id=ident,
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                    reason=reason,
                )
            pending_mention = {}
            continue

        m = re.search(r"Skipping hot-post candidate (\d+): (.*)$", msg, re.S)
        if m:
            add_event("hot_post_reply_skipped", r.ts, hot_post_reply_id=m.group(1), reason=m.group(2).strip())
            continue

        # Quote tweet lane.
        m = re.search(r"Considering quote tweet id=(\d+) author_id=([^\s]+) original_post_id=(\d+) text=(.*)$", msg, re.S)
        if m:
            pending_qt = {
                "quote_tweet_id": m.group(1),
                "author_id": m.group(2),
                "original_post_id": m.group(3),
                "incoming_text": lit(m.group(4)),
                "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            }
            continue

        m = re.search(r"Generated reply to quote tweet (\d+): (.*)$", msg, re.S)
        if m:
            if pending_qt.get("quote_tweet_id") != m.group(1):
                pending_qt = {"quote_tweet_id": m.group(1)}
            pending_qt["reply"] = lit(m.group(2))
            pending_qt["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            continue

        m = re.search(r"Recorded and cached own quote-tweet auto-reply id=(\d+)", msg)
        if m and pending_qt:
            pending_qt["reply_post_id"] = m.group(1)
            continue

        if msg == "Quote-tweet reply posted successfully" and pending_qt:
            if not pending_qt.get("reply_post_id") and last_created_post.get("post_id"):
                pending_qt["reply_post_id"] = last_created_post.get("post_id")
            add_event("quote_tweet_reply_posted", r.ts, **pending_qt)
            pending_qt = {}
            continue

        m = re.search(r"No usable reply generated for quote tweet (\d+)", msg)
        if m:
            add_event(
                "quote_tweet_grok_skip",
                r.ts,
                quote_tweet_id=m.group(1),
                author_id=pending_qt.get("author_id"),
                original_post_id=pending_qt.get("original_post_id"),
                incoming_text=pending_qt.get("incoming_text", ""),
            )
            pending_qt = {}
            continue

        m = re.search(r"Skipping quote tweet (\d+): (.*)$", msg, re.S)
        if m:
            reason = m.group(2).strip()
            if reason == "already seen/replied/skipped":
                routine_skip_counts["quote_tweet_already_seen"] += 1
            elif "not a direct quote" in reason:
                routine_skip_counts["quote_tweet_not_direct"] += 1
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            elif "authored by own account" in reason:
                routine_skip_counts["quote_tweet_self_authored"] += 1
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            else:
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            continue

        # Other interesting skip/rate/cap messages.
        if msg in {
            "Daily generated/replied cap reached",
            "Skipping mention check: minimum interval between replies not reached",
            "Skipping quote-tweet check: total daily reply cap reached",
            "Skipping quote-tweet check: daily quote-reply cap reached",
        }:
            routine_skip_counts[msg] += 1
            if msg == "Skipping mention check: minimum interval between replies not reached":
                stats["mention_checks_skipped_spacing"] += 1

    latest_state_summary: Dict[str, Any] = {}
    if latest_state is not None:
        latest_state_summary = {
            "time": latest_state_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_state_ts else None,
            "daily_reply_date": latest_state.get("daily_reply_date"),
            "daily_reply_count": latest_state.get("daily_reply_count"),
            "daily_quote_reply_date": latest_state.get("daily_quote_reply_date"),
            "daily_quote_reply_count": latest_state.get("daily_quote_reply_count"),
            "last_seen_mention_id": latest_state.get("last_seen_mention_id"),
            "last_main_post_id": latest_state.get("last_main_post_id"),
            "last_reply_epoch": latest_state.get("last_reply_epoch"),
            "last_reply_human": epoch_to_human(latest_state.get("last_reply_epoch")),
            "last_quote_post_epoch": latest_state.get("last_quote_post_epoch"),
            "last_quote_post_human": epoch_to_human(latest_state.get("last_quote_post_epoch")),
            "next_quote_post_epoch": latest_state.get("next_quote_post_epoch"),
            "next_quote_post_human": epoch_to_human(latest_state.get("next_quote_post_epoch")),
            "next_meme_post_epoch": latest_state.get("next_meme_post_epoch"),
            "next_meme_post_human": epoch_to_human(latest_state.get("next_meme_post_epoch")),
            "next_meme_schedule_mode": latest_state.get("next_meme_schedule_mode"),
            "next_meme_schedule_date": latest_state.get("next_meme_schedule_date"),
            "meme_anchor_quote_post_epoch": latest_state.get("meme_anchor_quote_post_epoch"),
            "meme_anchor_quote_post_human": epoch_to_human(latest_state.get("meme_anchor_quote_post_epoch")),
            "meme_schedule_version": latest_state.get("meme_schedule_version"),
            "api_cooldown_until_epoch": latest_state.get("api_cooldown_until_epoch"),
            "api_cooldown_until_human": epoch_to_human(latest_state.get("api_cooldown_until_epoch")),
            "api_cooldown_reason": latest_state.get("api_cooldown_reason"),
            "xai_api_cooldown_until_epoch": latest_state.get("xai_api_cooldown_until_epoch"),
            "xai_api_cooldown_until_human": epoch_to_human(latest_state.get("xai_api_cooldown_until_epoch")),
            "xai_api_cooldown_reason": latest_state.get("xai_api_cooldown_reason"),
            "quote_api_cooldown_until_epoch": latest_state.get("quote_api_cooldown_until_epoch"),
            "quote_api_cooldown_until_human": epoch_to_human(latest_state.get("quote_api_cooldown_until_epoch")),
            "quote_api_cooldown_reason": latest_state.get("quote_api_cooldown_reason"),
            "quote_spam_author_count": len(latest_state.get("quote_spam_author_ids") or []),
            "posted_meme_count": len(latest_state.get("posted_meme_filenames") or []),
            "posted_meme_filenames_tail": list((latest_state.get("posted_meme_filenames") or [])[-8:]),
            "recent_own_post_ids_head": list((latest_state.get("recent_own_post_ids") or [])[:5]),
            "next_reply_lane_priority": latest_state.get("next_reply_lane_priority"),
            "skipped_hot_reply_count": len(latest_state.get("skipped_hot_reply_ids") or []),
        }

    self_test_times = {str(item.get("time")) for item in self_test_errors}
    api_error_times = {str(item.get("time")) for item in api_errors}
    remaining_errors: List[Dict[str, Any]] = []
    for item in errors:
        message = str(item.get("message", ""))
        timestamp = str(item.get("time", ""))
        if timestamp in self_test_times and (
            "Missing X credentials." in message
            or "ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in message
        ):
            self_test_errors.append(item)
            continue
        if timestamp in api_error_times and (
            message.startswith("Failed to get mention")
            or message.startswith("Failed to get quote")
            or message.startswith("Failed to fetch quote")
        ):
            continue
        remaining_errors.append(item)
    errors = remaining_errors

    # Build a short automatic headline.
    serious_errors = [e for e in errors if e["level"] in {"ERROR", "CRITICAL"}]
    warnings = [e for e in errors if e["level"] == "WARNING"]
    headline = []
    headline.append(f"{stats.get('quote_image_posted', 0)} quote/image post(s)")
    headline.append(f"{stats.get('daily_meme_posted', 0)} daily meme(s)")
    headline.append(f"{stats.get('mention_reply_posted', 0)} mention reply/replies")
    headline.append(f"{stats.get('hot_post_reply_posted', 0)} hot-post reply/replies")
    headline.append(f"{stats.get('quote_tweet_reply_posted', 0)} quote-tweet reply/replies")
    headline.append(f"{stats.get('mention_grok_skip', 0) + stats.get('hot_post_reply_grok_skip', 0) + stats.get('quote_tweet_grok_skip', 0)} Grok skip(s)")
    if serious_errors:
        headline.append(f"{len(serious_errors)} operational error(s)")
    else:
        headline.append("no serious errors")
    if handled_api_restrictions:
        headline.append(f"{len(handled_api_restrictions)} handled API restriction(s)")
    if self_test_errors:
        selftest_fail_checks = sum(1 for e in self_test_errors if str(e.get("message", "")).startswith("SELFTEST FAIL:"))
        headline.append(f"self-test failures: {selftest_fail_checks} check(s)")
    cooldown_until_epoch = int_or_none(latest_state_summary.get("api_cooldown_until_epoch"))
    xai_cooldown_until_epoch = int_or_none(latest_state_summary.get("xai_api_cooldown_until_epoch"))
    quote_cooldown_until_epoch = int_or_none(latest_state_summary.get("quote_api_cooldown_until_epoch"))
    latest_state_time = parse_dt(latest_state_summary.get("time"))
    window_start_epoch = int(records[0].ts.timestamp()) if records else None

    def cooldown_headline(until_epoch: int | None, *, label: str) -> str | None:
        if not until_epoch or not latest_state_time:
            return None
        latest_state_epoch = int(latest_state_time.timestamp())
        if latest_state_epoch < until_epoch:
            return f"{label} cooldown active now"
        if window_start_epoch is not None and until_epoch >= window_start_epoch:
            return f"{label} cooldown occurred, now expired"
        return None

    cooldown_labels = [
        label
        for label in (
            cooldown_headline(cooldown_until_epoch, label="API"),
            cooldown_headline(xai_cooldown_until_epoch, label="xAI"),
            cooldown_headline(quote_cooldown_until_epoch, label="quote API"),
        )
        if label
    ]
    if cooldown_labels:
        headline.extend(cooldown_labels)
    elif stats.get("api_cooldown_entered", 0):
        headline.append("API cooldown occurred")
    else:
        headline.append("no API cooldown")

    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(latest_state_summary.get("daily_reply_count"))
    used_quote = int_or_none(latest_state_summary.get("daily_quote_reply_count"))
    derived = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
        },
        "reply_lane_priority": {
            "current_next_priority": latest_state_summary.get("next_reply_lane_priority"),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
        },
    }

    not_rate_limited = any(
        str(item.get("remaining", "")).isdigit()
        and int(str(item.get("remaining"))) > 0
        and str(item.get("status")) != "429"
        for item in api_errors
    )
    post_cooldown_errors: List[Dict[str, Any]] = []
    cooldown_events = [ev for ev in events if ev.get("kind") == "api_cooldown_entered"]
    for item in api_errors:
        try:
            item_ts = datetime.strptime(item["time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        for ev in cooldown_events:
            until = parse_dt(str(ev.get("until", "")))
            if until and item_ts > until:
                post_cooldown_errors.append(item)
                break

    return {
        "summary": {
            "record_count": len(records),
            "time_start": records[0].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "time_end": records[-1].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "headline": "; ".join(headline),
            "stats": dict(stats),
            "routine_skip_counts": dict(routine_skip_counts),
        },
        "latest_config": configs,
        "latest_state": latest_state_summary,
        "derived": derived,
        "api_health": {
            "errors": api_errors,
            "handled_restrictions": handled_api_restrictions,
            "cooldown_active": cooldown_active,
            "post_cooldown_errors": post_cooldown_errors,
            "not_rate_limited": not_rate_limited,
        },
        "lifecycle": lifecycle[-12:],
        "events": events,
        "self_test_errors": self_test_errors[-40:],
        "errors_and_warnings": errors[-40:],
    }


def md_table_row(cols: List[Any]) -> str:
    def esc(x: Any) -> str:
        s = short(x, 240).replace("|", "\\|")
        return s
    return "| " + " | ".join(esc(c) for c in cols) + " |"


def refresh_derived(report: Dict[str, Any]) -> None:
    """Recalculate derived sections after any carried-forward context is applied."""
    configs = report.get("latest_config") or {}
    st = report.get("latest_state") or {}
    stats = report.get("summary", {}).get("stats", {}) or {}

    # Cooldown human timestamps are derived from the epoch. Recompute after
    # saved-context merging so a cleared epoch=0 cannot keep an old date/reason.
    for prefix in ("api_cooldown", "xai_api_cooldown", "quote_api_cooldown"):
        epoch_key = f"{prefix}_until_epoch"
        human_key = f"{prefix}_until_human"
        reason_key = f"{prefix}_reason"
        if epoch_key not in st:
            continue

        until = int_or_none(st.get(epoch_key))
        if until and until > 0:
            st[human_key] = epoch_to_human(until)
        else:
            st[human_key] = None
            st[reason_key] = ""

    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(st.get("daily_reply_count"))
    used_quote = int_or_none(st.get("daily_quote_reply_count"))

    report["derived"] = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
            "has_any_budget_input": any(x is not None for x in (used_auto, max_auto, used_quote, max_quote)),
            "state_carried_forward": bool(st.get("_carried_forward")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
        },
        "reply_lane_priority": {
            "current_next_priority": st.get("next_reply_lane_priority"),
            "has_priority_state": st.get("next_reply_lane_priority") is not None,
            "state_carried_forward": bool(st.get("_carried_forward")),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
            "normal_lane_due_checks": stats.get("normal_lane_due_checks", 0),
            "mention_function_entries": stats.get("mention_checks", 0),
            "mention_fetch_attempts": stats.get("mention_fetch_attempts", 0),
            "mention_checks_skipped_spacing": stats.get("mention_checks_skipped_spacing", 0),
            "mention_checks_skipped_cooldown": stats.get("cooldown_mentions", 0),
            "quote_lane_due_checks": stats.get("quote_lane_due_checks", 0),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
            "quote_tweet_status_posted": stats.get("quote_tweet_status_posted", 0),
            "quote_tweet_status_checked": stats.get("quote_tweet_status_checked", 0),
            "quote_tweet_status_skipped_spacing": stats.get("quote_tweet_status_skipped_spacing", 0),
            "quote_tweet_status_skipped_cap": stats.get("quote_tweet_status_skipped_cap", 0),
            "quote_tweet_status_skipped_cooldown": stats.get("quote_tweet_status_skipped_cooldown", 0),
            "quote_tweet_checks_no_post": stats.get("quote_tweet_checks_no_post", 0),
        },
    }


def apply_saved_context(report: Dict[str, Any], state_file: Path) -> None:
    """Fill missing latest_state/latest_config from the previous digest run.

    v4 merges field-by-field. That means a current state snapshot can be
    combined with a carried-forward config snapshot, so the reply budget section
    can still show e.g. "5 / 12" even in windows with no startup Config line.
    """
    old = read_resume_data(state_file)

    report["latest_state"] = merge_context(
        report.get("latest_state") or {},
        old.get("last_known_latest_state") or {},
    )
    report["latest_config"] = merge_context(
        report.get("latest_config") or {},
        old.get("last_known_latest_config") or {},
    )

    refresh_derived(report)



def _cfg_bool(cfg: Dict[str, Any], key: str) -> Optional[bool]:
    if key not in cfg:
        return None
    val = str(cfg.get(key)).strip().lower()
    if val in {"true", "1", "yes", "on"}:
        return True
    if val in {"false", "0", "no", "off"}:
        return False
    return None


def _seconds_to_minutes_text(value: Any) -> str:
    n = int_or_none(value)
    if n is None:
        return "?"
    if n % 60 == 0:
        return f"{n // 60} min"
    return f"{n} sec"


def _source_bits_for_state_config(st: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    bits: List[str] = []
    if st.get("_carried_forward"):
        bits.append("state carried forward")
    if st.get("_filled_from_previous"):
        bits.append("state partly filled")
    if cfg.get("_carried_forward"):
        bits.append("config carried forward")
    if cfg.get("_carried_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
    if cfg.get("_filled_from_previous"):
        bits.append("config partly filled from previous digest state")
    if cfg.get("_filled_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
    return bits

def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    out: List[str] = []
    out.append("# MrsMThatcher log digest")
    out.append("")
    out.append(f"Window: `{s.get('time_start')}` → `{s.get('time_end')}`")
    if report.get("requested_since"):
        mode = "exclusive" if report.get("since_exclusive") else "inclusive"
        source = report.get("since_source") or "manual"
        out.append(f"Requested since: `{report.get('requested_since')}` ({mode}, source={source})")
    if report.get("resume_state_file"):
        out.append(f"Resume state file: `{report.get('resume_state_file')}`")
    out.append(f"Records parsed: `{s.get('record_count')}`")
    input_warning = report.get("input_warning")
    if input_warning:
        out.append(f"Input warning: **{input_warning}**")
    out.append("")

    input_files = report.get("input_files") or []
    if input_files:
        out.append("## Input files")
        out.append("```text")
        for item in input_files:
            out.append(str(item.get("path")))
            if not item.get("exists"):
                out.append("  missing")
                continue
            out.append(f"  size={item.get('size')}  mtime={item.get('mtime')}")
            out.append(
                f"  first_timestamp={item.get('first_timestamp')}  "
                f"last_timestamp={item.get('last_timestamp')}"
            )
            out.append(
                f"  total_records={item.get('total_records')}  "
                f"records_after_since={item.get('records_after_since')}  "
                f"records_in_window_before_dedupe={item.get('records_in_window')}"
            )
        out.append("```")
        out.append("")

    out.append("## Headline")
    out.append(s.get("headline") or "")
    out.append("")

    st = report.get("latest_state") or {}
    if st:
        out.append("## Latest state")
        if st.get("_carried_forward"):
            out.append(f"State timestamp: `{st.get('time')}` (carried forward from previous digest state)")
        elif st.get("_filled_from_previous"):
            out.append(f"State timestamp: `{st.get('time')}` (current snapshot with missing fields filled from previous digest state)")
        else:
            out.append(f"State timestamp: `{st.get('time')}`")
        out.append("")
        out.append("```text")
        out.append(f"daily_reply_count       = {st.get('daily_reply_count')}  date={st.get('daily_reply_date')}")
        out.append(f"daily_quote_reply_count = {st.get('daily_quote_reply_count')}  date={st.get('daily_quote_reply_date')}")
        if st.get("next_reply_lane_priority") is not None:
            out.append(f"next_reply_lane_priority = {st.get('next_reply_lane_priority')}")
        if st.get("skipped_hot_reply_count") is not None:
            out.append(f"skipped_hot_reply_count = {st.get('skipped_hot_reply_count')}")
        out.append(f"quote_spam_author_count = {st.get('quote_spam_author_count')}")
        api_cooldown_status = cooldown_state_text(st.get("api_cooldown_until_epoch"), st.get("time"))
        api_cooldown_suffix = f"  {api_cooldown_status}" if api_cooldown_status else ""
        api_cooldown_human = st.get("api_cooldown_until_human") or "none"
        out.append(
            f"api_cooldown_until      = {st.get('api_cooldown_until_epoch')}  "
            f"{api_cooldown_human}{api_cooldown_suffix}"
        )
        if st.get("api_cooldown_reason"):
            out.append(f"api_cooldown_reason     = {st.get('api_cooldown_reason')}")
        xai_api_cooldown_status = cooldown_state_text(st.get("xai_api_cooldown_until_epoch"), st.get("time"))
        xai_api_cooldown_suffix = f"  {xai_api_cooldown_status}" if xai_api_cooldown_status else ""
        xai_api_cooldown_human = st.get("xai_api_cooldown_until_human") or "none"
        out.append(
            f"xai_api_cooldown_until  = {st.get('xai_api_cooldown_until_epoch')}  "
            f"{xai_api_cooldown_human}{xai_api_cooldown_suffix}"
        )
        if st.get("xai_api_cooldown_reason"):
            out.append(f"xai_api_cooldown_reason = {st.get('xai_api_cooldown_reason')}")
        quote_api_cooldown_status = cooldown_state_text(st.get("quote_api_cooldown_until_epoch"), st.get("time"))
        quote_api_cooldown_suffix = f"  {quote_api_cooldown_status}" if quote_api_cooldown_status else ""
        quote_api_cooldown_human = st.get("quote_api_cooldown_until_human") or "none"
        out.append(
            f"quote_api_cooldown_until = {st.get('quote_api_cooldown_until_epoch')}  "
            f"{quote_api_cooldown_human}{quote_api_cooldown_suffix}"
        )
        if st.get("quote_api_cooldown_reason"):
            out.append(f"quote_api_cooldown_reason = {st.get('quote_api_cooldown_reason')}")
        out.append(f"last_main_post_id       = {st.get('last_main_post_id')}")
        out.append(f"last_seen_mention_id    = {st.get('last_seen_mention_id')}")
        if st.get("last_quote_post_epoch") is not None:
            out.append(f"last_quote_post         = {st.get('last_quote_post_human')}  epoch={st.get('last_quote_post_epoch')}")
        out.append(f"next_quote_post         = {st.get('next_quote_post_human')}  epoch={st.get('next_quote_post_epoch')}")
        out.append(f"next_meme_post          = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
        if st.get("next_meme_schedule_mode") is not None:
            out.append(f"next_meme_mode          = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
        if st.get("meme_anchor_quote_post_epoch"):
            out.append(f"meme_anchor_quote_post  = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
        if st.get("meme_schedule_version") is not None:
            out.append(f"meme_schedule_version   = {st.get('meme_schedule_version')}")
        out.append(f"posted_meme_count       = {st.get('posted_meme_count')}")
        out.append("```")
        if st.get("posted_meme_filenames_tail"):
            out.append("Recent posted meme filenames:")
            out.append("```text")
            for name in st["posted_meme_filenames_tail"]:
                out.append(str(name))
            out.append("```")
        out.append("")

    cfg = report.get("latest_config") or {}
    meme_keys = {
        "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
        "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
        "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
    }
    if cfg or st:
        has_new_meme_config = any(k in cfg for k in meme_keys)
        has_meme_state = any(st.get(k) is not None for k in (
            "next_meme_post_epoch", "next_meme_schedule_mode",
            "meme_anchor_quote_post_epoch", "meme_schedule_version",
        ))
        if has_new_meme_config or has_meme_state:
            out.append("## Daily meme schedule")
            out.append("```text")
            source_bits = _source_bits_for_state_config(st, cfg)
            if source_bits:
                out.append(f"source                  = {', '.join(source_bits)}")
            enabled = _cfg_bool(cfg, "ENABLE_DAILY_MEME_POSTS")
            if enabled is not None:
                out.append(f"enabled                 = {enabled}")
            trigger = cfg.get("MEME_TRIGGER_AFTER_HOUR")
            min_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS")
            max_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS")
            fallback_hour = cfg.get("MEME_FALLBACK_HOUR")
            fallback_minute = cfg.get("MEME_FALLBACK_MINUTE")
            if trigger is not None:
                out.append(f"rule                    = first normal quote/image post after {trigger}:00 schedules the daily meme")
            if min_delay is not None or max_delay is not None:
                out.append(f"random_delay_after_rule = {_seconds_to_minutes_text(min_delay)} to {_seconds_to_minutes_text(max_delay)}")
            if fallback_hour is not None or fallback_minute is not None:
                hh = str(fallback_hour) if fallback_hour is not None else "?"
                mm_int = int_or_none(fallback_minute)
                mm = f"{mm_int:02d}" if mm_int is not None else "?"
                out.append(f"fallback_if_no_anchor   = {hh}:{mm}")
            if cfg.get("MEME_MIN_SECONDS_AFTER_QUOTE_POST") is not None:
                out.append(f"min_gap_after_quote     = {_seconds_to_minutes_text(cfg.get('MEME_MIN_SECONDS_AFTER_QUOTE_POST'))}")
            if cfg.get("MEME_SCHEDULE_VERSION") is not None:
                out.append(f"config_schedule_version = {cfg.get('MEME_SCHEDULE_VERSION')}")
            if st.get("next_meme_post_epoch") is not None:
                out.append(f"current_next_meme       = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
            if st.get("next_meme_schedule_mode") is not None:
                out.append(f"current_mode            = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
            if st.get("meme_anchor_quote_post_epoch"):
                out.append(f"current_anchor          = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
            else:
                if st.get("next_meme_schedule_mode") and str(st.get("next_meme_schedule_mode")).startswith("fallback"):
                    out.append("current_anchor          = none yet; fallback remains until first qualifying post/image after midday")
            out.append("```")
            out.append("")

    derived = report.get("derived") or {}
    budget = derived.get("reply_budget") or {}
    if budget:
        out.append("## Reply budget")
        out.append("```text")
        au, al, ar = budget.get("auto_used"), budget.get("auto_limit"), budget.get("auto_remaining")
        qu, ql, qr = budget.get("quote_used"), budget.get("quote_limit"), budget.get("quote_remaining")
        source_bits = []
        if budget.get("state_carried_forward"):
            source_bits.append("state carried forward")
        if budget.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if budget.get("config_carried_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if budget.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if budget.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if budget.get("config_filled_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if source_bits:
            out.append(f"source             = {', '.join(source_bits)}")
        if not budget.get("has_any_budget_input"):
            out.append("not available      = no state/config snapshot in this window or saved resume context")
        else:
            if au is not None or al is not None:
                out.append(f"auto replies used  = {au if au is not None else '?'} / {al if al is not None else '?'}  remaining={ar if ar is not None else '?'}")
            if qu is not None or ql is not None:
                out.append(f"quote replies used = {qu if qu is not None else '?'} / {ql if ql is not None else '?'}  remaining={qr if qr is not None else '?'}")
        out.append("```")
        out.append("")

    lane = derived.get("reply_lane_priority") or {}
    if lane:
        out.append("## Reply lane priority")
        out.append("```text")
        source_bits = []
        if lane.get("state_carried_forward"):
            source_bits.append("state carried forward")
        if lane.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if lane.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if lane.get("config_carried_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if lane.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if lane.get("config_filled_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if source_bits:
            out.append(f"source                         = {', '.join(source_bits)}")
        priority = lane.get("current_next_priority")
        out.append(f"current_next_priority          = {priority if priority is not None else 'not available'}")
        out.append(f"normal_lane_due_checks         = {lane.get('normal_lane_due_checks')}")
        out.append(f"mention_function_entries       = {lane.get('mention_function_entries')}")
        out.append(f"mention_fetch_attempts         = {lane.get('mention_fetch_attempts')}")
        out.append(f"mention_checks_skipped_spacing = {lane.get('mention_checks_skipped_spacing')}")
        out.append(f"mention_checks_skipped_cooldown = {lane.get('mention_checks_skipped_cooldown')}")
        out.append(f"quote_lane_due_checks          = {lane.get('quote_lane_due_checks')}")
        out.append(f"priority_flipped_to_quote      = {lane.get('flipped_to_quote')}")
        out.append(f"priority_flipped_to_normal     = {lane.get('flipped_to_normal')}")
        out.append(f"forced_normal_before_quote     = {lane.get('forced_normal_before_quote')}")
        out.append(f"normal_first_refusal_no_post   = {lane.get('normal_first_refusal_no_post')}")
        out.append(f"quote_tweet_status_posted      = {lane.get('quote_tweet_status_posted')}")
        out.append(f"quote_tweet_status_checked     = {lane.get('quote_tweet_status_checked')}")
        out.append(f"quote_tweet_status_spacing     = {lane.get('quote_tweet_status_skipped_spacing')}")
        out.append(f"quote_tweet_status_cap         = {lane.get('quote_tweet_status_skipped_cap')}")
        out.append(f"quote_tweet_status_cooldown    = {lane.get('quote_tweet_status_skipped_cooldown')}")
        out.append(f"quote_tweet_checks_no_post     = {lane.get('quote_tweet_checks_no_post')}")
        out.append("```")
        out.append("")

    stats = report["summary"].get("stats", {})
    routine = report["summary"].get("routine_skip_counts", {})
    out.append("## Counts")
    out.append("```json")
    out.append(json.dumps({"stats": stats, "routine_skip_counts": routine}, indent=2, ensure_ascii=False))
    out.append("```")
    out.append("")

    events = report.get("events") or []
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        by_kind.setdefault(ev["kind"], []).append(ev)

    def section(kind: str, title: str, cols: List[str]) -> None:
        rows = by_kind.get(kind) or []
        if not rows:
            return
        out.append(f"## {title}")
        out.append(md_table_row(cols))
        out.append(md_table_row(["---"] * len(cols)))
        for ev in rows:
            out.append(md_table_row([ev.get(c, "") for c in cols]))
        out.append("")

    section("quote_image_posted", "Quote/image posts", ["time", "post_id", "line_no", "image_no", "text"])
    section("daily_meme_posted", "Daily meme posts", ["time", "post_id", "file", "summary"])
    section("mention_reply_posted", "Mention replies", ["time", "mention_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("hot_post_reply_posted", "Hot-post replies", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("quote_tweet_reply_posted", "Quote-tweet replies", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text", "reply", "reply_post_id"])
    section("hot_post_search_result", "Hot-post recent-search results", ["time", "original_post_id", "candidates"])
    section("mention_grok_skip", "Mention Grok skips", ["time", "mention_id", "author_id", "incoming_text"])
    section("hot_post_reply_grok_skip", "Hot-post Grok skips", ["time", "hot_post_reply_id", "author_id", "incoming_text"])
    section("quote_tweet_grok_skip", "Quote-tweet Grok skips", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text"])
    section("mention_skipped", "Mention direct skips", ["time", "mention_id", "author_id", "incoming_text", "reason"])
    section("hot_post_reply_skipped", "Hot-post direct skips", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reason"])
    section("quote_tweet_skipped", "Quote-tweet direct skips", ["time", "quote_tweet_id", "reason"])
    section("api_cooldown_entered", "API cooldowns entered", ["time", "reason", "until"])
    section("used_history_migrated", "Used-history migrations", ["time", "legacy_file", "json_file"])
    section("used_history_normalized", "Used-history normalizations", ["time", "json_file"])

    api_health = report.get("api_health") or {}
    api_errors = api_health.get("errors") or []
    handled_restrictions = api_health.get("handled_restrictions") or []
    cooldown_active = api_health.get("cooldown_active") or []
    post_cooldown_errors = api_health.get("post_cooldown_errors") or []
    if api_errors or handled_restrictions or cooldown_active:
        out.append("## API health")
        if api_errors:
            out.append(md_table_row(["time", "service", "endpoint", "status", "window", "remaining", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in api_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                    item.get("remaining", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if handled_restrictions:
            out.append("Handled API restrictions:")
            out.append(md_table_row(["time", "service", "endpoint", "status", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---"]))
            for item in handled_restrictions:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if cooldown_active:
            out.append("Cooldown-active checks:")
            out.append(md_table_row(["time", "until", "reason"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in cooldown_active:
                out.append(md_table_row([item.get("time", ""), item.get("until", ""), item.get("reason", "")]))
            out.append("")
        if post_cooldown_errors:
            out.append("Post-cooldown errors:")
            out.append(md_table_row(["time", "service", "endpoint", "status", "window"]))
            out.append(md_table_row(["---", "---", "---", "---", "---"]))
            for item in post_cooldown_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                ]))
            out.append("")
        if api_health.get("not_rate_limited"):
            out.append("503/5xx summary: likely upstream/API-side failure, not quota exhaustion; remaining quota was non-zero on recorded error headers.")
            out.append("")
        if handled_restrictions:
            out.append("403 restriction summary: target conversation controls disallowed the reply; handled locally without quota/cooldown impact.")
            out.append("")

    self_test_errors = report.get("self_test_errors") or []
    if self_test_errors:
        out.append("## Self-test failures")
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in self_test_errors:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
        out.append("")

    errs = report.get("errors_and_warnings") or []
    out.append("## Errors / warnings")
    if not errs:
        out.append("None found in selected window.")
    else:
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in errs:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
    out.append("")

    if report.get("lifecycle"):
        out.append("## Lifecycle")
        out.append("```text")
        for item in report["lifecycle"]:
            out.append(f"{item['time']} {item['level']} {item['message']}")
        out.append("```")
        out.append("")

    cfg = report.get("latest_config") or {}
    if cfg:
        out.append("## Latest config seen")
        if cfg.get("_carried_forward"):
            out.append("Config source: carried forward from previous digest state.")
        elif cfg.get("_carried_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: backfilled from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous") and cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from previous digest state and earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous"):
            out.append("Config source: current window plus missing values from previous digest state.")
        keep = [
            "MAX_AUTO_REPLIES_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY", "MIN_SECONDS_BETWEEN_REPLIES",
            "REPLY_CHECK_EVERY_SECONDS", "MAX_MENTIONS_PER_CHECK", "MENTIONS_MAX_PAGES_PER_CHECK",
            "QUOTE_CHECK_EVERY_SECONDS", "QUOTE_LOOKUP_API_MAX_RESULTS", "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
            "QUOTE_CHECK_SPACING_RETRY_SECONDS", "ENABLE_HOT_POST_REPLY_CHECKS",
            "MAX_HOT_POST_REPLIES_PER_CHECK", "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
            "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
            "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
            "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
            "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
            "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
            "POST_SLEEP_MIN", "POST_SLEEP_MAX",
        ]
        out.append("```text")
        for k in keep:
            if k in cfg:
                out.append(f"{k}={cfg[k]}")
        out.append("```")
        out.append("")

    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Summarise MrsMThatcher bot logs into a compact digest.")
    ap.add_argument(
        "logs",
        nargs="*",
        type=Path,
        help="Optional explicit log files. If omitted, logs are auto-discovered in the current directory.",
    )
    ap.add_argument("--since", help="Only include records at/after this local timestamp, e.g. '2026-06-25 08:00'. Overrides saved resume time.")
    ap.add_argument("--until", help="Only include records at/before this local timestamp.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown.")
    ap.add_argument("--max-text", type=int, default=280, help="Maximum text length per field in report. Default: 280.")
    ap.add_argument("--glob", default="mrsMThatcher*.log*", help="Log glob to use when no explicit log files are supplied. Default: mrsMThatcher*.log*")
    ap.add_argument("--state-file", type=Path, default=Path(".mrs_log_digest_state.json"), help="Resume-state file. Default: .mrs_log_digest_state.json")
    ap.add_argument("--no-state", action="store_true", help="Do not read or update the resume-state file.")
    ap.add_argument("--reset-state", action="store_true", help="Ignore any existing resume-state file for this run; save the new end timestamp afterwards.")
    ap.add_argument("--no-update-state", action="store_true", help="Read resume state, but do not write the new end timestamp.")
    args = ap.parse_args(argv)

    if args.logs:
        logs = args.logs
    else:
        logs = discover_logs(Path.cwd(), args.glob)

    if not logs:
        raise SystemExit(
            f"No log files found. Run this in the log directory or pass files explicitly. "
            f"Auto-discovery pattern was: {args.glob!r}"
        )

    since_source = None
    since_exclusive = False
    resume_boundary_fingerprints: set[str] = set()

    if args.since:
        since = parse_dt(args.since)
        since_source = "manual --since"
        since_exclusive = False
    elif not args.no_state and not args.reset_state:
        resume_data = read_resume_data(args.state_file)
        since = None
        if resume_data:
            try:
                since = parse_dt(resume_data.get("last_log_entry_time"))
            except Exception as e:
                print(
                    f"WARNING: ignoring invalid resume timestamp in {args.state_file}: "
                    f"{resume_data.get('last_log_entry_time')!r} ({e})",
                    file=sys.stderr,
                )
                since = None
            resume_boundary_fingerprints = {
                str(value)
                for value in resume_data.get("last_log_entry_fingerprints", [])
                if value
            }
        if since:
            since_source = "saved resume state"
            since_exclusive = not bool(resume_boundary_fingerprints)
    else:
        since = None

    until = parse_dt(args.until)
    records = read_records(logs, since, until, since_exclusive=since_exclusive)
    if since is not None and resume_boundary_fingerprints:
        records = [
            record
            for record in records
            if not (record.ts == since and record_fingerprint(record) in resume_boundary_fingerprints)
        ]
    input_files = summarize_input_files(logs, since, until, since_exclusive=since_exclusive)
    report = analyse(records, max_text=args.max_text)

    report["log_files"] = [str(p) for p in logs]
    report["input_files"] = input_files
    report["input_warning"] = None
    if not records and any(int(item.get("records_in_window") or 0) > 0 for item in input_files):
        report["input_warning"] = (
            "selected log sources contain timestamped records inside the requested window, "
            "but 0 records survived filtering"
        )
    report["requested_since"] = dt_text(since) if since else None
    report["since_source"] = since_source
    report["since_exclusive"] = since_exclusive
    report["resume_boundary_fingerprint_count"] = len(resume_boundary_fingerprints)
    report["resume_state_file"] = None if args.no_state else str(args.state_file)
    report["state_updated"] = False

    # v5: if this incremental window has no startup Config lines, scan earlier
    # records in the same log files for the most recent Config values before
    # the window. This avoids "5 / ?" budget output after quiet windows, even
    # when the digest resume state has not yet stored config context.
    cutoff_for_backscan = records[0].ts if records else since
    if cutoff_for_backscan is not None:
        backscan_config, backscan_ts = find_latest_config_before(logs, cutoff_for_backscan)
        if backscan_config:
            report["latest_config"] = merge_context_from_log_backscan(
                report.get("latest_config") or {},
                backscan_config,
                backscan_ts=backscan_ts,
            )
            report["config_backscan_timestamp"] = dt_text(backscan_ts) if backscan_ts else None

    if not args.no_state and not args.reset_state:
        apply_saved_context(report, args.state_file)
    else:
        refresh_derived(report)

    if records and not args.no_state and not args.no_update_state:
        last_ts = records[-1].ts
        save_resume_time(args.state_file, last_ts, records, report, logs)
        report["state_updated"] = True
        report["saved_last_log_entry_time"] = dt_text(last_ts)
    elif not records:
        report["saved_last_log_entry_time"] = dt_text(since) if since else None

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(report))
        if records and not args.no_state and not args.no_update_state:
            print(f"\n<!-- resume state updated: {args.state_file} last_log_entry_time={dt_text(records[-1].ts)} -->")
        elif not records:
            print("\n<!-- no matching records; resume state not advanced -->")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
