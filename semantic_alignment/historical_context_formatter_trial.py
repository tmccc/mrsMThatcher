"""Offline comparison of the production historical-context formatter and v2 candidate."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from historical_context_formatter import (
    UNKNOWN_VALUES,
    VERIFICATION_LABELS,
    classify_source,
    format_context_reply,
    load_and_validate_corpus,
    select_primary_source,
    x_weighted_length,
)

FORMATTER_V1 = "historical_context_reply_schema_v1"
FORMATTER_V2 = "historical_context_reply_schema_v2_candidate"
TRIAL_SCHEMA_VERSION = 1
SAMPLE_SEED = "historical-context-formatter-trial-001"
UNCERTAIN_STATUSES = {"paraphrase", "composite", "misattributed", "unverified"}
DECISIONS = {"a": "A is better", "b": "B is better", "equal": "roughly equal", "neither": "neither is acceptable"}
REASON_TAGS = (
    "clearer", "more concise", "better historical context", "better explanation of meaning",
    "Meaning section unnecessary", "Meaning section missing", "better provenance presentation",
    "source easier to understand", "more natural prose", "less repetitive", "too terse",
    "lost historical nuance", "factually misleading", "verification unclear", "source unclear",
    "formatting awkward on X", "other",
)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "hers", "him", "his", "i", "if", "in", "into", "is",
    "it", "its", "not", "of", "on", "or", "our", "she", "that", "the", "their", "them", "there",
    "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your", "must", "should", "can", "could", "may", "than", "then",
}
MONTHS = {
    "january": "January", "february": "February", "march": "March", "april": "April",
    "may": "May", "june": "June", "july": "July", "august": "August",
    "september": "September", "october": "October", "november": "November", "december": "December",
}
TOPIC_RULES = (
    ("Europe", ("europe", "eec", "eu ", "brussels")),
    ("foreign affairs", ("soviet", "russia", "war", "defence", "foreign", "nato", "argentina", "falkland")),
    ("economy", ("econom", "inflation", "tax", "money", "market", "trade", "business", "industry")),
    ("socialism", ("socialis", "marx", "communis", "collectiv")),
    ("liberty", ("liberty", "freedom", "individual", "responsib", "choice")),
    ("nation", ("britain", "british", "nation", "country", "patriot")),
    ("leadership", ("leader", "leadership", "government", "minister", "party")),
    ("society", ("family", "society", "moral", "religion", "church", "education")),
)


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 bytes."""
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    """Return the canonical hash."""
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_bytes(path: Path, value: bytes) -> None:
    """Write bytes atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    """Write a JSON document atomically."""
    atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _clean(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return "" if text.lower() in UNKNOWN_VALUES else text


def _sentence(value: Any) -> str:
    text = _clean(value).strip(" ")
    return text if not text or text.endswith((".", "?", "!")) else text + "."


def _one_sentence(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return _sentence(match.group(1) if match else text)


def format_british_date(value: Any) -> str:
    """Format british date."""
    text = _clean(value)
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
        names = tuple(MONTHS.values())
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{int(day)} {names[int(month)-1]} {year}"
    american = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if american and american.group(1).lower() in MONTHS:
        return f"{int(american.group(2))} {MONTHS[american.group(1).lower()]} {american.group(3)}"
    british = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if british and british.group(2).lower() in MONTHS:
        return f"{int(british.group(1))} {MONTHS[british.group(2).lower()]} {british.group(3)}"
    return text if re.fullmatch(r"\d{4}", text) else _british_dates_in_text(text)


def _british_dates_in_text(value: str) -> str:
    pattern = re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),\s*(\d{4})\b",
        re.I,
    )
    return pattern.sub(lambda match: f"{int(match.group(2))} {MONTHS[match.group(1).lower()]} {match.group(3)}", value)


def _tokens(value: Any) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", _clean(value).lower()) if len(word) > 2 and word not in STOPWORDS}


def _overlap(left: Any, right: Any) -> dict[str, float]:
    a, b = _tokens(left), _tokens(right)
    intersection = len(a & b)
    return {
        "jaccard": round(intersection / len(a | b), 4) if a | b else 0.0,
        "left_coverage": round(intersection / len(a), 4) if a else 0.0,
        "right_coverage": round(intersection / len(b), 4) if b else 0.0,
    }


def _distinct_count(value: Any, *against: Any) -> int:
    used: set[str] = set()
    for item in against:
        used |= _tokens(item)
    return len(_tokens(value) - used)


def meaning_decision(packet: dict[str, Any], context: str) -> dict[str, Any]:
    """Return the meaning decision."""
    meaning = _one_sentence(packet.get("intended_argument")) or _one_sentence(packet.get("literal_meaning"))
    quote = packet["quote_text"]
    mechanism = _clean(packet.get("mechanism"))
    consequence = _clean(packet.get("claimed_consequence"))
    quote_overlap = _overlap(meaning, quote)
    context_overlap = _overlap(meaning, context)
    mechanism_distinct = _distinct_count(mechanism, quote, context, meaning)
    consequence_distinct = _distinct_count(consequence, quote, context, meaning)
    obscure_reference = bool(re.search(
        r"\b(?:this|that|these|those|here|there|he|she|they|them|it|such|former|latter)\b",
        quote.lower(),
    ))
    counter_intuitive = bool(re.search(
        r"\b(?:paradox|contrary|although|despite|not .* but|rather than|unless|only if)\b",
        " ".join((quote, meaning, mechanism)).lower(),
    ))
    uncertain = packet.get("verification_status") in UNCERTAIN_STATUSES
    explicit_ambiguity = bool(packet.get("unresolved_questions")) or bool(_clean(packet.get("text_variation_notes")))
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
    return {"meaning": meaning, "meaning_included": included, "meaning_decision_reason": reason, "rule_outputs": rule_outputs}


def _context_sentence(packet: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    event = _clean(packet.get("source_event"))
    uncertain_event = re.fullmatch(r"unknown\s*\((.+)\)", event, re.I)
    if uncertain_event:
        qualifier = uncertain_event.group(1).strip()
        event = "" if qualifier.lower() == "attributed" else qualifier[0].upper() + qualifier[1:]
    elif re.match(r"unknown\s+", event, re.I):
        remainder = re.sub(r"^unknown\s+", "", event, flags=re.I).strip()
        event = f"Attributed to a {remainder}" if remainder else ""
    event = _british_dates_in_text(event)
    date = format_british_date(packet.get("date"))
    immediate = _one_sentence(packet.get("immediate_subject")) or _one_sentence(packet.get("historical_context"))
    immediate = _british_dates_in_text(immediate.rstrip("."))
    event = event.rstrip(". :;-")
    if event and immediate and date:
        text = f"{event}, {date}: {immediate}."
    elif event and immediate:
        text = f"{event}: {immediate}."
    elif immediate and date:
        text = f"{date}: {immediate}."
    elif immediate:
        text = _sentence(immediate)
    elif event and date:
        text = f"{event}, {date}."
    elif event:
        text = _sentence(event)
    elif date:
        text = f"The surviving record dates this wording to {date}, but does not establish its occasion."
    else:
        text = "The surviving attribution does not establish an occasion, date or immediate historical issue."
    return text, {"source_event": packet.get("source_event"), "date": packet.get("date"),
                  "normalised_date": date, "immediate_subject": packet.get("immediate_subject")}


def _render_v2_text(context: str, meaning: str, verification: str, source: dict[str, Any]) -> str:
    sections = [f"Context — {context}"]
    if meaning:
        sections.append(f"Meaning — {meaning}")
    sections.append(f"Verification — {verification}")
    source_line = f"Source — {source['title']}"
    if source.get("url"):
        source_line += f"\n{source['url']}"
    sections.append(source_line)
    return "\n\n".join(sections)


def _forbidden_candidate_text(text: str) -> list[str]:
    """Return the forbidden candidate text."""
    findings = []
    if re.search(r"(?:^|\s)#[A-Za-z0-9_]", text): findings.append("hashtag")
    if re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF]", text): findings.append("emoji")
    if "vertexaisearch.cloud.google.com/grounding-api-redirect/" in text: findings.append("opaque_redirect")
    if re.search(r"(?:^|[—:\s])(N/A|None|unknown|unavailable)(?:$|[.\s])", text, re.I): findings.append("placeholder")
    if re.search(r"\b(?:did you know|interesting fact|click here|learn more)\b", text, re.I): findings.append("promotional_wording")
    return findings


def format_context_reply_v2(packet: dict[str, Any]) -> dict[str, Any]:
    """Format a compact schema-v2 historical context reply."""
    source = select_primary_source(packet)
    if not source or not _clean(source.get("title")):
        raise ValueError(f"packet lacks a defensible source or canonical locator: {packet.get('quote_id')}")
    context, context_provenance = _context_sentence(packet)
    decision = meaning_decision(packet, context)
    verification = VERIFICATION_LABELS[packet["verification_status"]]
    with_meaning = _render_v2_text(context, decision["meaning"], verification, source)
    without_meaning = _render_v2_text(context, "", verification, source)
    final = with_meaning if decision["meaning_included"] else without_meaning
    findings = _forbidden_candidate_text(final)
    if findings:
        raise ValueError(f"candidate contains forbidden content for {packet['quote_id']}: {', '.join(findings)}")
    if packet["verification_status"] in UNCERTAIN_STATUSES:
        variant = "compact_uncertain_wording"
    elif not decision["meaning_included"]:
        variant = "compact_without_redundant_meaning"
    elif not source.get("url"):
        variant = "compact_no_public_url"
    else:
        variant = "compact_with_meaning"
    # Preserve the complete source title and provenance even when this exceeds the editorial target.
    weighted = x_weighted_length(final)
    return {
        "quote_id": packet["quote_id"], "quote_text": packet["quote_text"], "text": final,
        "formatter_version": FORMATTER_V2, "template_variant": variant,
        "raw_character_count": len(final), "weighted_character_count": weighted,
        "character_count": weighted, "meaning_included": decision["meaning_included"],
        "meaning_decision_reason": decision["meaning_decision_reason"],
        "meaning_rule_outputs": decision["rule_outputs"], "candidate_text_with_meaning": with_meaning,
        "candidate_text_without_meaning": without_meaning, "verification_status": packet["verification_status"],
        "verification_label": verification, "source": source, "source_class": classify_source(source),
        "historical_confidence": packet.get("research_confidence") or "unavailable",
        "shortening_applied": False,
        "field_provenance": {
            "context": context_provenance,
            "meaning": {"selected_field": "intended_argument" if _clean(packet.get("intended_argument")) else "literal_meaning",
                        "intended_argument": packet.get("intended_argument"), "literal_meaning": packet.get("literal_meaning"),
                        "mechanism": packet.get("mechanism"), "claimed_consequence": packet.get("claimed_consequence")},
            "verification": {"verification_status": packet["verification_status"]},
            "source": {"stable_locator": packet.get("stable_locator"), "selected_source": source},
        },
        "output_sha256": sha256_bytes(final.encode()),
    }


def _v1_record(packet: dict[str, Any]) -> dict[str, Any]:
    rendered = format_context_reply(packet)
    if not rendered:
        raise ValueError(f"v1 could not render {packet['quote_id']}")
    return {**rendered, "formatter_version": FORMATTER_V1, "weighted_character_count": rendered["character_count"],
            "verification_status": packet["verification_status"], "source_event": packet["source_event"],
            "date": packet["date"], "output_sha256": sha256_bytes(rendered["text"].encode())}


def _topic(packet: dict[str, Any]) -> str:
    text = " ".join(_clean(packet.get(field)) for field in (
        "quote_text", "historical_context", "immediate_subject", "intended_argument", "broader_principle"
    )).lower()
    for name, markers in TOPIC_RULES:
        if any(marker in text for marker in markers):
            return name
    return "general political principle"


def _event_type(packet: dict[str, Any]) -> str:
    event = _clean(packet.get("source_event")).lower()
    for name, markers in (("speech", ("speech", "address", "remarks")), ("interview", ("interview", "broadcast")),
                          ("debate", ("debate", "hansard", "commons", "parliament")),
                          ("book", ("book", "memoir", "statecraft", "path to power", "downing street years")),
                          ("article", ("article", "foreword", "newspaper", "press"))):
        if any(marker in event for marker in markers): return name
    return "other"


def _length_band(value: int) -> str:
    if value <= 300: return "up_to_300"
    if value <= 450: return "301_to_450"
    if value <= 600: return "451_to_600"
    if value <= 1000: return "601_to_1000"
    return "over_1000"


def _reduction_band(v1: int, v2: int) -> str:
    reduction = (v1 - v2) / v1 if v1 else 0
    if reduction < 0.05: return "little_or_none"
    if reduction < 0.20: return "5_to_19_percent"
    if reduction < 0.40: return "20_to_39_percent"
    return "40_percent_or_more"


def _overlap_band(value: float) -> str:
    if value >= 0.30: return "high"
    if value >= 0.15: return "medium"
    return "low"


def render_complete_corpus(research_run: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Render complete corpus."""
    packets, unresolved = load_and_validate_corpus(
        research_run,
        load_source_role_audit=False,
    )
    rows: dict[str, dict[str, Any]] = {}
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        source = select_primary_source(packet)
        if source is None or not _clean(source.get("title")):
            raise RuntimeError(f"no defensible source or canonical locator: {quote_id}")
        v1, v2 = _v1_record(packet), format_context_reply_v2(packet)
        context_word_count = len(re.findall(r"\b\w+\b", " ".join(_clean(packet.get(field)) for field in (
            "source_event", "immediate_subject", "historical_context", "intended_argument", "mechanism"
        ))))
        overlap_value = max(v2["meaning_rule_outputs"]["meaning_quote_overlap"]["left_coverage"],
                            v2["meaning_rule_outputs"]["meaning_context_overlap"]["left_coverage"])
        rows[quote_id] = {
            "quote_id": quote_id, "quote_text": packet["quote_text"], "topic": _topic(packet),
            "event_type": _event_type(packet), "verification_status": packet["verification_status"],
            "verification_label": VERIFICATION_LABELS[packet["verification_status"]],
            "source_class": v1["source_class"], "source_title": v1["source"]["title"],
            "source_locator": v1["source"].get("url") or v1["source"]["title"],
            "has_public_url": bool(v1["source"].get("url")),
            "historical_confidence": packet.get("research_confidence") or "unavailable",
            "source_event": packet["source_event"], "date": packet["date"],
            "context_complexity": "complex" if context_word_count >= 90 else "simple",
            "context_evidence_word_count": context_word_count,
            "lexical_overlap_band": _overlap_band(overlap_value), "lexical_overlap_value": overlap_value,
            "ambiguous_event_description": bool(re.search(r"\b(?:unknown|alleged|attributed|possibly|uncertain)\b", packet["source_event"], re.I)),
            "difficult_source_title": len(v1["source"]["title"]) >= 100,
            "v1": v1, "v2": v2,
            "v1_raw_count": v1["raw_character_count"], "v1_weighted_count": v1["weighted_character_count"],
            "v2_raw_count": v2["raw_character_count"], "v2_weighted_count": v2["weighted_character_count"],
            "v2_template_variant": v2["template_variant"], "meaning_included": v2["meaning_included"],
            "meaning_decision_reason": v2["meaning_decision_reason"],
            "v1_sha256": v1["output_sha256"], "v2_sha256": v2["output_sha256"],
        }
    return rows, unresolved


def provenance_parity(rows: dict[str, dict[str, Any]], packets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return the provenance parity."""
    comparisons = []
    invariant_fields = ("quote_id", "verification_status", "verification_label", "source_title", "source_locator",
                        "historical_confidence", "date", "source_event")
    for quote_id, row in rows.items():
        packet = packets[quote_id]
        v1_source, v2_source = row["v1"]["source"], row["v2"]["source"]
        values = {
            "quote_id": (row["quote_id"], row["v2"]["quote_id"]),
            "verification_status": (packet["verification_status"], row["v2"]["verification_status"]),
            "verification_label": (row["v1"]["verification_label"], row["v2"]["verification_label"]),
            "source_title": (v1_source["title"], v2_source["title"]),
            "source_locator": (v1_source.get("url") or v1_source["title"], v2_source.get("url") or v2_source["title"]),
            "historical_confidence": (row["v1"]["historical_confidence"], row["v2"]["historical_confidence"]),
            "date": (packet["date"], row["v2"]["field_provenance"]["context"]["date"]),
            "source_event": (packet["source_event"], row["v2"]["field_provenance"]["context"]["source_event"]),
        }
        mismatches = [field for field in invariant_fields if values[field][0] != values[field][1]]
        comparisons.append({"quote_id": quote_id, "match": not mismatches, "mismatches": mismatches,
                            "values": {field: {"v1_or_packet": values[field][0], "v2": values[field][1]} for field in invariant_fields}})
    failures = [row for row in comparisons if not row["match"]]
    return {"schema_version": 1, "records": len(comparisons), "blocking_regression_count": len(failures),
            "passed": not failures, "invariant_fields": list(invariant_fields), "items": comparisons}


def length_analysis(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return the length analysis."""
    v1 = [row["v1_weighted_count"] for row in rows.values()]
    v2 = [row["v2_weighted_count"] for row in rows.values()]
    reductions = [(a - b) / a * 100 for a, b in zip(v1, v2) if a]
    def stats(values: list[int]) -> dict[str, Any]:
        return {"average": round(statistics.mean(values), 2), "median": round(statistics.median(values), 2),
                "minimum": min(values), "maximum": max(values),
                "bands": dict(sorted(Counter(_length_band(x) for x in values).items())),
                "over_450": sum(x > 450 for x in values), "over_600": sum(x > 600 for x in values),
                "over_1000": sum(x > 1000 for x in values)}
    return {"schema_version": 1, "records": len(rows), "v1_weighted": stats(v1), "v2_weighted": stats(v2),
            "v1_raw": stats([row["v1_raw_count"] for row in rows.values()]),
            "v2_raw": stats([row["v2_raw_count"] for row in rows.values()]),
            "mean_percentage_reduction": round(statistics.mean(reductions), 2),
            "median_percentage_reduction": round(statistics.median(reductions), 2),
            "meaning_omitted_count": sum(not row["meaning_included"] for row in rows.values()),
            "meaning_included_count": sum(row["meaning_included"] for row in rows.values()),
            "reduction_bands": dict(sorted(Counter(_reduction_band(row["v1_weighted_count"], row["v2_weighted_count"])
                                                        for row in rows.values()).items()))}


def _near_duplicate(left: str, right: str) -> bool:
    """Return the near duplicate."""
    a, b = _tokens(left), _tokens(right)
    return bool(a and b and len(a & b) / len(a | b) >= 0.86)


def select_review_sample(rows: dict[str, dict[str, Any]], count: int = 50) -> list[dict[str, Any]]:
    """Select review sample."""
    if count != 50:
        raise ValueError("this trial requires exactly 50 review records")
    ordered = sorted(rows.values(), key=lambda row: row["quote_id"])
    selected: list[dict[str, Any]] = []
    rationales: dict[str, set[str]] = {}
    def add(row: dict[str, Any], reason: str) -> bool:
        if row in selected:
            rationales[row["quote_id"]].add(reason); return True
        if any(_near_duplicate(row["quote_text"], item["quote_text"]) for item in selected):
            return False
        selected.append(row); rationales[row["quote_id"]] = {reason}; return True
    longest = sorted(ordered, key=lambda row: (-row["v1_weighted_count"], row["quote_id"]))[:10]
    for row in longest:
        if not add(row, "one of the ten longest v1 replies"):
            raise RuntimeError("the ten longest v1 replies contain a duplicate quote family")
    # Force scarce and editorially important strata before balanced greedy filling.
    requirements = [
        (lambda r: r["has_public_url"], "public source URL"),
        (lambda r: not r["has_public_url"], "canonical locator without public URL"),
        (lambda r: not r["meaning_included"], "Meaning judged redundant by v2 rule"),
        (lambda r: r["meaning_included"], "Meaning retained as historically useful"),
        (lambda r: r["verification_status"] in UNCERTAIN_STATUSES, "non-exact or uncertain wording"),
        (lambda r: _reduction_band(r["v1_weighted_count"], r["v2_weighted_count"]) == "little_or_none", "little or no v2 length reduction"),
        (lambda r: _reduction_band(r["v1_weighted_count"], r["v2_weighted_count"]) == "40_percent_or_more", "substantial v2 length reduction"),
        (lambda r: r["lexical_overlap_band"] == "high", "high lexical overlap between Meaning and existing text"),
        (lambda r: r["context_complexity"] == "complex", "complex canonical context"),
        (lambda r: r["context_complexity"] == "simple", "simple canonical context"),
        (lambda r: r["ambiguous_event_description"], "ambiguous event description"),
        (lambda r: r["difficult_source_title"], "difficult or long source title"),
    ]
    for predicate, reason in requirements:
        candidates = [row for row in ordered if predicate(row)]
        candidates.sort(key=lambda row: sha256_bytes(f"{SAMPLE_SEED}:{reason}:{row['quote_id']}".encode()))
        if candidates and not any(predicate(row) for row in selected):
            if not any(add(row, reason) for row in candidates):
                raise RuntimeError(f"could not satisfy sample stratum: {reason}")
    feature_counts: Counter[tuple[str, str]] = Counter()
    def features(row: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            ("verification", row["verification_status"]), ("source", row["source_class"]),
            ("confidence", row["historical_confidence"]), ("topic", row["topic"]),
            ("event", row["event_type"]), ("v1_length", _length_band(row["v1_weighted_count"])),
            ("v2_length", _length_band(row["v2_weighted_count"])),
            ("meaning", "included" if row["meaning_included"] else "omitted"),
            ("public_url", str(row["has_public_url"])),
            ("reduction", _reduction_band(row["v1_weighted_count"], row["v2_weighted_count"])),
            ("overlap", row["lexical_overlap_band"]), ("complexity", row["context_complexity"]),
            ("ambiguous_event", str(row["ambiguous_event_description"])),
            ("difficult_source_title", str(row["difficult_source_title"])),
        ]
    for row in selected: feature_counts.update(features(row))
    all_counts = Counter(feature for row in ordered for feature in features(row))
    while len(selected) < count:
        candidates = [row for row in ordered if row not in selected and not any(
            _near_duplicate(row["quote_text"], item["quote_text"]) for item in selected)]
        if not candidates: raise RuntimeError("not enough non-duplicate records for review sample")
        def score(row: dict[str, Any]) -> tuple[float, str]:
            rarity = sum(1 / max(1, all_counts[feature]) for feature in features(row))
            coverage = sum(1 / (1 + feature_counts[feature]) for feature in features(row))
            tie = sha256_bytes(f"{SAMPLE_SEED}:{row['quote_id']}".encode())
            return (coverage + rarity, tie)
        row = max(candidates, key=score)
        add(row, "deterministic stratification across verification, source, confidence, topic, event, length and Meaning")
        feature_counts.update(features(row))
    selected.sort(key=lambda row: sha256_bytes(f"{SAMPLE_SEED}:review-order:{row['quote_id']}".encode()))
    result = []
    for order, row in enumerate(selected, 1):
        result.append({"review_order": order, "quote_id": row["quote_id"], "quote_text": row["quote_text"],
                       "selection_rationale": sorted(rationales[row["quote_id"]]),
                       "strata": {name: value for name, value in features(row)}})
    return result


def build_blind_assignments(sample: list[dict[str, Any]]) -> dict[str, Any]:
    """Build blind assignments."""
    items = {}
    for row in sample:
        digest = sha256_bytes(f"{SAMPLE_SEED}:blind:{row['quote_id']}".encode())
        items[row["quote_id"]] = {"quote_id": row["quote_id"], "a": "v1" if int(digest[:2], 16) % 2 == 0 else "v2",
                                  "b": "v2" if int(digest[:2], 16) % 2 == 0 else "v1", "assignment_hash": digest}
    return {"schema_version": 1, "seed_hash": sha256_bytes(SAMPLE_SEED.encode()), "items": items}


def blind_presentations(row: dict[str, Any], assignment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return browser-safe A/B data without formatter identity metadata."""
    return {letter: {"text": row[assignment[letter]]["text"],
                     "raw_character_count": row[assignment[letter]]["raw_character_count"],
                     "weighted_character_count": row[assignment[letter]]["weighted_character_count"]}
            for letter in ("a", "b")}


def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[key]) for row in rows).items()))


def prepare_trial(research_run: Path, output: Path, sample_count: int = 50) -> dict[str, Any]:
    """Prepare trial."""
    rows, unresolved = render_complete_corpus(research_run)
    packets, _ = load_and_validate_corpus(
        research_run,
        load_source_role_audit=False,
    )
    parity = provenance_parity(rows, packets)
    if not parity["passed"]:
        raise RuntimeError(f"blocking provenance regressions: {parity['blocking_regression_count']}")
    lengths = length_analysis(rows)
    sample = select_review_sample(rows, sample_count)
    blind = build_blind_assignments(sample)
    existing_reviews = read_json(output / "human_reviews.json", {"items": {}}).get("items", {}) if output.exists() else {}
    existing_sample = read_json(output / "review_sample_50.json", {"items": []}).get("items", []) if output.exists() else []
    if existing_reviews and [row.get("quote_id") for row in existing_sample] != [row["quote_id"] for row in sample]:
        raise RuntimeError("refusing to replace a reviewed trial with a different sample or order")
    output.mkdir(parents=True, exist_ok=True); (output / "review").mkdir(exist_ok=True)
    generated = utc_now()
    eligible_ids = sorted(rows)
    formatter_path = Path("historical_context_formatter.py")
    trial_module = Path(__file__)
    corpus_hashes = {
        "schema_version": 1, "generated_at": generated,
        "canonical_corpus_hash": canonical_hash({qid: packets[qid] for qid in eligible_ids}),
        "eligible_quote_id_set_hash": canonical_hash(eligible_ids),
        "eligible_count": len(eligible_ids), "unresolved_count": len(unresolved),
        "research_packet_schema_version": "quote_research_packet_v1",
        "formatter_source_hashes": {FORMATTER_V1: sha256_bytes(formatter_path.read_bytes()),
                                    FORMATTER_V2: sha256_bytes(trial_module.read_bytes())},
    }
    manifest = {"schema_version": TRIAL_SCHEMA_VERSION, "generated_at": generated,
                "research_run": str(research_run), "eligible_count": len(rows), "unresolved_count": len(unresolved),
                "unresolved_quote_ids": sorted(unresolved), "sample_count": len(sample),
                "formatter_versions": [FORMATTER_V1, FORMATTER_V2],
                "corpus_hash": corpus_hashes["canonical_corpus_hash"],
                "eligible_quote_id_set_hash": corpus_hashes["eligible_quote_id_set_hash"]}
    v1 = {qid: row["v1"] for qid, row in rows.items()}
    v2 = {qid: row["v2"] for qid, row in rows.items()}
    atomic_write_json(output / "trial_manifest.json", manifest)
    atomic_write_json(output / "corpus_hashes.json", corpus_hashes)
    atomic_write_json(output / "formatter_v1_snapshot.json", {"schema_version": 1, "formatter_version": FORMATTER_V1, "items": v1})
    atomic_write_json(output / "formatter_v2_candidate.json", {"schema_version": 1, "formatter_version": FORMATTER_V2, "items": v2})
    atomic_write_json(output / "complete_pairwise_renderings.json", {"schema_version": 1, "items": rows})
    atomic_write_bytes(output / "complete_pairwise_renderings.jsonl", ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows.values()) + "\n").encode())
    atomic_write_json(output / "provenance_parity_audit.json", parity)
    atomic_write_json(output / "length_analysis.json", lengths)
    atomic_write_json(output / "review_sample_50.json", {"schema_version": 1, "items": sample})
    atomic_write_json(output / "blind_assignment_manifest.json", blind)
    if not (output / "human_reviews.json").exists():
        atomic_write_json(output / "human_reviews.json", {"schema_version": 1, "items": {}})
    if not (output / "human_review_audit.jsonl").exists(): atomic_write_bytes(output / "human_review_audit.jsonl", b"")
    atomic_write_bytes(output / "review" / "README.md", (
        "# Blind review interface\n\nRun from the repository root:\n\n"
        "```bash\npython3 compare_historical_context_formatters.py serve \\\n"
        f"  --trial-dir {output} \\\n  --host 127.0.0.1 \\\n  --port 8766\n```\n\n"
        "Open <http://127.0.0.1:8766>. The normal review page exposes only A/B labels.\n"
    ).encode())
    write_preparation_reports(output, rows, sample, parity, lengths)
    generate_review_results(output)
    return {"manifest": manifest, "parity": parity, "lengths": lengths, "sample": sample}


def write_preparation_reports(output: Path, rows: dict[str, dict[str, Any]], sample: list[dict[str, Any]],
                              parity: dict[str, Any], lengths: dict[str, Any]) -> None:
    """Write preparation reports."""
    parity_lines = ["# Provenance Parity Audit", "", f"Records: **{parity['records']}**.",
                    f"Blocking regressions: **{parity['blocking_regression_count']}**.",
                    f"Result: **{'PASS' if parity['passed'] else 'FAIL'}**.", "",
                    "Compared invariants: " + ", ".join(parity["invariant_fields"]) + "."]
    atomic_write_bytes(output / "provenance_parity_report.md", ("\n".join(parity_lines) + "\n").encode())
    v1, v2 = lengths["v1_weighted"], lengths["v2_weighted"]
    length_lines = ["# Formatter Length Analysis", "", f"Corpus: **{lengths['records']}** replies.", "",
                    "| Formatter | Average weighted | Median | Minimum | Maximum | >450 | >600 | >1,000 |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|",
                    f"| v1 | {v1['average']:.2f} | {v1['median']:.2f} | {v1['minimum']} | {v1['maximum']} | {v1['over_450']} | {v1['over_600']} | {v1['over_1000']} |",
                    f"| v2 candidate | {v2['average']:.2f} | {v2['median']:.2f} | {v2['minimum']} | {v2['maximum']} | {v2['over_450']} | {v2['over_600']} | {v2['over_1000']} |",
                    "", f"Mean reduction: **{lengths['mean_percentage_reduction']:.2f}%**.",
                    f"Median reduction: **{lengths['median_percentage_reduction']:.2f}%**.",
                    f"Meaning omitted: **{lengths['meaning_omitted_count']}**; retained: **{lengths['meaning_included_count']}**."]
    atomic_write_bytes(output / "length_analysis.md", ("\n".join(length_lines) + "\n").encode())
    selected_rows = [rows[row["quote_id"]] for row in sample]
    selection = ["# Review Sample Selection", "", "The deterministic 50-record sample includes the ten longest v1 replies, then fills underrepresented provenance, wording, event, topic, length, URL and Meaning strata while rejecting near-duplicate quote families.", "",
                 "## Composition", "", f"- Verification: `{_distribution(selected_rows, 'verification_status')}`",
                 f"- Sources: `{_distribution(selected_rows, 'source_class')}`",
                 f"- Confidence: `{_distribution(selected_rows, 'historical_confidence')}`",
                 f"- Events: `{_distribution(selected_rows, 'event_type')}`",
                 f"- Topics: `{_distribution(selected_rows, 'topic')}`", "", "## Cases", ""]
    for item in sample:
        selection.append(f"{item['review_order']}. `{item['quote_id']}` — {'; '.join(item['selection_rationale'])}")
    atomic_write_bytes(output / "review_selection_report.md", ("\n".join(selection) + "\n").encode())
    report = ["# Formatter v2 Candidate", "", f"Version: `{FORMATTER_V2}`.", "",
              "The candidate is an offline-only compact archive entry. It combines source event, British-formatted date and immediate issue in one Context sentence; includes at most one Meaning sentence under a deterministic rule; and preserves verification and the selected v1 source exactly.", "",
              "Meaning is retained for uncertain wording, context-dependent references, contrastive claims, distinct mechanisms or consequences, and unresolved ambiguity. It is omitted only when substantive terms are already supplied by the quotation or Context and no retention rule applies.", "",
              f"Corpus renderings: **{len(rows)}**. Parity: **{'PASS' if parity['passed'] else 'FAIL'}**. Sample: **{len(sample)}**.",
              f"Weighted mean: v1 **{v1['average']:.2f}**, v2 **{v2['average']:.2f}**. Meaning omitted: **{lengths['meaning_omitted_count']}**.", "",
              "Production v1 remains unchanged; this version is reachable only through the offline trial CLI."]
    atomic_write_bytes(output / "formatter_v2_candidate_report.md", ("\n".join(report) + "\n").encode())


def strict_audit(trial_dir: Path) -> dict[str, Any]:
    """Return the strict audit."""
    manifest = read_json(trial_dir / "trial_manifest.json")
    rows = read_json(trial_dir / "complete_pairwise_renderings.json", {}).get("items", {})
    parity = read_json(trial_dir / "provenance_parity_audit.json")
    blind = read_json(trial_dir / "blind_assignment_manifest.json", {}).get("items", {})
    sample = read_json(trial_dir / "review_sample_50.json", {}).get("items", [])
    hashes = read_json(trial_dir / "corpus_hashes.json", {})
    errors = []
    if not manifest or manifest.get("eligible_count") != 627 or manifest.get("unresolved_count") != 5: errors.append("corpus count invariant")
    if len(rows) != 627: errors.append("pairwise rendering count")
    if not parity or not parity.get("passed"): errors.append("provenance parity")
    if len(sample) != 50 or len({row.get("quote_id") for row in sample}) != 50: errors.append("review sample")
    if set(blind) != {row.get("quote_id") for row in sample}: errors.append("blind assignment coverage")
    research_run = Path(manifest.get("research_run", "")) if manifest else Path()
    try:
        current_rows, current_unresolved = render_complete_corpus(research_run)
        current_packets, _ = load_and_validate_corpus(
            research_run,
            load_source_role_audit=False,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        errors.append(f"current corpus render failed:{exc}")
        current_rows, current_unresolved, current_packets = {}, set(), {}
    if current_rows:
        if len(current_unresolved) != 5: errors.append("current unresolved count")
        if canonical_hash({qid: current_packets[qid] for qid in sorted(current_packets)}) != hashes.get("canonical_corpus_hash"): errors.append("canonical corpus hash")
        if canonical_hash(sorted(current_packets)) != hashes.get("eligible_quote_id_set_hash"): errors.append("eligible ID set hash")
        if select_review_sample(current_rows, 50) != sample: errors.append("deterministic sample drift")
        if build_blind_assignments(sample) != read_json(trial_dir / "blind_assignment_manifest.json"): errors.append("blind assignment drift")
        if any(current_rows[qid]["v1_sha256"] != rows.get(qid, {}).get("v1_sha256") for qid in current_rows): errors.append("v1 rerender drift")
        if any(current_rows[qid]["v2_sha256"] != rows.get(qid, {}).get("v2_sha256") for qid in current_rows): errors.append("v2 rerender drift")
    # Source hashes identify the exact implementation used for the completed trial. After a
    # reviewed candidate is promoted, the production module necessarily changes; byte-for-byte
    # rerender checks above remain the blocking regression guard for the historical trial.
    for quote_id, row in rows.items():
        if row.get("quote_id") != quote_id or row["v2"].get("quote_id") != quote_id: errors.append(f"identity:{quote_id}")
        if sha256_bytes(row["v1"]["text"].encode()) != row["v1_sha256"]: errors.append(f"v1 hash:{quote_id}")
        if sha256_bytes(row["v2"]["text"].encode()) != row["v2_sha256"]: errors.append(f"v2 hash:{quote_id}")
        if _forbidden_candidate_text(row["v2"]["text"]): errors.append(f"forbidden text:{quote_id}")
    return {"passed": not errors, "errors": errors, "eligible_count": len(rows), "sample_count": len(sample)}


def _review_store(trial_dir: Path) -> dict[str, Any]:
    store = read_json(trial_dir / "human_reviews.json", {"schema_version": 1, "items": {}})
    if not isinstance(store, dict) or store.get("schema_version") != 1 or not isinstance(store.get("items"), dict):
        raise RuntimeError("invalid human review store")
    return store


def save_review(trial_dir: Path, quote_id: str, decision: str, reasons: list[str], note: str = "") -> tuple[dict[str, Any], bool]:
    """Save review."""
    sample_ids = {row["quote_id"] for row in read_json(trial_dir / "review_sample_50.json")["items"]}
    if quote_id not in sample_ids: raise KeyError(quote_id)
    if decision not in DECISIONS: raise ValueError("invalid decision")
    clean_reasons = sorted({reason for reason in reasons if reason in REASON_TAGS})
    clean_note = str(note or "").strip()[:2000]
    store = _review_store(trial_dir); previous = store["items"].get(quote_id)
    comparable = {"decision": decision, "reason_tags": clean_reasons, "note": clean_note}
    if previous and all(previous.get(key) == value for key, value in comparable.items()): return previous, False
    revision = int((previous or {}).get("revision", 0)) + 1; timestamp = utc_now()
    record = {"quote_id": quote_id, **comparable, "decision_label": DECISIONS[decision],
              "reviewed_at": timestamp, "revision": revision}
    backup = trial_dir / "review" / "human_reviews.backup.json"
    source = trial_dir / "human_reviews.json"
    if source.exists(): atomic_write_bytes(backup, source.read_bytes())
    store["items"][quote_id] = record; atomic_write_json(source, store)
    _append_jsonl(trial_dir / "human_review_audit.jsonl", {"quote_id": quote_id, "timestamp": timestamp,
                  "revision": revision, "previous_value": previous, "new_value": record})
    generate_review_results(trial_dir)
    return record, True


def generate_review_results(trial_dir: Path) -> dict[str, Any]:
    """Generate review results."""
    rows = read_json(trial_dir / "complete_pairwise_renderings.json", {"items": {}})["items"]
    sample = read_json(trial_dir / "review_sample_50.json", {"items": []})["items"]
    blind = read_json(trial_dir / "blind_assignment_manifest.json", {"items": {}})["items"]
    reviews = _review_store(trial_dir)["items"] if (trial_dir / "human_reviews.json").exists() else {}
    output = []; wins = Counter(); reasons = Counter(); factual = []
    for item in sample:
        qid = item["quote_id"]; review = reviews.get(qid)
        if not review: continue
        assignment = blind[qid]; decision = review["decision"]
        winner = assignment[decision] if decision in {"a", "b"} else decision
        wins[winner] += 1; reasons.update(review["reason_tags"])
        if any(reason in {"factually misleading", "lost historical nuance", "verification unclear", "source unclear"} for reason in review["reason_tags"]): factual.append(qid)
        output.append({"quote_id": qid, "decision": decision, "winner": winner,
                       "reason_tags": review["reason_tags"], "note": review["note"], "revision": review["revision"],
                       "verification_label": rows[qid]["verification_label"], "source_class": rows[qid]["source_class"],
                       "meaning_included": rows[qid]["meaning_included"],
                       "length_reduction_band": _reduction_band(rows[qid]["v1_weighted_count"], rows[qid]["v2_weighted_count"]),
                       "a_formatter": assignment["a"], "b_formatter": assignment["b"]})
    decisive = wins["v1"] + wins["v2"]
    acceptable_v1 = sum(row["winner"] in {"v1", "equal"} for row in output)
    acceptable_v2 = sum(row["winner"] in {"v2", "equal"} for row in output)
    def grouped(field: str) -> dict[str, Any]:
        groups = {}
        for value in sorted({str(row[field]) for row in output}):
            group_rows = [row for row in output if str(row[field]) == value]; group_wins = Counter(row["winner"] for row in group_rows)
            group_decisive = group_wins["v1"] + group_wins["v2"]
            groups[value] = {"records": len(group_rows), "v1_wins": group_wins["v1"], "v2_wins": group_wins["v2"],
                             "equal": group_wins["equal"], "neither": group_wins["neither"],
                             "decisive_v2_win_rate": round(group_wins["v2"] / group_decisive, 4) if group_decisive else None}
        return groups
    results = {"schema_version": 1, "sample_count": len(sample), "reviewed_count": len(output),
               "unreviewed_count": len(sample) - len(output), "v1_wins": wins["v1"], "v2_wins": wins["v2"],
               "equal": wins["equal"], "neither": wins["neither"],
               "decisive_v2_win_rate": round(wins["v2"] / decisive, 4) if decisive else None,
               "v1_acceptable_rate": round(acceptable_v1 / len(output), 4) if output else None,
               "v2_acceptable_rate": round(acceptable_v2 / len(output), 4) if output else None,
               "reason_tag_counts": dict(sorted(reasons.items())), "factual_or_provenance_objection_quote_ids": factual,
               "results_by_verification_label": grouped("verification_label"),
               "results_by_source_class": grouped("source_class"),
               "results_by_meaning_included": grouped("meaning_included"),
               "results_by_length_reduction_band": grouped("length_reduction_band"),
               "consistency_checks": {"unique_reviewed_quote_ids": len({row["quote_id"] for row in output}) == len(output),
                                      "all_revisions_positive": all(row["revision"] >= 1 for row in output)},
               "items": output}
    atomic_write_json(trial_dir / "review_results.json", results)
    csv_path = trial_dir / "review_results.csv"; csv_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{csv_path.name}.", dir=csv_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("quote_id", "decision", "winner", "reason_tags", "note", "verification_label", "source_class", "meaning_included", "length_reduction_band"),
                lineterminator="\n",
            )
            writer.writeheader()
            for row in output: writer.writerow({key: "; ".join(row[key]) if key == "reason_tags" else row[key] for key in writer.fieldnames})
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, csv_path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise
    status = "Review incomplete; no promotion recommendation is available." if len(output) < len(sample) else (
        "Recommend considering v2 for a separate deployment review." if results["decisive_v2_win_rate"] is not None
        and results["decisive_v2_win_rate"] >= 0.60 and not factual and wins["neither"] <= 2
        else "Results do not meet the evidence threshold; revise v2 or run a second bounded review.")
    report = ["# Historical Context Formatter Blind Review", "", f"Reviewed: **{len(output)}/{len(sample)}**.", "",
              f"- v1 wins: {wins['v1']}", f"- v2 wins: {wins['v2']}", f"- roughly equal: {wins['equal']}",
              f"- neither acceptable: {wins['neither']}", f"- decisive v2 win rate: {results['decisive_v2_win_rate'] if decisive else 'unavailable'}", "",
              status, "", "This 50-record pilot is bounded editorial evidence, not definitive proof."]
    atomic_write_bytes(trial_dir / "review_report.md", ("\n".join(report) + "\n").encode())
    return results


def trial_status(trial_dir: Path) -> dict[str, Any]:
    """Return the trial status."""
    audit = strict_audit(trial_dir); results = generate_review_results(trial_dir)
    return {"trial_dir": str(trial_dir), "audit": audit, "review": {key: results[key] for key in (
        "sample_count", "reviewed_count", "unreviewed_count", "v1_wins", "v2_wins", "equal", "neither")}}


def _css() -> str:
    return """body{margin:0;background:#f3f3f1;color:#202020;font:16px system-ui}main{max-width:1160px;margin:auto;padding:12px}header,.panel,nav{background:#fff;border:1px solid #d4d4d0;padding:12px;margin-bottom:10px}header{position:sticky;top:0;z-index:2}.progress{height:6px;background:#ddd;margin-top:8px}.progress span{display:block;height:100%;background:#267047}.quote{font:1.35rem Georgia,serif;line-height:1.4;border-left:5px solid #9b1c2c;padding:14px;background:#fff}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.reply{white-space:pre-wrap;overflow-wrap:anywhere;max-width:540px;border:1px solid #bbb;background:#fafafa;padding:16px;line-height:1.42}.reply h2{font:700 1rem system-ui;margin-top:0}.choices,.reasons{display:flex;gap:8px;flex-wrap:wrap}.choice{min-height:52px;padding:9px 14px;font-weight:700}.choice.selected{outline:3px solid #28704a;background:#e7f1e9}.reasons label{border:1px solid #ccc;padding:7px;background:#fafafa}textarea{width:100%;box-sizing:border-box;min-height:70px}nav{display:flex;justify-content:space-between}.muted{color:#666}.toolbar{display:flex;gap:8px;flex-wrap:wrap}button,select,input,textarea{font:inherit}@media(max-width:760px){main{padding:6px}.pair{grid-template-columns:1fr}.reply{max-width:none}.choice{flex:1 1 44%}}"""


def serve_trial(trial_dir: Path, host: str, port: int) -> None:
    """Serve trial."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"Warning: serving blind review on LAN interface {host}")
    rows = read_json(trial_dir / "complete_pairwise_renderings.json")["items"]
    sample = read_json(trial_dir / "review_sample_50.json")["items"]
    assignments = read_json(trial_dir / "blind_assignment_manifest.json")["items"]
    sample_ids = [row["quote_id"] for row in sample]
    class Handler(BaseHTTPRequestHandler):
        def send(self, code: int, body: bytes, mime: str = "text/html; charset=utf-8") -> None:
            self.send_response(code); self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers(); self.wfile.write(body)
        def redirect(self, target: str) -> None:
            self.send_response(303); self.send_header("Location", target); self.end_headers()
        def filtered(self, query: dict[str, list[str]]) -> list[str]:
            reviews = _review_store(trial_dir)["items"]; kind = (query.get("filter") or ["all"])[0]
            tag = (query.get("tag") or [""])[0]
            result = []
            for qid in sample_ids:
                review = reviews.get(qid)
                if kind == "unreviewed" and review: continue
                if kind in DECISIONS and (not review or review["decision"] != kind): continue
                if tag and (not review or tag not in review["reason_tags"]): continue
                result.append(qid)
            return result
        def do_GET(self) -> None:
            parsed = urlparse(self.path); query = parse_qs(parsed.query)
            if parsed.path == "/":
                ids = self.filtered(query)
                return self.redirect((f"/case/{ids[0]}" if ids else "/summary") + (f"?{parsed.query}" if parsed.query and ids else ""))
            if parsed.path == "/summary": return self.summary()
            if parsed.path.startswith("/case/"):
                qid = parsed.path.rsplit("/", 1)[-1]
                if qid not in assignments: return self.send(404, b"not found", "text/plain")
                return self.case(qid, query)
            self.send(404, b"not found", "text/plain")
        def case(self, qid: str, query: dict[str, list[str]]) -> None:
            reviews = _review_store(trial_dir)["items"]; review = reviews.get(qid); ids = self.filtered(query)
            if qid not in ids: ids = sample_ids
            index = ids.index(qid); previous = ids[max(0, index - 1)]; following = ids[min(len(ids) - 1, index + 1)]
            row = rows[qid]; assignment = assignments[qid]; presentations = blind_presentations(row, assignment)
            replies = {letter: presentations[letter]["text"] for letter in ("a", "b")}
            summary = _one_sentence(row["v2"]["field_provenance"]["context"]["immediate_subject"])
            selected = (review or {}).get("decision", ""); reviewed = len(reviews); suffix = "?" + urlencode({k: v[0] for k, v in query.items()}) if query else ""
            choices = "".join(f'<button type="button" class="choice {"selected" if selected==key else ""}" data-choice="{key}">{i}. {html.escape(label)}</button>' for i, (key, label) in enumerate(DECISIONS.items(), 1))
            reason_boxes = "".join(f'<label><input type="checkbox" name="reason" value="{html.escape(tag)}" {"checked" if review and tag in review["reason_tags"] else ""}> {html.escape(tag)}</label>' for tag in REASON_TAGS)
            filters = [("all", "All"), ("unreviewed", "Unreviewed")] + list(DECISIONS.items())
            filter_options = "".join(f'<option value="{key}" {"selected" if (query.get("filter") or ["all"])[0]==key else ""}>{html.escape(label)}</option>' for key, label in filters)
            body = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Context reply review</title><style>{_css()}</style></head><body><main><header><b>Blind historical-context review</b> · {index+1} of {len(ids)} · {reviewed}/50 reviewed ({reviewed*2:.0f}%) <a href="/summary" style="float:right">Summary</a><div class="progress"><span style="width:{reviewed*2}%"></span></div><form class="toolbar" action="/"><label>Filter <select name="filter">{filter_options}</select></label><label>Reason <select name="tag"><option value="">Any</option>{''.join(f'<option {"selected" if (query.get("tag") or [""])[0]==tag else ""}>{html.escape(tag)}</option>' for tag in REASON_TAGS)}</select></label><button>Apply</button></form></header><nav><a id="previous" href="/case/{previous}{suffix}">← Previous</a><span>{'Reviewed' if review else 'Unreviewed'}</span><a id="next" href="/case/{following}{suffix}">Next →</a></nav><section class="panel"><div class="quote">{html.escape(row['quote_text'])}</div><p><b>Canonical evidence:</b> {html.escape(summary)}</p></section><section class="pair"><article class="reply"><h2>Reply A</h2>{html.escape(replies['a'])}</article><article class="reply"><h2>Reply B</h2>{html.escape(replies['b'])}</article></section><form id="review" class="panel" method="post" action="/case/{qid}{suffix}"><input id="decision" type="hidden" name="decision" value="{selected}"><h2>Which reply is better?</h2><div class="choices">{choices}</div><details><summary>Optional reason tags</summary><div class="reasons">{reason_boxes}</div></details><label>Optional note<textarea name="note">{html.escape((review or {}).get('note',''))}</textarea></label><p id="state" class="muted">Selecting a decision saves immediately.</p><button name="save_next" value="1">Save and Next</button>{f'<details><summary>Neutral character counts</summary><p>A: {row[assignment["a"]]["raw_character_count"]} raw / {row[assignment["a"]]["weighted_character_count"]} weighted. B: {row[assignment["b"]]["raw_character_count"]} raw / {row[assignment["b"]]["weighted_character_count"]} weighted.</p></details>' if review else ''}</form></main><script>const form=document.getElementById('review'),decision=document.getElementById('decision'),state=document.getElementById('state');let saving=false,queued=false,noteTimer;async function save(){{if(!decision.value)return;if(saving){{queued=true;return}}do{{queued=false;saving=true;state.textContent='Saving…';try{{const response=await fetch(form.action,{{method:'POST',body:new FormData(form),headers:{{'X-Autosave':'1'}}}});state.textContent=response.ok?'Saved':'Save failed'}}catch(_error){{state.textContent='Save failed'}}finally{{saving=false}}}}while(queued)}}document.querySelectorAll('.choice').forEach(button=>button.onclick=()=>{{decision.value=button.dataset.choice;document.querySelectorAll('.choice').forEach(item=>item.classList.toggle('selected',item===button));save()}});document.querySelectorAll('input[name=reason]').forEach(box=>box.onchange=save);form.querySelector('textarea').oninput=()=>{{clearTimeout(noteTimer);noteTimer=setTimeout(save,300)}};document.addEventListener('keydown',event=>{{if(['TEXTAREA','INPUT','SELECT'].includes(document.activeElement.tagName))return;if('1234'.includes(event.key))document.querySelectorAll('.choice')[Number(event.key)-1].click();else if(event.key==='ArrowLeft')location.href=document.getElementById('previous').href;else if(event.key==='ArrowRight')location.href=document.getElementById('next').href}});form.addEventListener('submit',event=>{{if(saving)event.preventDefault()}});</script></body></html>'''
            self.send(200, body.encode())
        def do_POST(self) -> None:
            parsed = urlparse(self.path); qid = parsed.path.rsplit("/", 1)[-1]
            if qid not in assignments: return self.send(404, b"not found", "text/plain")
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode(), keep_blank_values=True)
            try: save_review(trial_dir, qid, (form.get("decision") or [""])[0], form.get("reason", []), (form.get("note") or [""])[0])
            except (ValueError, KeyError): return self.send(400, b"invalid review", "text/plain")
            if self.headers.get("X-Autosave") == "1": return self.send(200, b'{"saved":true}', "application/json")
            query = parse_qs(parsed.query); ids = self.filtered(query); index = ids.index(qid) if qid in ids else -1
            following = ids[min(len(ids) - 1, index + 1)] if ids else qid
            self.redirect(f"/case/{following}" + (f"?{parsed.query}" if parsed.query else ""))
        def summary(self) -> None:
            results = generate_review_results(trial_dir)
            body = f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>{_css()}</style><main><header><b>Review summary</b> <a href="/?filter=unreviewed" style="float:right">Continue unreviewed</a></header><section class="panel"><p>Reviewed <b>{results['reviewed_count']}/50</b>.</p><p>v1 wins {results['v1_wins']} · v2 wins {results['v2_wins']} · equal {results['equal']} · neither {results['neither']}</p><p>Formatter identities are shown only on this separate results page.</p></section></main>'''
            self.send(200, body.encode())
        def log_message(self, *_args: Any) -> None: pass
    ThreadingHTTPServer((host, port), Handler).serve_forever()
