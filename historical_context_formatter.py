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
HISTORICAL_CONTEXT_FORMATTER_V2 = "historical_context_reply_schema_v2"
_V2_UNCERTAIN_STATUSES = {"paraphrase", "composite", "misattributed", "unverified"}
_V2_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "hers", "him", "his", "i", "if", "in", "into", "is",
    "it", "its", "not", "of", "on", "or", "our", "she", "that", "the", "their", "them", "there",
    "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your", "must", "should", "can", "could", "may", "than", "then",
}
_V2_MONTHS = {
    "january": "January", "february": "February", "march": "March", "april": "April",
    "may": "May", "june": "June", "july": "July", "august": "August",
    "september": "September", "october": "October", "november": "November", "december": "December",
}
_FORMATTER_METADATA_KEYS = {
    "formatter_version", "template_variant", "meaning_included", "meaning_decision_reason",
    "raw_character_count", "weighted_character_count", "verification_label", "source_class",
    "historical_confidence", "shortening_applied",
}
_MARGARET_THATCHER_CANONICAL_SPEAKER = "margaret thatcher"
THATCHER_ATTRIBUTION_RULE_VERSION = "canonical-principal-speaker-v2-reject-misattributed"


class AmbiguousContextReplyOutcome(RuntimeError):
    """A durable sending record exists, so repeating the reply could duplicate it."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def quote_text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", str(text or "").strip()).encode()).hexdigest()


def packet_is_attributed_to_margaret_thatcher(packet: dict[str, Any]) -> bool:
    """Accept only packets whose canonical principal speaker is Thatcher herself."""
    if not isinstance(packet, dict):
        return False
    verification = str(packet.get("verification_status") or "").strip().casefold()
    if verification == "misattributed":
        return False
    speaker = re.sub(r"\s+", " ", str(packet.get("speaker") or "").strip())
    principal = re.split(r"\s*(?:\(|/)\s*", speaker, maxsplit=1)[0].strip().casefold()
    return principal == _MARGARET_THATCHER_CANONICAL_SPEAKER


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
        return direct if (
            packet_is_attributed_to_margaret_thatcher(direct)
            and re.sub(r"\s+", " ", direct["quote_text"].strip()) == re.sub(r"\s+", " ", quote_text.strip())
        ) else None
    normalised = re.sub(r"\s+", " ", quote_text.strip())
    matches = [
        packet for packet in packets.values()
        if packet_is_attributed_to_margaret_thatcher(packet)
        and re.sub(r"\s+", " ", packet["quote_text"].strip()) == normalised
    ]
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


def _v2_clean(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return "" if text.lower() in UNKNOWN_VALUES else text


def _v2_one_sentence(value: Any) -> str:
    text = _v2_clean(value)
    if not text:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence if sentence.endswith((".", "?", "!")) else sentence + "."


def _v2_british_dates_in_text(value: str) -> str:
    pattern = re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),\s*(\d{4})\b",
        re.I,
    )
    return pattern.sub(
        lambda match: f"{int(match.group(2))} {_V2_MONTHS[match.group(1).lower()]} {match.group(3)}",
        value,
    )


def _v2_british_date(value: Any) -> str:
    text = _v2_clean(value)
    if not text:
        return ""
    published = re.fullmatch(r"unknown\s*\(published\s+(\d{4})\)", text, re.I)
    if published:
        return f"published in {published.group(1)}"
    decade = re.fullmatch(r"(\d{4}s)\s*\(exact date unknown\)", text, re.I)
    if decade:
        return decade.group(1)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = iso.groups()
        names = tuple(_V2_MONTHS.values())
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{int(day)} {names[int(month)-1]} {year}"
    american = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if american and american.group(1).lower() in _V2_MONTHS:
        return f"{int(american.group(2))} {_V2_MONTHS[american.group(1).lower()]} {american.group(3)}"
    british = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if british and british.group(2).lower() in _V2_MONTHS:
        return f"{int(british.group(1))} {_V2_MONTHS[british.group(2).lower()]} {british.group(3)}"
    return text if re.fullmatch(r"\d{4}", text) else _v2_british_dates_in_text(text)


def _v2_tokens(value: Any) -> set[str]:
    return {
        word for word in re.findall(r"[a-z0-9]+", _v2_clean(value).lower())
        if len(word) > 2 and word not in _V2_STOPWORDS
    }


def _v2_overlap(left: Any, right: Any) -> dict[str, float]:
    a, b = _v2_tokens(left), _v2_tokens(right)
    intersection = len(a & b)
    return {
        "jaccard": round(intersection / len(a | b), 4) if a | b else 0.0,
        "left_coverage": round(intersection / len(a), 4) if a else 0.0,
        "right_coverage": round(intersection / len(b), 4) if b else 0.0,
    }


def _v2_distinct_count(value: Any, *against: Any) -> int:
    used: set[str] = set()
    for item in against:
        used |= _v2_tokens(item)
    return len(_v2_tokens(value) - used)


def _v2_meaning_decision(packet: dict[str, Any], context: str) -> dict[str, Any]:
    meaning = _v2_one_sentence(packet.get("intended_argument")) or _v2_one_sentence(packet.get("literal_meaning"))
    quote = packet["quote_text"]
    mechanism = _v2_clean(packet.get("mechanism"))
    consequence = _v2_clean(packet.get("claimed_consequence"))
    quote_overlap = _v2_overlap(meaning, quote)
    context_overlap = _v2_overlap(meaning, context)
    mechanism_distinct = _v2_distinct_count(mechanism, quote, context, meaning)
    consequence_distinct = _v2_distinct_count(consequence, quote, context, meaning)
    obscure_reference = bool(re.search(
        r"\b(?:this|that|these|those|here|there|he|she|they|them|it|such|former|latter)\b",
        quote.lower(),
    ))
    counter_intuitive = bool(re.search(
        r"\b(?:paradox|contrary|although|despite|not .* but|rather than|unless|only if)\b",
        " ".join((quote, meaning, mechanism)).lower(),
    ))
    uncertain = packet.get("verification_status") in _V2_UNCERTAIN_STATUSES
    explicit_ambiguity = bool(packet.get("unresolved_questions")) or bool(_v2_clean(packet.get("text_variation_notes")))
    rhetorical_wrapper = meaning.lower().startswith((
        "that ", "to argue that ", "to assert that ", "to emphasise that ", "to emphasize that ",
        "to demonstrate that ", "to highlight that ",
    ))
    quote_states_mechanism = bool(re.search(
        r"\b(?:because|by|if|then|means?|results?|so that|cannot|requires?|depends?|leads?|creates?|"
        r"destroys?|without|when|where|better than|worse than)\b", quote, re.I,
    ))
    redundant_direct_proposition = (
        rhetorical_wrapper and not obscure_reference and "?" not in quote
        and (quote_states_mechanism or quote_overlap["left_coverage"] >= 0.35)
    )
    rule_outputs = {
        "meaning_quote_overlap": quote_overlap,
        "meaning_context_overlap": context_overlap,
        "mechanism_distinct_token_count": mechanism_distinct,
        "consequence_distinct_token_count": consequence_distinct,
        "obscure_reference_marker": obscure_reference,
        "counter_intuitive_marker": counter_intuitive,
        "uncertain_wording": uncertain,
        "explicit_ambiguity": explicit_ambiguity,
        "rhetorical_wrapper": rhetorical_wrapper,
        "quote_states_mechanism": quote_states_mechanism,
        "redundant_direct_proposition": redundant_direct_proposition,
    }
    if not meaning:
        included, reason = False, "No usable intended-argument or literal-meaning field."
    elif uncertain:
        included, reason = True, "Meaning retained because the wording is non-exact or uncertain."
    elif obscure_reference:
        included, reason = True, "Meaning retained because the quotation contains a context-dependent reference."
    elif counter_intuitive:
        included, reason = True, "Meaning retained because the argument is counter-intuitive or contrastive."
    elif redundant_direct_proposition:
        included, reason = False, "Meaning omitted because it restates a direct, self-contained proposition whose mechanism or substantive terms are already visible."
    elif mechanism_distinct >= 3:
        included, reason = True, "Meaning retained because the packet records a distinct explanatory mechanism."
    elif consequence_distinct >= 3:
        included, reason = True, "Meaning retained because the packet records a distinct claimed consequence."
    elif explicit_ambiguity and quote_overlap["right_coverage"] < 0.80:
        included, reason = True, "Meaning retained because the packet records ambiguity not resolved by the quotation alone."
    elif max(quote_overlap["left_coverage"], context_overlap["left_coverage"]) >= 0.78:
        included, reason = False, "Meaning omitted because its substantive terms are already present in the quotation or Context."
    else:
        included, reason = True, "Meaning retained because it adds substantive terms not supplied by the quotation or Context."
    return {"meaning": meaning, "meaning_included": included,
            "meaning_decision_reason": reason, "rule_outputs": rule_outputs}


def _v2_context_sentence(packet: dict[str, Any]) -> str:
    event = _v2_clean(packet.get("source_event"))
    uncertain_event = re.fullmatch(r"unknown\s*\((.+)\)", event, re.I)
    if uncertain_event:
        qualifier = uncertain_event.group(1).strip()
        event = "" if qualifier.lower() == "attributed" else qualifier[0].upper() + qualifier[1:]
    elif re.match(r"unknown\s+", event, re.I):
        remainder = re.sub(r"^unknown\s+", "", event, flags=re.I).strip()
        event = f"Attributed to a {remainder}" if remainder else ""
    event = _v2_british_dates_in_text(event).rstrip(". :;-")
    date = _v2_british_date(packet.get("date"))
    immediate = _v2_one_sentence(packet.get("immediate_subject")) or _v2_one_sentence(packet.get("historical_context"))
    immediate = _v2_british_dates_in_text(immediate.rstrip("."))
    if event and immediate and date: return f"{event}, {date}: {immediate}."
    if event and immediate: return f"{event}: {immediate}."
    if immediate and date: return f"{date}: {immediate}."
    if immediate: return immediate if immediate.endswith((".", "?", "!")) else immediate + "."
    if event and date: return f"{event}, {date}."
    if event: return event if event.endswith((".", "?", "!")) else event + "."
    if date: return f"The surviving record dates this wording to {date}, but does not establish its occasion."
    return "The surviving attribution does not establish an occasion, date or immediate historical issue."


def _v2_forbidden_style(text: str) -> bool:
    return bool(
        re.search(r"(?:^|\s)#[A-Za-z0-9_]", text)
        or re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF]", text)
        or "vertexaisearch.cloud.google.com/grounding-api-redirect/" in text
        or re.search(r"(?:^|[—:\s])(N/A|None|unknown|unavailable)(?:$|[.\s])", text, re.I)
        or re.search(r"\b(?:did you know|interesting fact|click here|learn more)\b", text, re.I)
    )


def format_context_reply_v2(
    packet: dict[str, Any], *, maximum_length: int = DEFAULT_MAXIMUM_LENGTH,
    include_meaning: bool = True, include_source: bool = True,
    include_verification: bool = True,
) -> dict[str, Any] | None:
    """Render the human-validated compact archive-entry formatter."""
    if type(maximum_length) is not int or not 120 <= maximum_length <= MAXIMUM_SUPPORTED_LENGTH:
        raise ValueError(f"maximum_length must be from 120 to {MAXIMUM_SUPPORTED_LENGTH}")
    source = select_primary_source(packet) if include_source else None
    if include_source and (not source or not _v2_clean(source.get("title"))):
        return None
    context = _v2_context_sentence(packet)
    decision = _v2_meaning_decision(packet, context)
    if not include_meaning:
        decision = {**decision, "meaning_included": False,
                    "meaning_decision_reason": "Meaning disabled by explicit formatter configuration."}
    verification = VERIFICATION_LABELS[packet["verification_status"]]
    sections = [f"Context — {context}"]
    if decision["meaning_included"]:
        sections.append(f"Meaning — {decision['meaning']}")
    if include_verification:
        sections.append(f"Verification — {verification}")
    if source:
        source_line = f"Source — {source['title']}"
        if source.get("url"):
            source_line += f"\n{source['url']}"
        sections.append(source_line)
    text = "\n\n".join(sections)
    weighted = x_weighted_length(text)
    if weighted > maximum_length or _v2_forbidden_style(text):
        return None
    if packet["verification_status"] in _V2_UNCERTAIN_STATUSES:
        variant = "compact_uncertain_wording"
    elif not decision["meaning_included"]:
        variant = "compact_without_redundant_meaning"
    elif not source or not source.get("url"):
        variant = "compact_no_public_url"
    else:
        variant = "compact_with_meaning"
    return {
        "quote_id": packet["quote_id"], "text": text, "character_count": weighted,
        "raw_character_count": len(text), "maximum_length": maximum_length,
        "historical_confidence": packet.get("research_confidence") or "unavailable",
        "meaning_included": bool(decision["meaning_included"]),
        "meaning_omitted": not bool(decision["meaning_included"]),
        "meaning_decision_reason": decision["meaning_decision_reason"],
        "meaning_rule_outputs": decision["rule_outputs"],
        "shortening_applied": False,
        "verification_label": verification if include_verification else None,
        "verification_omitted": not include_verification,
        "source": source, "source_class": classify_source(source), "source_omitted": not bool(source),
        "formatter_version": HISTORICAL_CONTEXT_FORMATTER_V2, "template_variant": variant,
        "weighted_character_count": weighted,
    }


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
                    or ("formatter_metadata" in item and not self._valid_formatter_metadata(item["formatter_metadata"]))
                ):
                    raise RuntimeError("invalid failed context reply history")
            else:
                raise RuntimeError("invalid context reply history status")
        return value

    def _save_history(self, value: dict[str, Any]) -> None: atomic_write_json(self.history_path, value)

    @staticmethod
    def _valid_formatter_metadata(value: Any) -> bool:
        return bool(
            isinstance(value, dict)
            and set(value) == _FORMATTER_METADATA_KEYS
            and isinstance(value.get("formatter_version"), str)
            and value["formatter_version"].startswith("historical_context_reply_schema_v")
            and isinstance(value.get("template_variant"), str)
            and bool(value["template_variant"])
            and type(value.get("meaning_included")) is bool
            and isinstance(value.get("meaning_decision_reason"), str)
            and bool(value["meaning_decision_reason"])
            and type(value.get("raw_character_count")) is int
            and value["raw_character_count"] >= 1
            and type(value.get("weighted_character_count")) is int
            and value["weighted_character_count"] >= 1
            and (value.get("verification_label") is None or isinstance(value["verification_label"], str))
            and isinstance(value.get("source_class"), str)
            and value.get("historical_confidence") in {"high", "medium", "low", "unavailable"}
            and type(value.get("shortening_applied")) is bool
        )

    @staticmethod
    def _valid_receipt(receipt: Any) -> bool:
        if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
                or receipt.get("schema_version") != 1):
            return False
        required = {
            "schema_version", "parent_post_id", "reply_post_id", "quote_id",
            "reply_text", "reply_epoch", "confirmed_at",
        }
        lifecycle = required | {"lifecycle_state", "started_at", "attempt_number"}
        with_metadata = lifecycle | {"formatter_metadata"}
        if set(receipt) not in (required, lifecycle, with_metadata):
            return False
        if "lifecycle_state" in receipt and receipt.get("lifecycle_state") != "confirmed":
            return False
        if "started_at" in receipt and (not isinstance(receipt["started_at"], str) or not receipt["started_at"].strip()):
            return False
        if "attempt_number" in receipt and (
            type(receipt["attempt_number"]) is not int or receipt["attempt_number"] < 1
        ):
            return False
        if "formatter_metadata" in receipt and not HistoricalContextReplyStore._valid_formatter_metadata(receipt["formatter_metadata"]):
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
        allowed = required | {"formatter_metadata"}
        return bool(
            isinstance(receipt, dict)
            and set(receipt) in (required, allowed)
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
            and ("formatter_metadata" not in receipt or HistoricalContextReplyStore._valid_formatter_metadata(receipt["formatter_metadata"]))
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

    def record_failure(self, parent_post_id: str, quote_id: str, text: str, error: BaseException,
                       formatter_metadata: dict[str, Any] | None = None) -> None:
        history = self.history(); previous = history["items"].get(str(parent_post_id), {})
        history["items"][str(parent_post_id)] = {"parent_post_id": str(parent_post_id), "quote_id": quote_id,
            "reply_text": text, "status": "failed", "failure": f"{type(error).__name__}: {error}",
            "attempt_count": int(previous.get("attempt_count", 0)) + 1, "updated_at": utc_now(),
            **({"formatter_metadata": formatter_metadata} if formatter_metadata else {})}
        self._save_history(history)

    def post(self, *, parent_post_id: str, quote_id: str, reply_text: str,
             create_post: Callable[..., dict[str, Any]], now_epoch: Callable[[], int], dry_run: bool = False,
             formatter_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if (
            not re.fullmatch(r"\d{1,30}", str(parent_post_id or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(quote_id or ""))
            or not isinstance(reply_text, str)
            or not reply_text.strip()
        ):
            raise ValueError("invalid historical context reply request")
        if formatter_metadata is not None and not self._valid_formatter_metadata(formatter_metadata):
            raise ValueError("invalid historical context formatter metadata")
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
            **({"formatter_metadata": formatter_metadata} if formatter_metadata else {}),
        }
        atomic_write_json(self.receipt_path, sending)

        def finish_confirmed_failure(error: Exception) -> dict[str, str]:
            try:
                self.record_failure(parent_post_id, quote_id, reply_text, error, formatter_metadata)
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
    result = format_context_reply_v2(packet, maximum_length=args.maximum_length)
    if result is None: raise SystemExit("canonical packet cannot produce a supported reply")
    print(result["text"])
    print(f"\nCharacter count: raw={result['raw_character_count']} x_weighted={result['character_count']}/{result['maximum_length']}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
