"""Validate retained evidence before translating it into curated source claims.

This is an offline check, not a new editorial review. HTML checks retain lexical
content and punctuation while collapsing layout whitespace. PDF checks allow
typographic punctuation and line-break hyphenation, never fuzzy word matching.
Supplementary sources are hash-checked but cannot establish claims about the
primary source. Explicit reviewed variants and secondary sources keep their
weaker classifications rather than acquiring primary/exact status.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from lxml import html

_CLAIMS = {"wording", "attribution", "source_event", "date", "historical_context"}
_ROLES = {
    "wording": "wording_verification", "attribution": "attribution_support",
    "source_event": "source_event_support", "date": "source_event_support",
    "historical_context": "historical_context_support",
}
_PRIMARY_TYPES = {"primary", "primary_website_transcript", "official_primary_transcript"}
_SECONDARY_TYPES = {"secondary", "secondary_history", "secondary_recollection"}
_CHECK_KINDS = {"date", "event", "venue", "source_type", "speaker", "wording_and_context",
                "supporting_context", "editorial_information", "release_date"}
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _text(value: object, name: str) -> str:
    """Require real, nonempty text instead of accepting truthy containers."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Evidence {name} must be nonempty text")
    return value


def _hash_file(path: Path, expected: object) -> str:
    """Bind an ordinary retained file to its reviewed bytes."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Evidence source must be a regular, non-symlink file: {path.name}")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected:
        raise ValueError(f"Reviewed source has changed: {path.name}")
    return digest


def _normalise(value: str, *, pdf: bool = False) -> str:
    """Normalise layout, and only for PDF text, typographical differences."""
    value = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    value = re.sub(r"\[end p\d+\]", "", value)
    if pdf:
        value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value).translate(_TYPOGRAPHY).casefold()
    return " ".join(value.split())


def _url(value: str) -> str:
    """Reject ambiguous URLs before deriving publisher or source identity."""
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("Evidence needs an unambiguous HTTPS source URL")
    return parsed.hostname.casefold()


def _event_tokens(value: str) -> set[str]:
    """Compare event facts while expanding the archive's catalogue abbreviations."""
    value = value.casefold().replace("’", "'")
    for short, expanded in ((r"\bhc\b", "house of commons"), (r"\b2r\b", "second reading"),
                            (r"\bpqs\b", "prime minister's questions"), (r"\binterviewed\b", "interview")):
        value = re.sub(short, expanded, value)
    grammatical = {"a", "an", "the", "of", "to", "by", "at", "in", "on", "for", "about", "and", "s",
                   "speech", "speaking", "text", "debate", "chapter", "section"}
    return set(re.findall(r"[^\W_]+", value)) - grammatical


class _RetainedSource:
    """Extract only existing local HTML or the explicitly checked PDF pages."""

    def __init__(self, path: Path) -> None:
        """Read source structure without OCR, network access or provider calls."""
        self.path = path
        self.pdf = path.suffix.lower() == ".pdf"
        self.pages: dict[int, str] = {}
        if self.pdf:
            self.tree = None
            self.document_text = ""
        elif path.suffix.lower() in {".html", ".htm"}:
            self.tree = html.fromstring(path.read_bytes())
            for node in self.tree.xpath("//script|//style|//span[contains(@class, 'page-number') or @class='pagenum']"):
                node.drop_tree()
            self.document_text = self.tree.text_content()
        else:
            raise ValueError("Evidence format must be retained HTML or PDF")

    def check_text(self, check: dict) -> str:
        """Select a saved HTML location or a validated one-based PDF page."""
        if not self.pdf:
            location = check.get("location") or check.get("locator") or ""
            if isinstance(location, str) and location.startswith("/") and " and " not in location:
                try:
                    nodes = self.tree.xpath(location)
                except Exception as error:
                    raise ValueError("Invalid evidence XPath") from error
                if not nodes:
                    raise ValueError("Evidence XPath is absent from retained HTML")
                return " ".join(node.text_content() if hasattr(node, "text_content") else str(node)
                                for node in nodes)
            return self.document_text
        page = check.get("pdf_page_number")
        if type(page) is not int or page < 1:
            raise ValueError("PDF evidence needs a positive one-based page number")
        if page not in self.pages:
            try:
                import fitz
            except ImportError as error:
                raise ValueError("PDF evidence validation requires PyMuPDF") from error
            with fitz.open(self.path) as document:
                if page > len(document):
                    raise ValueError("PDF evidence page is outside retained source")
                self.pages[page] = document[page - 1].get_text()
        return self.pages[page]

    def contains(self, expected: str, actual: str) -> bool:
        """Check complete lexical evidence with format-appropriate normalisation."""
        normalised_expected = _normalise(expected, pdf=self.pdf)
        if self.pdf:
            # An extraction line break can split a genuinely hyphenated term.
            # Permit that exact observed boundary, never erase other hyphens.
            for left, right in re.findall(r"(\w+)-[ \t]*\n\s*(\w+)", unicodedata.normalize("NFKC", actual)):
                normalised_expected = re.sub(
                    r"(?<!\w)" + re.escape(left.casefold() + "-" + right.casefold()) + r"(?!\w)",
                    left.casefold() + right.casefold(), normalised_expected,
                )
        return normalised_expected in _normalise(actual, pdf=self.pdf)


def _validate_checks(row: dict, source: _RetainedSource, primary_hash: str) -> list[dict]:
    """Validate check structure and bind every admitted claim to retained text."""
    checks = row.get("evidence_checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Each addition needs structured evidence_checks")
    primary_checks = []
    checked_files = {source.path: primary_hash}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Evidence checks must be objects")
        claim = _text(check.get("claim"), "claim")
        method = _text(check.get("method") or check.get("inspection_method"), "inspection method")
        support = _text(check.get("exact_supporting_text") or check.get("supporting_text"), "supporting text")
        if len(claim.split()) < 3 or len(method.split()) < 3 or len(support.split()) < 2:
            raise ValueError("Evidence checks must contain meaningful claims, methods and supporting text")
        if check.get("kind") is not None and check["kind"] not in _CHECK_KINDS:
            raise ValueError("Unsupported evidence check kind")
        path = Path(_text(check.get("source_path"), "source path"))
        if path not in checked_files:
            checked_files[path] = _hash_file(path, check.get("source_sha256"))
        elif check.get("source_sha256") != checked_files[path]:
            raise ValueError("Evidence check source hash contradicts retained source")
        check_url = check.get("source_url") or check.get("url")
        if check_url:
            _url(check_url)
        if "image_path" in check or "image_sha256" in check:
            _hash_file(Path(_text(check.get("image_path"), "image path")), check.get("image_sha256"))
        if path != source.path:
            # Other editions can contain variant wording and OCR errors. Their
            # reviewed bytes are retained, but no primary claim is inferred.
            continue
        if (check_url or "") != (row.get("source_url") or row.get("primary_source_url") or ""):
            raise ValueError("Evidence check URL contradicts the primary source")
        actual = source.check_text(check)
        snippets = support.splitlines() if source.pdf else [support]
        if source.pdf and check.get("supporting_text_basis", "").startswith("Image-checked title words"):
            snippets = support.split(";")
        if any(not source.contains(snippet, actual) for snippet in snippets if snippet.strip()):
            raise ValueError("Evidence check text is absent from retained source")
        if check.get("datetime_attribute"):
            if (source.pdf or check["datetime_attribute"] != row["packet"]["date"]
                    or not source.tree.xpath("//time[@datetime=$value]", value=check["datetime_attribute"])):
                raise ValueError("Evidence date contradicts retained source or packet")
        primary_checks.append(check)
    if not primary_checks:
        raise ValueError("No evidence check supports the primary source")
    return primary_checks


def curated_source_for(row: dict, *, recorded_at: str) -> dict:
    """Admit only supported source classifications from a deterministic batch."""
    source_path = Path(_text(row.get("primary_source_path"), "primary source path"))
    source_hash = _hash_file(source_path, row.get("primary_source_sha256"))
    try:
        recorded_date = date.fromisoformat(recorded_at).isoformat()
    except ValueError:
        parsed_date = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if parsed_date.tzinfo is None:
            raise ValueError("Evidence timestamp must include its timezone")
        recorded_date = parsed_date.date().isoformat()
    packet = row["packet"]
    source = _RetainedSource(source_path)
    checks = _validate_checks(row, source, source_hash)
    retained = "\n".join(source.pages.values()) if source.pdf else source.document_text
    passage = _text(row.get("exact_supporting_passage"), "exact supporting passage")
    context_items = row.get("supporting_context") or row.get("context_support_strings")
    if not isinstance(context_items, list) or not context_items:
        raise ValueError("Evidence needs explicit supporting context")
    context_parts = [_text(item.get("exact_supporting_text") if isinstance(item, dict) else item,
                           "supporting context") for item in context_items]
    passage_parts = row.get("supporting_passage_parts")
    if passage_parts is not None:
        if (not isinstance(passage_parts, list) or len(passage_parts) < 2
                or any(not isinstance(part, str) or not part.strip() for part in passage_parts)
                or _normalise(" ".join(passage_parts)) != _normalise(passage)
                or any(not source.contains(part, retained) for part in passage_parts)):
            raise ValueError("Composite supporting passage parts are invalid or absent")
    elif not source.contains(passage, retained):
        raise ValueError("Exact supporting passage is absent from retained evidence")
    if any(not source.contains(part, retained) for part in context_parts):
        raise ValueError("Supporting context is absent from retained evidence")
    checked_text = "\n".join(c.get("exact_supporting_text") or c.get("supporting_text") for c in checks)
    if any(not source.contains(part, checked_text) for part in (passage_parts or [passage])):
        raise ValueError("Supporting passage has no corresponding evidence check")

    url = row.get("source_url") or row.get("primary_source_url") or ""
    domain = _url(url) if url else ""
    review = row.get("reviewed_classification", {})
    if not isinstance(review, dict):
        raise ValueError("Reviewed classification must be an object")
    if set(review) - {"rationale", "source_type", "source_quality_class", "wording_match_kind",
                      "source_domain", "source_publisher", "claims_supported", "page_independently_inspected"}:
        raise ValueError("Unknown reviewed classification fields")
    if "source_domain" in review and review["source_domain"] != domain:
        raise ValueError("Reviewed domain contradicts the retained source URL")
    if "page_independently_inspected" in review and type(review["page_independently_inspected"]) is not bool:
        raise ValueError("Reviewed inspection status must be a boolean")
    if review and len(_text(review.get("rationale"), "classification rationale").split()) < 3:
        raise ValueError("Reviewed classification needs a meaningful rationale")
    packet_sources = packet.get("sources", [])
    matching = [item for item in packet_sources if item.get("url", "") == url]
    if not matching:
        raise ValueError("Packet does not identify the retained primary source")
    declared_type = review.get("source_type") or matching[0].get("source_type")
    packet_type = matching[0].get("source_type")
    secondary = declared_type in _SECONDARY_TYPES
    if secondary != (packet_type in _SECONDARY_TYPES):
        raise ValueError("Reviewed source classification contradicts the packet source type")
    if secondary:
        if not review or review.get("source_quality_class") not in {"reliable_secondary_evidence", "secondary_recollection"}:
            raise ValueError("Secondary source needs an explicit reviewed secondary quality")
        if domain and review.get("source_domain") != domain:
            raise ValueError("Secondary source domain needs explicit reviewed identification")
        quality = review["source_quality_class"]
        source_type = declared_type
    elif source.pdf:
        if url or declared_type not in {"operator_supplied_bibliographic_citation", "thatcher_authored_primary_book"}:
            raise ValueError("PDF primary source classification contradicts its bibliographic citation")
        if not source.contains("Margaret Thatcher", retained):
            raise ValueError("Retained book does not substantiate its claimed author")
        quality, source_type = "strong_primary_evidence", "thatcher_authored_primary_book"
    else:
        if (domain not in {"margaretthatcher.org", "www.margaretthatcher.org"}
                or not re.fullmatch(r"/document/\d+", urlsplit(url).path)
                or declared_type not in _PRIMARY_TYPES):
            raise ValueError("Official primary transcript requires a validated Foundation document URL and source type")
        rows = [" ".join(node.text_content().split()) for node in source.tree.xpath("//tr")]
        if not any(re.match(r"Source:\s*(Thatcher MSS|Hansard)\b", text) for text in rows):
            raise ValueError("Archive metadata does not establish a primary transcript source")
        if not source.tree.xpath("//time[@datetime=$value]", value=packet["date"]):
            raise ValueError("Packet date is absent from retained archive metadata")
        authors = [" ".join(node.text_content().split()) for node in source.tree.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' docauthor ')]")]
        headers = " ".join(node.text_content() for node in source.tree.xpath("//h1"))
        named_archive_interview = ("Margaret Thatcher" in headers and "interview" in headers.casefold()
                                   and any(c.get("kind") == "speaker"
                                           and _normalise(c.get("exact_supporting_text", "")) in {"Prime Minister", "Margaret Thatcher"}
                                           for c in checks))
        if "Margaret Thatcher" not in authors and not named_archive_interview:
            raise ValueError("Retained transcript does not substantiate its claimed speaker")
        event_metadata = headers + " " + " ".join(text for text in rows if text.startswith(("Venue:", "Editorial comments:", "Source:")))
        if not _event_tokens(packet["source_event"]) <= _event_tokens(event_metadata):
            raise ValueError("Packet source event contradicts retained archive metadata")
        quality, source_type = "strong_primary_evidence", "official_primary_transcript"
    if review.get("source_quality_class", quality) != quality:
        raise ValueError("Reviewed quality contradicts source type")
    if packet.get("speaker") != "Margaret Thatcher":
        raise ValueError("Evidence cannot assign a different packet speaker to Margaret Thatcher")
    if source.pdf and not source.contains(str(packet["date"]), retained):
        raise ValueError("Book date is absent from retained evidence")
    title = row.get("source_title") or row.get("primary_source_title") or matching[0].get("title")
    _text(title, "source title")
    if source.pdf and not secondary:
        if not re.match(r"Margaret Thatcher\s*[,;:]", title):
            raise ValueError("Primary book citation must explicitly attribute authorship to Margaret Thatcher")
        if not _event_tokens(packet["source_event"]) <= _event_tokens(retained):
            raise ValueError("Packet source event contradicts retained book title or section evidence")
    if not source.pdf and not secondary:
        headers = " ".join(node.text_content() for node in source.tree.xpath("//h1"))
        if not source.contains(title, headers):
            raise ValueError("Source title contradicts retained archive heading")

    match_kind = review.get("wording_match_kind", "exact")
    if match_kind not in {"exact", "normalised", "excerpt", "historical_variant", "partial"}:
        raise ValueError("Unsupported reviewed wording classification")
    if passage_parts is not None and (match_kind != "partial" or packet.get("verification_status") != "composite"):
        raise ValueError("Composite evidence cannot be classified as an exact contiguous quotation")
    if packet.get("verification_status") in {"variant", "composite", "paraphrase", "misattributed", "unverified"} and match_kind == "exact":
        raise ValueError("Exact wording classification contradicts packet verification status")
    exact = _normalise(packet["quote_text"]) in _normalise(passage)
    typography = source.contains(packet["quote_text"], passage)
    if match_kind == "exact" and not exact:
        raise ValueError("Exact wording classification contradicts the supporting passage")
    if match_kind in {"normalised", "excerpt"} and not typography:
        raise ValueError("Reviewed wording classification is not supported by the passage")
    if match_kind in {"historical_variant", "partial"}:
        compatible = {"historical_variant": {"variant"}, "partial": {"composite", "paraphrase"}}
        if not review or packet.get("verification_status") not in compatible[match_kind]:
            raise ValueError("Variant or composite wording requires an explicit compatible review")
        if not source.contains(_text(packet.get("verified_text"), "verified wording"), passage):
            raise ValueError("Reviewed variant has no retained verified wording")
    claims = review.get("claims_supported", ["wording", "attribution", "source_event", "date", "historical_context"])
    if not isinstance(claims, list) or not claims or len(set(claims)) != len(claims) or not set(claims) <= _CLAIMS:
        raise ValueError("Reviewed claims are invalid")
    if secondary and "claims_supported" not in review:
        raise ValueError("Secondary review must explicitly scope supported claims")
    if review.get("page_independently_inspected") is False:
        inspected = False
    else:
        inspected = all(re.search(r"inspect|view|read", c.get("method") or c.get("inspection_method"), re.I)
                        for c in checks)
    publisher = "Margaret Thatcher Foundation" if not source.pdf and not secondary else review.get("source_publisher")
    if review.get("source_publisher", publisher) != publisher:
        raise ValueError("Reviewed publisher contradicts the validated source")
    roles = list(dict.fromkeys(_ROLES[claim] for claim in claims))
    if quality == "secondary_recollection":
        roles.append("secondary_recollection")
    context = "\n\n".join(context_parts)
    result = {
        "title": title, "url": url, "stable_locator": packet["stable_locator"],
        "source_type": source_type, "author_or_speaker": packet["speaker"],
        "source_date": packet["date"], "source_event": packet["source_event"],
        "assigned_roles": roles, "claims_supported": claims,
        "source_quality_class": quality, "wording_match_kind": match_kind,
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": hashlib.sha256(passage.encode()).hexdigest(),
        "supporting_context": context, "supporting_context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "evidence_origin": "operator_supplied_bibliographic_citation" if source.pdf else "independently_reviewed_public_retrieval",
        "page_independently_inspected": bool(inspected), "recorded_at": recorded_date,
        "rationale": review.get("rationale") or (
            "Retained source hashes, supporting passages, context and catalogue or bibliographic metadata were checked offline against the recorded inspection. "
            + ("PDF extraction correspondence allows typography and line-wrap normalisation; exact wording describes the match to the reviewed supporting passage. " if source.pdf else "")
            + "No new human approval or independent audio verification is claimed."),
    }
    if source.pdf:
        result["pdf_sha256"] = source_hash
    else:
        result.update(canonical_url=url, page_sha256=source_hash)
    if publisher:
        result["source_publisher"] = publisher
    return result
