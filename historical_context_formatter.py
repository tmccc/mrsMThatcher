#!/usr/bin/env python3
"""Deterministic historical-context replies backed only by canonical research packets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

DEFAULT_RESEARCH_DIR = Path("semantic_alignment_research/quote_research_full_001")
DEFAULT_MAXIMUM_LENGTH = 4000
MAXIMUM_SUPPORTED_LENGTH = 25_000
VERIFICATION_LABELS = {
    "exact": "Exact wording",
    "normalised": "Normalised wording",
    "excerpt": "Verified excerpt",
    "variant": "Historically verified variant",
    "paraphrase": "Historical paraphrase",
    "composite": "Composite wording",
    "misattributed": "Commonly misattributed wording",
    "unverified": "Exact wording not verified",
}
SOURCE_PRIORITIES = (
    ("margaretthatcher.org", "Margaret Thatcher Foundation transcript"),
    ("hansard", "Hansard"),
    ("gov.uk", "Original speech transcript"),
    ("archive.org", "Thatcher-authored publication"),
)
QUOTATION_AGGREGATORS = (
    "allgreatquotes", "azquotes", "brainyquote", "goodreads", "libquotes",
    "magicalquote", "notable-quotes", "picturequotes", "quotefancy", "quotepark",
    "quotes.net", "quotetab", "quotery", "wikiquote", "wisesayings", "wonderfulquote",
)
URL_WEIGHT = 23
UNKNOWN_VALUES = {"", "n/a", "n.a.", "none", "not available", "unknown", "unavailable"}


class AmbiguousContextReplyOutcome(RuntimeError):
    """A durable sending record exists, so repeating the reply could duplicate it."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def quote_text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", str(text or "").strip()).encode()).hexdigest()


def atomic_write_json(path: Path, value: Any, *, durable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write("\n")
            handle.flush()
            if durable: os.fsync(handle.fileno())
        os.replace(temporary, path)
        if durable:
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def durable_unlink(path: Path) -> None:
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def load_and_validate_corpus(research_dir: Path = DEFAULT_RESEARCH_DIR) -> tuple[dict[str, Any], set[str]]:
    from semantic_alignment.quote_research_gemini import TOP_LEVEL_FIELDS, validate_packet

    packets_document = json.loads((research_dir / "research_packets.json").read_text())
    manifest_document = json.loads((research_dir / "corpus_manifest.json").read_text())
    status = json.loads((research_dir / "final_unresolved" / "final_research_status.json").read_text())
    packets = packets_document.get("items")
    records = manifest_document.get("records")
    if (
        not isinstance(records, list)
        or len(records) != 632
        or any(not isinstance(record, dict) or not record.get("quote_id") for record in records)
        or len({record["quote_id"] for record in records}) != 632
    ):
        raise RuntimeError("historical context replies require exactly 632 unique manifest records")
    manifest = {record["quote_id"]: record for record in records}
    unresolved_records = status.get("unresolved_quote_ids")
    if (
        not isinstance(unresolved_records, list)
        or len(unresolved_records) != 6
        or len(set(unresolved_records)) != 6
        or any(not re.fullmatch(r"[0-9a-f]{64}", str(quote_id or "")) for quote_id in unresolved_records)
    ):
        raise RuntimeError("historical context replies require exactly six unique unresolved quote IDs")
    unresolved = set(unresolved_records)
    if not isinstance(packets, dict) or len(packets) != 626:
        raise RuntimeError("historical context replies require exactly 626 completed packets")
    if len(manifest) != 632 or set(manifest) != set(packets) | unresolved:
        raise RuntimeError("canonical 632-record manifest does not partition into 626 completed and six unresolved")
    if set(packets) & unresolved:
        raise RuntimeError("unresolved quote appears in completed packet collection")
    for quote_id, packet in packets.items():
        if packet.get("quote_id") != quote_id or packet.get("quote_text") != manifest[quote_id].get("quote_text"):
            raise RuntimeError(f"canonical quote identity mismatch: {quote_id}")
        try: validate_packet({field: packet[field] for field in TOP_LEVEL_FIELDS}, {"quote_id": quote_id, "quote_text": packet["quote_text"]})
        except (KeyError, ValueError) as exc: raise RuntimeError(f"completed packet is not schema-valid: {quote_id}: {exc}") from exc
        if packet.get("verification_status") not in VERIFICATION_LABELS:
            raise RuntimeError(f"unsupported verification status: {quote_id}")
    return packets, unresolved


def packet_for_posted_quote(packets: dict[str, Any], unresolved: set[str], quote_hash: str,
                            quote_text: str) -> dict[str, Any] | None:
    """Resolve bot-normalized quote identity without changing canonical IDs or wording."""
    if quote_hash in unresolved: return None
    direct = packets.get(quote_hash)
    if direct is not None:
        return direct if re.sub(r"\s+", " ", direct["quote_text"].strip()) == re.sub(r"\s+", " ", quote_text.strip()) else None
    normalised = re.sub(r"\s+", " ", quote_text.strip())
    matches = [packet for packet in packets.values() if re.sub(r"\s+", " ", packet["quote_text"].strip()) == normalised]
    return matches[0] if len(matches) == 1 else None


def _source_rank(source: dict[str, Any]) -> tuple[int, int, str]:
    title = str(source.get("title") or "").lower(); url = str(source.get("url") or "")
    host = urlsplit(url).netloc.lower(); text = f"{title} {host} {source.get('source_type','')}"
    for index, (marker, _description) in enumerate(SOURCE_PRIORITIES):
        if marker in text: return index, len(url), url
    if any(word in text for word in ("speech", "transcript", "statement")): return 2, len(url), url
    if any(word in text for word in ("memoir", "book", "publication")): return 3, len(url), url
    if any(word in text for word in ("interview", "bbc", "newspaper", "times", "guardian")): return 4, len(url), url
    return 5, len(url), url


def _locator_rank(locator: str) -> int:
    text = locator.lower()
    if "margaret thatcher foundation" in text:
        return 0
    if "hansard" in text:
        return 1
    if any(word in text for word in ("archive", "document", "speech", "statement", "transcript")):
        return 2
    if any(word in text for word in ("book", "memoir", "statecraft", "downing street years", "path to power")):
        return 3
    return 6


def _is_grounding_redirect(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.netloc.lower() == "vertexaisearch.cloud.google.com"
        and parsed.path.startswith("/grounding-api-redirect/")
    )


def select_primary_source(packet: dict[str, Any]) -> dict[str, str] | None:
    locator = " ".join(str(packet.get("stable_locator") or "").split())
    valid_locator = locator if locator.lower() not in UNKNOWN_VALUES else ""
    all_sources = [source for source in packet.get("sources", []) if isinstance(source, dict)
               and str(source.get("title") or "").strip()
               and str(source.get("url") or "").startswith(("https://", "http://"))]
    eligible_sources = [
        source for source in all_sources
        if not any(
            marker in f"{source.get('title', '')} {source.get('url', '')}".lower()
            for marker in QUOTATION_AGGREGATORS
        )
    ]
    sources = [
        source for source in eligible_sources
        if not _is_grounding_redirect(str(source.get("url") or ""))
    ]
    if not sources:
        if valid_locator:
            return {"title": valid_locator, "url": "", "source_type": "canonical_stable_locator"}
        if eligible_sources:
            source = min(eligible_sources, key=_source_rank)
            title = " ".join(str(source["title"]).split())
            if packet.get("verification_status") in {"unverified", "misattributed"}:
                title = "Attribution record: " + title
            return {
                "title": title,
                "url": "",
                "source_type": "grounded_source_title_without_public_url",
            }
        if packet.get("verification_status") in {"unverified", "misattributed"} and all_sources:
            source = min(all_sources, key=_source_rank)
            source_url = str(source["url"]).strip()
            return {"title": "Attribution record: " + " ".join(str(source["title"]).split()),
                    "url": "" if _is_grounding_redirect(source_url) else source_url,
                    "source_type": "attribution_error_documentation"}
        return None
    source = min(sources, key=_source_rank)
    if valid_locator and _locator_rank(valid_locator) < _source_rank(source)[0]:
        return {"title": valid_locator, "url": "", "source_type": "canonical_stable_locator"}
    return {"title": " ".join(str(source["title"]).split()), "url": str(source["url"]).strip(),
            "source_type": str(source.get("source_type") or "unknown")}


def classify_source(source: dict[str, Any] | None) -> str:
    """Map a selected canonical source to a stable, non-provider digest class."""
    if not source:
        return "unavailable"
    title = str(source.get("title") or "").lower()
    url = str(source.get("url") or "")
    source_type = str(source.get("source_type") or "").lower()
    host = urlsplit(url).netloc.lower()
    text = f"{title} {host} {source_type}"
    if "margaretthatcher.org" in text or "margaret thatcher foundation" in text:
        return "Margaret Thatcher Foundation"
    if "hansard" in text:
        return "Hansard"
    if any(term in text for term in ("thatcher-authored", "memoir", "book", "publication")):
        return "Thatcher-authored publication"
    if any(term in text for term in ("interview", "bbc", "newspaper", "the times", "guardian")):
        return "contemporary interview"
    if any(term in text for term in ("conservative", "conservatives.com", "conservative party")):
        return "official Conservative publication"
    if source_type == "canonical_stable_locator":
        return "canonical locator only"
    if not url or _is_grounding_redirect(url):
        return "no public URL"
    if any(term in text for term in ("speech", "transcript", "statement", "gov.uk")):
        return "original speech transcript"
    return "other authoritative source"


def _sentence(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if text.lower() in UNKNOWN_VALUES: return ""
    return text if text.endswith((".", "?", "!")) else text + "."


def _first_sentence(*values: Any) -> str:
    for value in values:
        sentence = _sentence(value)
        if sentence:
            return sentence
    return ""


def _shorten_words(text: str, maximum: int) -> str:
    if maximum <= 0: return ""
    if len(text) <= maximum: return text
    if maximum < 2: return ""
    shortened = text[: maximum - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return shortened + "…" if shortened else ""


def _has_forbidden_style(text: str) -> bool:
    emoji = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")
    return bool(re.search(r"(?:^|\s)#[A-Za-z0-9_]", text) or emoji.search(text))


def x_weighted_length(text: str) -> int:
    urls = re.findall(r"https?://\S+", text)
    return len(text) - sum(len(url) for url in urls) + URL_WEIGHT * len(urls)


def format_context_reply(packet: dict[str, Any], *, maximum_length: int = DEFAULT_MAXIMUM_LENGTH,
                         include_meaning: bool = True, include_source: bool = True,
                         include_verification: bool = True) -> dict[str, Any] | None:
    if type(maximum_length) is not int or not 120 <= maximum_length <= MAXIMUM_SUPPORTED_LENGTH:
        raise ValueError(f"maximum_length must be from 120 to {MAXIMUM_SUPPORTED_LENGTH}")
    source = select_primary_source(packet) if include_source else None
    if include_source and source is None: return None
    event = _sentence(packet.get("source_event")) or "Source event not established."
    raw_date = " ".join(str(packet.get("date") or "").split()).strip()
    date = "Unknown" if raw_date.lower() in UNKNOWN_VALUES else raw_date
    immediate = _first_sentence(packet.get("immediate_subject"), packet.get("historical_context"))
    meaning = _first_sentence(packet.get("intended_argument"), packet.get("literal_meaning")) if include_meaning else ""
    verification = VERIFICATION_LABELS[packet["verification_status"]]
    event_label = (
        "Occasion"
        if packet["verification_status"] in {"exact", "normalised", "excerpt", "variant"}
        else "Source event"
    )

    def render(current_meaning: str, current_context: str, current_event: str, current_title: str) -> str:
        context_lines = ["Historical context", f"{event_label}: {current_event}", f"Date: {date}"]
        if current_context: context_lines.append(f"Immediate context: {current_context}")
        sections = ["\n".join(context_lines)]
        if current_meaning: sections.append(f"Meaning: {current_meaning}")
        provenance = []
        if include_verification: provenance.append(f"Verification: {verification}")
        if source: provenance.append(f"Source: {current_title}" + (f"\n{source['url']}" if source["url"] else ""))
        if provenance: sections.append("\n".join(provenance))
        return "\n\n".join(sections)

    source_title = source["title"] if source else ""
    original_meaning = meaning
    original_immediate = immediate
    original_event = event
    original_source_title = source_title
    text = render(meaning, immediate, event, source_title)
    # Meaning is always compressed or removed before any provenance field.
    if x_weighted_length(text) > maximum_length and meaning:
        fixed = x_weighted_length(render("", immediate, event, source_title))
        meaning = _shorten_words(meaning, maximum_length - fixed - len("\n\nMeaning: "))
        text = render(meaning, immediate, event, source_title)
    if x_weighted_length(text) > maximum_length:
        meaning = ""; text = render("", immediate, event, source_title)
    if x_weighted_length(text) > maximum_length and immediate:
        excess = x_weighted_length(text) - maximum_length
        immediate = _shorten_words(immediate, max(0, len(immediate) - excess))
        text = render("", immediate, event, source_title)
    if x_weighted_length(text) > maximum_length:
        excess = x_weighted_length(text) - maximum_length
        event = _shorten_words(event, max(12, len(event) - excess))
        text = render("", immediate, event, source_title)
    if x_weighted_length(text) > maximum_length and source:
        excess = x_weighted_length(text) - maximum_length
        source_title = _shorten_words(source_title, max(8, len(source_title) - excess))
        text = render("", immediate, event, source_title)
    weighted = x_weighted_length(text)
    if weighted > maximum_length or _has_forbidden_style(text): return None
    shortened = any((meaning != original_meaning, immediate != original_immediate,
                     event != original_event, source_title != original_source_title))
    return {"text": text, "character_count": weighted, "raw_character_count": len(text), "maximum_length": maximum_length,
            "verification_label": verification if include_verification else None, "source": source,
            "source_class": classify_source(source),
            "historical_confidence": packet.get("research_confidence") or "unavailable",
            "shortening_applied": shortened,
            "meaning_included": bool(meaning),
            "meaning_omitted": not bool(meaning),
            "source_omitted": not bool(source),
            "verification_omitted": not include_verification,
            "quote_id": packet["quote_id"]}


class HistoricalContextReplyStore:
    """Independent transactional state for confirmed context replies."""
    def __init__(self, history_path: Path, receipt_path: Path):
        self.history_path = history_path; self.receipt_path = receipt_path

    def history(self) -> dict[str, Any]:
        if not self.history_path.exists(): return {"schema_version": 1, "items": {}}
        value = json.loads(self.history_path.read_text())
        if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
                or value.get("schema_version") != 1 or not isinstance(value.get("items"), dict)):
            raise RuntimeError("invalid context reply history")
        for parent_post_id, item in value["items"].items():
            if not isinstance(item, dict) or str(item.get("parent_post_id") or "") != str(parent_post_id):
                raise RuntimeError("invalid context reply history item")
            if item.get("status") == "completed":
                receipt = {key: value for key, value in item.items() if key != "status"}
                if not self._valid_receipt(receipt):
                    raise RuntimeError("invalid completed context reply history")
            elif item.get("status") == "failed":
                if (
                    not re.fullmatch(r"\d{1,30}", str(parent_post_id))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("quote_id") or ""))
                    or not isinstance(item.get("reply_text"), str)
                    or not item["reply_text"].strip()
                    or type(item.get("attempt_count")) is not int
                    or item["attempt_count"] < 1
                    or not isinstance(item.get("failure"), str)
                ):
                    raise RuntimeError("invalid failed context reply history")
            else:
                raise RuntimeError("invalid context reply history status")
        return value

    def _save_history(self, value: dict[str, Any]) -> None: atomic_write_json(self.history_path, value)

    @staticmethod
    def _valid_receipt(receipt: Any) -> bool:
        if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
                or receipt.get("schema_version") != 1):
            return False
        required = {
            "schema_version", "parent_post_id", "reply_post_id", "quote_id",
            "reply_text", "reply_epoch", "confirmed_at",
        }
        allowed = required | {"lifecycle_state", "started_at", "attempt_number"}
        if set(receipt) not in (required, allowed):
            return False
        if "lifecycle_state" in receipt and receipt.get("lifecycle_state") != "confirmed":
            return False
        if "started_at" in receipt and (not isinstance(receipt["started_at"], str) or not receipt["started_at"].strip()):
            return False
        if "attempt_number" in receipt and (
            type(receipt["attempt_number"]) is not int or receipt["attempt_number"] < 1
        ):
            return False
        if not re.fullmatch(r"\d{1,30}", str(receipt.get("parent_post_id") or "")):
            return False
        if not re.fullmatch(r"\d{1,30}", str(receipt.get("reply_post_id") or "")):
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("quote_id") or "")):
            return False
        if not isinstance(receipt.get("reply_text"), str) or not receipt["reply_text"].strip():
            return False
        if type(receipt.get("reply_epoch")) is not int or receipt["reply_epoch"] < 0:
            return False
        return isinstance(receipt.get("confirmed_at"), str) and bool(receipt["confirmed_at"].strip())

    @staticmethod
    def _valid_sending_receipt(receipt: Any) -> bool:
        required = {
            "schema_version", "lifecycle_state", "parent_post_id", "quote_id",
            "reply_text", "reply_epoch", "started_at", "attempt_number",
        }
        return bool(
            isinstance(receipt, dict)
            and set(receipt) == required
            and type(receipt.get("schema_version")) is int
            and receipt.get("schema_version") == 1
            and receipt.get("lifecycle_state") == "sending"
            and re.fullmatch(r"\d{1,30}", str(receipt.get("parent_post_id") or ""))
            and re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("quote_id") or ""))
            and isinstance(receipt.get("reply_text"), str)
            and receipt["reply_text"].strip()
            and type(receipt.get("reply_epoch")) is int
            and receipt["reply_epoch"] >= 0
            and isinstance(receipt.get("started_at"), str)
            and receipt["started_at"].strip()
            and type(receipt.get("attempt_number")) is int
            and receipt["attempt_number"] >= 1
        )

    def reconcile_receipt(self) -> bool:
        if not self.receipt_path.exists(): return False
        receipt = json.loads(self.receipt_path.read_text())
        if self._valid_sending_receipt(receipt):
            history = self.history()
            previous = history["items"].get(str(receipt["parent_post_id"]))
            if (
                previous
                and previous.get("status") == "failed"
                and previous.get("quote_id") == receipt["quote_id"]
                and previous.get("reply_text") == receipt["reply_text"]
                and previous.get("attempt_count") == receipt["attempt_number"]
            ):
                durable_unlink(self.receipt_path)
                return False
            raise AmbiguousContextReplyOutcome(
                "historical context reply was interrupted while sending; manual reconciliation required"
            )
        if not self._valid_receipt(receipt):
            raise RuntimeError("invalid historical context reply receipt")
        history = self.history()
        parent_post_id = str(receipt["parent_post_id"])
        previous = history["items"].get(parent_post_id)
        if previous and previous.get("status") == "completed":
            comparable = {key: previous.get(key) for key in receipt}
            if comparable != receipt:
                raise RuntimeError("historical context reply receipt conflicts with completed history")
        history["items"][parent_post_id] = {**receipt, "status": "completed"}
        self._save_history(history); durable_unlink(self.receipt_path); return True

    def record_failure(self, parent_post_id: str, quote_id: str, text: str, error: BaseException) -> None:
        history = self.history(); previous = history["items"].get(str(parent_post_id), {})
        history["items"][str(parent_post_id)] = {"parent_post_id": str(parent_post_id), "quote_id": quote_id,
            "reply_text": text, "status": "failed", "failure": f"{type(error).__name__}: {error}",
            "attempt_count": int(previous.get("attempt_count", 0)) + 1, "updated_at": utc_now()}
        self._save_history(history)

    def post(self, *, parent_post_id: str, quote_id: str, reply_text: str,
             create_post: Callable[..., dict[str, Any]], now_epoch: Callable[[], int], dry_run: bool = False) -> dict[str, Any]:
        if (
            not re.fullmatch(r"\d{1,30}", str(parent_post_id or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(quote_id or ""))
            or not isinstance(reply_text, str)
            or not reply_text.strip()
        ):
            raise ValueError("invalid historical context reply request")
        self.reconcile_receipt(); history = self.history(); previous = history["items"].get(str(parent_post_id))
        if previous and previous.get("quote_id") != quote_id:
            raise RuntimeError("historical context reply quote identity conflicts with parent history")
        if previous and previous.get("status") == "completed": return {**previous, "status": "already_completed"}
        if dry_run: return {"status": "dry_run", "parent_post_id": str(parent_post_id), "quote_id": quote_id,
                            "reply_text": reply_text, "character_count": len(reply_text)}
        reply_epoch = int(now_epoch())
        started_at = utc_now()
        sending = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": str(parent_post_id),
            "quote_id": quote_id,
            "reply_text": reply_text,
            "reply_epoch": reply_epoch,
            "started_at": started_at,
            "attempt_number": int(previous.get("attempt_count", 0) if previous else 0) + 1,
        }
        atomic_write_json(self.receipt_path, sending)

        def finish_confirmed_failure(error: Exception) -> dict[str, str]:
            try:
                self.record_failure(parent_post_id, quote_id, reply_text, error)
            except Exception as persistence_error:
                raise AmbiguousContextReplyOutcome(
                    "could not persist context reply failure history; manual reconciliation required"
                ) from persistence_error
            try:
                durable_unlink(self.receipt_path)
            except Exception as persistence_error:
                raise AmbiguousContextReplyOutcome(
                    "could not clear context reply sending record; manual reconciliation required"
                ) from persistence_error
            return {"status": "failed", "error": str(error)}

        try:
            response = create_post(
                text=reply_text,
                media_ids=None,
                reply_to_id=str(parent_post_id),
                made_with_ai=False,
            )
        except Exception as exc:
            if type(exc).__name__ == "AmbiguousRemotePostOutcome":
                raise AmbiguousContextReplyOutcome(
                    "remote context reply outcome is ambiguous; manual reconciliation required"
                ) from exc
            return finish_confirmed_failure(exc)
        reply_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        if not re.fullmatch(r"\d{1,30}", str(reply_id or "")):
            error = RuntimeError("context reply did not return a valid numeric post id")
            return finish_confirmed_failure(error)
        receipt = {
            **sending,
            "lifecycle_state": "confirmed",
            "reply_post_id": str(reply_id),
            "confirmed_at": utc_now(),
        }
        try:
            atomic_write_json(self.receipt_path, receipt)
        except Exception as exc:
            raise AmbiguousContextReplyOutcome(
                "could not persist confirmed reply receipt; manual reconciliation required"
            ) from exc
        self.reconcile_receipt()
        return {"status": "completed", **receipt}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline historical context reply formatter")
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument("--quote-id"); parser.add_argument("--quote-text"); parser.add_argument("--maximum-length", type=int, default=DEFAULT_MAXIMUM_LENGTH)
    args = parser.parse_args(argv); packets, unresolved = load_and_validate_corpus(args.research_dir)
    quote_id = args.quote_id or (quote_text_hash(args.quote_text) if args.quote_text else None)
    if not quote_id: parser.error("--quote-id or --quote-text is required")
    packet = packets.get(quote_id) if args.quote_id else packet_for_posted_quote(packets, unresolved, quote_id, args.quote_text)
    if quote_id in unresolved or packet is None: raise SystemExit("no completed canonical research packet; no reply")
    result = format_context_reply(packet, maximum_length=args.maximum_length)
    if result is None: raise SystemExit("canonical packet cannot produce a supported reply")
    print(result["text"])
    print(f"\nCharacter count: raw={result['raw_character_count']} x_weighted={result['character_count']}/{result['maximum_length']}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
