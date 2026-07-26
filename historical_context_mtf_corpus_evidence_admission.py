#!/usr/bin/env python3
"""Admit the ten human-reviewed MTF corpus-search source bindings offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from historical_context_source_curated_evidence import curated_source_id


TRANSITION_DATE = "2026-07-26"
TRANSITION_TIMESTAMP = "2026-07-26T09:27:02Z"
PRODUCTION_BASE_COMMIT = "982eda4374ef294cb4ae20b0aa40b97de3862592"
DECISIONS_SHA256 = (
    "7a969ba9764ec9128d5cc6d148d6fc4d4841ec97560c17d6677fd7f28f1087dd"
)
CANDIDATES_SHA256 = (
    "9116d924c6ee204f8258962f7f51c7f4339761c4badedc659b228dc5f1fd4721"
)


def _record(
    candidate_id: str,
    document: str,
    title: str,
    date: str,
    file_sha256: str,
    text_sha256: str,
    match_kind: str,
    passage: str,
    context: str,
    variant_notes: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "document": document,
        "title": title,
        "date": date,
        "file_sha256": file_sha256,
        "text_sha256": text_sha256,
        "match_kind": match_kind,
        "passage": passage,
        "context": context,
        "variant_notes": variant_notes,
    }


SOURCE_RECORDS: dict[str, dict[str, Any]] = {
    "36d8caf8b8ae9fbd20473e1ea33dc37aa74045e0e39efeb241c36113da822027":
    _record(
        "6017a42f90d19f69704efb4d38579a0efd6c605171d2602f6a6aaa2455a64fe1",
        "107868", "Interview for Sunday Times", "1990-11-15",
        "70e4bdf628085c1aecdfff4075bd1e6f4b2ae88a0b9287ba8a2216e25496bf44",
        "04d551a2d8af2cca0afb41fe83b6672d72a82e2b1a092c1b95ae76707540125e",
        "exact",
        "Quite simple: Do you give up the power to issue your own currency? "
        "Do you give up the pound sterling?",
        "Asked how a referendum question on the proposed currency change "
        "could be framed, Thatcher reduced it to whether Britain would give "
        "up issuing its own currency and the pound sterling.",
        "The stored quotation matches the inspected primary transcript; "
        "ordinary terminal-punctuation normalisation is non-substantive.",
    ),
    "7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428":
    _record(
        "dcbc02336418ac22fb320eef2ddfdef01afa37aa91107b8e942e61f4a097480b",
        "108258", "Remarks departing Downing Street", "1990-11-28",
        "8161baa20a84fbb484b511d2bd15f179d8e64fec4cc44ace1f4d61a2f922d454",
        "caa31224e13b5ce75d53b486d32f3677b75e1bf7eaa39dcdc9ae9b454f540d30",
        "exact",
        "We're leaving Downing Street for the last time after "
        "eleven-and-a-half wonderful years, and we're very happy that we "
        "leave the United Kingdom in a very, very much better state than when "
        "we came here eleven and a half years ago.",
        "Thatcher made the remarks outside 10 Downing Street as she left after "
        "resigning as Prime Minister.",
        "The stored quotation matches the inspected primary transcript; "
        "ordinary terminal-punctuation normalisation is non-substantive.",
    ),
    "7748a7ec8ec505312e4714e9e98961453b0eecd8813a9678e28a57c332d3cd9f":
    _record(
        "c95f63a62feb46803a5c70457ae767fdd20d984358fdd9db5b0cd1ace067903d",
        "108256", "HC S: [Confidence in Her Majesty's Government]", "1990-11-22",
        "dd238f9c309b3ee1e413d366a873faa4766345d1292e4ae4055daddf0eb39049",
        "d19e2a5a5559d412fe16994dd087a62c6dece32cca53723110f640c9b653c582",
        "exact",
        "The hon. Gentleman is saying that he would rather that the poor were "
        "poorer, provided that the rich were less rich. That way one will "
        "never create the wealth for better social services, as we have. "
        "What a policy.",
        "During the Commons confidence debate, Thatcher answered Simon "
        "Hughes's criticism of inequality by contrasting relative inequality "
        "with higher absolute living standards.",
        "The stored quotation matches the inspected primary transcript; "
        "ordinary terminal-punctuation normalisation is non-substantive.",
    ),
    "78fac4018710af853f7eac01666370afad7551c24b604d19df3b5a710f7c5682":
    _record(
        "8278eaf3dd974a1fc373a9b7673789c523b4a1530b52df4469b5f96fc81a1247",
        "104594",
        "Speech at Guardian Young Businessman of the Year Award "
        "(defence of the budget)",
        "1981-03-11",
        "3ad3761923818b5bf0259bbb39d8c9bbbe8d6b7501ed46ac3f567b911b0a9d6a",
        "a222da4341a502cd82d11d9d4ed781637f94befe982340e66cc8a37967e01b29",
        "exact",
        "I tell you what they really mean, they mean, “We don't like the "
        "expenditure we have agreed, we are unwilling to raise the tax to pay "
        "for it. Let us print the money instead.” The most immoral path of all.",
        "Defending the Budget, Thatcher rejected deficit-financed reflation "
        "and characterised printing money instead of taxing to fund agreed "
        "expenditure as the most immoral course.",
        "The stored quotation matches the inspected primary transcript under "
        "authorised curly-versus-straight quotation-mark normalisation.",
    ),
    "5fbdcee710fe7e18425f4eeefe811890b3c8a03803f78b23685ad11309840679":
    _record(
        "38c95daa90f07d3aef657c286196bcc2c70160bbb65a798915c34d400064ffb8",
        "107332", "Speech to the College of Europe (\"The Bruges Speech\")",
        "1988-09-20",
        "c81b81d30dde9bb6faaeb204a25269e68d6a467014f242d627b65cf927f5b8e6",
        "b3fd5db8d9d016642e4dd1fab25849cb0df214fec8478cc40006e707b6b73b6e",
        "historical_variant",
        "We have not successfully rolled back the frontiers of the state in "
        "Britain, only to see them re-imposed at a European level with a "
        "European super-state exercising a new dominance from Brussels.",
        "In the Bruges speech, Thatcher opposed centralising European power "
        "in Brussels after Britain had dispersed state power at home.",
        "Primary variant: the source uses lower-case 'state' and "
        "'super-state', hyphenates 're-imposed', inserts 'a' before "
        "'European level', and differs in comma placement from the stored "
        "quotation.",
    ),
    "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab":
    _record(
        "66e3a53345d2c26099be1d26261b61034d3771d6fd6838b387d1a5bf928a2475",
        "102939",
        "Speech at Kensington Town Hall (\"Britain Awake\") (The Iron Lady)",
        "1976-01-19",
        "8d98575a081526544f1286431ea28dd9db417b8e8c6d8e248d4b68ff576fbaec",
        "39959086efd34cca7435b79627187dfdba6fa3f0fd19f319ed16fffd5effc59d",
        "historical_variant",
        "They put guns before butter, while we put just about everything "
        "before guns.",
        "Thatcher contrasted the Soviet Politburo's military priority with "
        "Western spending priorities in her Britain Awake speech.",
        "Primary variant: the source says 'They put guns before butter, while "
        "we put just about everything before guns'; the stored quotation "
        "replaces 'They' with 'The Russians' and divides the single sentence "
        "into two.",
    ),
    "6cab54a1bfcbd9c79b72c39ff64eb7126436cde07c37d48aa6fcd9ddfec4f662":
    _record(
        "697d20def5221d948b13dbeb3558f3a0c50b42f2061fc8d8552cd2ab18afcb44",
        "106689",
        "Interview for Woman's Own (\"no such thing [as society] \")",
        "1987-09-23",
        "f9857d5f5ec8ef1c67104077a4b2963bd297e33fa4f01ece7d85ce245bd4828e",
        "3051dfa50c983f18c7f1139ce8ff3d9c32fa35a407bfee213fd58257ea8e013e",
        "historical_variant",
        "There is no such thing! There are individual men and women and there "
        "are families.",
        "In the Woman's Own interview, Thatcher rejected treating society as "
        "an actor separate from individuals and families, then continued "
        "with reciprocal duties and obligations.",
        "Primary variant: the source asks 'who is society?' before saying "
        "'There is no such thing!' and then names individual men, women and "
        "families; the stored quotation recasts this as 'There is no such "
        "thing as society:' and adds a comma before the final clause.",
    ),
    "98000f36211d96c33ca0e033551ef2a7b24f56f5d624768c13abf666f5c61fe5":
    _record(
        "9f9940b176aaba1efeb7f3c912621b47f5cb75eceb9e9f2ad430383cd44e73fa",
        "102487",
        "Press Conference after winning Conservative leadership "
        "(Conservative Central Office)",
        "1975-02-11",
        "977d3c2ed77db2bd68d2fd913931cb2763c802203a6ad064ba66afd5c6b6c98f",
        "6226e495a0c61950d9c9d0c107a05baf55cf5e6ccd129b2871827e83bd1f564f",
        "historical_variant",
        "You don't win but just being against things, you only win by being "
        "for things and making your message perfectly clear.",
        "After winning the Conservative leadership, Thatcher said a "
        "distinctive positive Conservative philosophy and clear message were "
        "needed to win.",
        "Primary variant: the inspected transcript reads 'you don't win but "
        "just being against things'; the stored quotation regularises the "
        "apparent transcription error to 'You don't win by just being against "
        "things'.",
    ),
    "a8b53417a59ef215988e22c6c44d52e6a8401fec6ba89400001ba1e778b04855":
    _record(
        "b12bb0ba59ea93c59a6e590deb0c1dfc6b1eeb15fa123e843e62361408819029",
        "105763", "Speech to Conservative Party Conference", "1984-10-12",
        "9d66bf235ac60a5eda7b40608afb204cce6fa4603fbd7926e9b80faaf220a839",
        "959f631d90c53596081f9ac8bf6cc355a5b091ea04755f053232e4bb9461ee43",
        "historical_variant",
        "",
        "In the 1984 conference speech, Thatcher described a revolutionary "
        "minority exploiting industrial disputes to attack democratic order.",
        "Primary variant: the source spells 'organized' with a z; the stored "
        "quotation uses British spelling 'organised'.",
    ),
    "b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8":
    _record(
        "d11341e3e900bfb2bf2759a7e094f27e21be7ddccbcee7773da022dac5082c8a",
        "102769",
        "Speech to the Institute of SocioEconomic Studies "
        "(\"Let Our Children Grow Tall\")",
        "1975-09-15",
        "8da7a8b9ca51da2ddb015af2cd57af4a48f6a956224f7f20904447285b98d523",
        "e5842b254608bc1f789cb69586029bbd4d689f84de2c897d83f292cb7bb3b590",
        "historical_variant",
        "",
        "Thatcher contrasted voluntary giving and pooling in a free society "
        "with state-compelled redistribution.",
        "Primary variant: the source uses 'they' rather than 'people', adds "
        "'to whom they want to', and repeats 'In a free society' before the "
        "final clause; the stored quotation omits those words.",
    ),
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _normalise(value: str) -> str:
    return " ".join(value.split()).strip()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
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


def _comparison_text(value: str) -> str:
    """Normalise only typography and explicit transcript page markers."""
    for old, new in (
        ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
        ("–", "-"), ("—", "-"), ("\u00ad", ""),
    ):
        value = value.replace(old, new)
    import re
    value = re.sub(r"\[end p\d+\]", " ", value, flags=re.I)
    return _normalise(value).casefold().rstrip(".")


def _candidate_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for name in (
            "strong_primary_review_candidates",
            "other_local_review_candidates",
        )
        for row in document.get(name, [])
        if isinstance(row, dict)
    ]


def _verify_private_inputs(
    review_dir: Path,
    mirror_root: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    decisions_path = review_dir / "mtf_site_corpus_human_review_decisions.json"
    candidates_path = review_dir / "mtf_site_corpus_candidates.json"
    if (
        _sha256_bytes(decisions_path.read_bytes()) != DECISIONS_SHA256
        or _sha256_bytes(candidates_path.read_bytes()) != CANDIDATES_SHA256
    ):
        raise RuntimeError("reviewed MTF decision or candidate manifest differs")
    decisions = _json(decisions_path)
    candidates = _json(candidates_path)
    expected = {
        (quote_id, record["candidate_id"], record["document"])
        for quote_id, record in SOURCE_RECORDS.items()
    }
    decided = {
        (row["quote_id"], row["candidate_id"], row["mtf_document"])
        for group in ("accepted_exact_primary", "accepted_reviewed_variant")
        for row in decisions.get(group, [])
    }
    if decided != expected:
        raise RuntimeError("human-reviewed MTF admission scope differs")
    rows = {row.get("candidate_id"): row for row in _candidate_rows(candidates)}
    if set(rows) < {record["candidate_id"] for record in SOURCE_RECORDS.values()}:
        raise RuntimeError("reviewed MTF candidates are absent")

    verified: dict[str, Any] = {}
    for quote_id, record in SOURCE_RECORDS.items():
        row = rows[record["candidate_id"]]
        metadata = row.get("document_metadata", {})
        canonical_url = (
            f"https://www.margaretthatcher.org/document/{record['document']}"
        )
        if (
            row.get("quote_id") != quote_id
            or row.get("canonical_url") != canonical_url
            or metadata.get("canonical_url") != canonical_url
            or metadata.get("document_number") != record["document"]
            or metadata.get("title") != record["title"]
            or metadata.get("date") != record["date"]
            or metadata.get("author") != "Margaret Thatcher"
            or row.get("local_file_sha256") != record["file_sha256"]
            or row.get("page_text_sha256") != record["text_sha256"]
            or row.get("decisive_wording_match") is not True
        ):
            raise RuntimeError(f"reviewed MTF candidate differs: {quote_id}")
        path = mirror_root / "document" / f"{record['document']}.html"
        before = path.stat()
        body = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or path.is_symlink()
            or _sha256_bytes(body) != record["file_sha256"]
        ):
            raise RuntimeError(f"local MTF source bytes differ: {quote_id}")
        soup = BeautifulSoup(body, "lxml")
        article = (
            soup.select_one("article.node-archive-document")
            or soup.select_one("#documentbody")
        )
        if article is None:
            raise RuntimeError(f"local MTF document body is absent: {quote_id}")
        article_text = _normalise(article.get_text(" ", strip=True))
        if _sha256_text(article_text) != record["text_sha256"]:
            raise RuntimeError(f"local MTF document text differs: {quote_id}")
        candidate_passage = str(
            row.get("match", {}).get("supporting_passage") or ""
        ).strip()
        if (
            not candidate_passage
            or _comparison_text(candidate_passage)
            not in _comparison_text(article_text)
        ):
            raise RuntimeError(f"reviewed MTF passage is absent: {quote_id}")
        if not record["passage"]:
            record["passage"] = candidate_passage.rstrip(".") + "."
        if _comparison_text(record["passage"]) not in _comparison_text(article_text):
            raise RuntimeError(f"admission passage is absent: {quote_id}")
        verified[quote_id] = {
            "article_text_sha256": record["text_sha256"],
            "candidate_id": record["candidate_id"],
            "canonical_url": canonical_url,
            "document_number": record["document"],
            "local_file_sha256": record["file_sha256"],
            "metadata": {
                "author": metadata["author"],
                "date": metadata["date"],
                "title": metadata["title"],
            },
            "passage_sha256": _sha256_text(record["passage"]),
            "verified": True,
        }
    return verified, {
        "human_review_decisions_sha256": DECISIONS_SHA256,
        "research_candidate_manifest_sha256": CANDIDATES_SHA256,
    }


def _curated_source(
    quote_id: str,
    packet: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    canonical_url = (
        f"https://www.margaretthatcher.org/document/{record['document']}"
    )
    passage = record["passage"]
    context = record["context"]
    source = {
        "assigned_roles": [
            "wording_verification",
            "attribution_support",
            "source_event_support",
        ],
        "author_or_speaker": "Margaret Thatcher",
        "canonical_url": canonical_url,
        "claims_supported": [
            "wording", "attribution", "source_event", "date",
        ],
        "date": record["date"],
        "evidence_origin": "independently_reviewed_archival_retrieval",
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": _sha256_text(passage),
        "page_independently_inspected": True,
        "page_sha256": record["file_sha256"],
        "page_text_sha256": record["text_sha256"],
        "rationale": (
            "The locally preserved MTF HTML was independently reverified "
            "against its reviewed byte and article-text hashes. It identifies "
            "Margaret Thatcher, the event and date, and contains the reviewed "
            "exact or variant passage. The public canonical URL remains the "
            "source identity and no private path is retained."
        ),
        "recorded_at": TRANSITION_DATE,
        "source_date": packet["date"],
        "source_date_raw": record["date"],
        "source_event": packet["source_event"],
        "source_publisher": "Margaret Thatcher Foundation",
        "source_quality_class": "strong_primary_evidence",
        "source_review_candidate_id": record["candidate_id"],
        "source_type": "official_primary_transcript",
        "stable_locator": (
            f"Margaret Thatcher Foundation document {record['document']}"
        ),
        "supporting_context": context,
        "supporting_context_sha256": _sha256_text(context),
        "title": (
            f"Margaret Thatcher Foundation, {record['title']}, "
            f"{record['date']} (Document {record['document']})"
        ),
        "url": canonical_url,
        "wording_match_kind": record["match_kind"],
    }
    source["source_id"] = curated_source_id(quote_id, source)
    return source


def refresh_final_research_status(project_root: Path) -> None:
    """Refresh the packet fingerprint without changing research partitions."""
    research = (
        project_root
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    packets_path = research / "research_packets.json"
    status_path = research / "final_unresolved" / "final_research_status.json"
    status = _json(status_path)
    if (
        status.get("completed_quotes") != 627
        or status.get("unresolved_quotes") != 5
    ):
        raise RuntimeError("final research partition differs")
    status["corpus_hash"] = _sha256_bytes(packets_path.read_bytes())
    status["generated_timestamp"] = TRANSITION_TIMESTAMP
    _atomic_json(status_path, status)


def refresh_corpus_closure_audit(project_root: Path) -> None:
    """Rebuild and deterministically timestamp the canonical closure audit."""
    from semantic_alignment.quote_research_closure import corpus_closure_audit

    research = (
        project_root
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    audit = corpus_closure_audit(research, strict=True)
    audit["generated_at"] = TRANSITION_TIMESTAMP
    _atomic_json(
        research / "final_unresolved" / "corpus_closure_audit.json",
        audit,
    )


def apply(
    project_root: Path,
    review_dir: Path,
    mirror_root: Path,
) -> dict[str, Any]:
    """Verify and apply the reviewed ten-record canonical input transition."""
    research = (
        project_root
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    verified, private_hashes = _verify_private_inputs(review_dir, mirror_root)
    packets_path = research / "research_packets.json"
    curated_path = research / "historical_context_source_curated_evidence.json"
    status_path = research / "final_unresolved" / "final_research_status.json"
    packets_doc = _json(packets_path)
    curated = _json(curated_path)
    source_role_audit = _json(
        research / "historical_context_source_role_audit.json"
    )
    packets = packets_doc.get("items")
    if (
        not isinstance(packets, dict)
        or len(packets) != 627
        or curated.get("source_count") != 26
        or set(SOURCE_RECORDS) - set(packets)
        or set(SOURCE_RECORDS) & set(curated.get("items", {}))
    ):
        raise RuntimeError("canonical MTF admission baseline differs")
    before_quote_text = {
        quote_id: packet["quote_text"] for quote_id, packet in packets.items()
    }
    before_meanings = {
        quote_id: packet.get("intended_argument")
        for quote_id, packet in packets.items()
    }
    before = {
        "research_packets.json": _sha256_bytes(packets_path.read_bytes()),
        "historical_context_source_curated_evidence.json": _sha256_bytes(
            curated_path.read_bytes()
        ),
        "final_research_status.json": _sha256_bytes(status_path.read_bytes()),
    }
    before_public_fields = {
        quote_id: list(
            source_role_audit["items"][quote_id].get(
                "public_context_supported_fields", []
            )
        )
        for quote_id in sorted(SOURCE_RECORDS)
    }

    sources: dict[str, dict[str, Any]] = {}
    for quote_id, record in SOURCE_RECORDS.items():
        packet = packets[quote_id]
        if _sha256_text(packet["quote_text"]) != quote_id:
            raise RuntimeError(f"quotation identity differs: {quote_id}")
        packet["speaker"] = "Margaret Thatcher"
        if quote_id == (
            "78fac4018710af853f7eac01666370afad7551c24b604d19df3b5a710f7c5682"
        ):
            packet["date"] = record["date"]
            packet["source_event"] = record["title"]
            packet["stable_locator"] = (
                f"Margaret Thatcher Foundation Document {record['document']}"
            )
        elif quote_id == (
            "7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428"
        ):
            packet["stable_locator"] = (
                f"Margaret Thatcher Foundation Document {record['document']}"
            )
        packet["verified_text"] = (
            packet["quote_text"]
            if record["match_kind"] == "exact"
            else record["passage"]
        )
        packet["text_variation_notes"] = record["variant_notes"]
        packet["verification_status"] = (
            "exact" if record["match_kind"] == "exact" else "variant"
        )
        source = _curated_source(quote_id, packet, record)
        sources[quote_id] = source
        curated.setdefault("items", {})[quote_id] = {
            "quote_id": quote_id,
            "quote_text": packet["quote_text"],
            "quote_text_sha256": quote_id,
            "sources": [source],
        }

    if (
        {quote_id: packet["quote_text"] for quote_id, packet in packets.items()}
        != before_quote_text
        or {
            quote_id: packet.get("intended_argument")
            for quote_id, packet in packets.items()
        }
        != before_meanings
    ):
        raise RuntimeError("quotation text or Meaning changed")
    packets_doc["items"] = dict(sorted(packets.items()))
    curated["items"] = dict(sorted(curated["items"].items()))
    curated["quote_count"] = len(curated["items"])
    curated["source_count"] = sum(
        len(item["sources"]) for item in curated["items"].values()
    )
    _atomic_json(packets_path, packets_doc)
    _atomic_json(curated_path, curated)
    refresh_corpus_closure_audit(project_root)
    refresh_final_research_status(project_root)
    output = {
        "audit_kind": "historical_context_mtf_corpus_evidence_admission",
        "after_hashes": {
            "historical_context_source_curated_evidence.json": _sha256_bytes(
                curated_path.read_bytes()
            ),
            "final_research_status.json": _sha256_bytes(
                status_path.read_bytes()
            ),
            "research_packets.json": _sha256_bytes(packets_path.read_bytes()),
        },
        "before_hashes": before,
        "before_public_context_supported_fields": before_public_fields,
        "counts": {
            "exact_primary": 4,
            "primary_variant": 6,
            "source_records_admitted": 10,
        },
        "generated_at": TRANSITION_TIMESTAMP,
        "human_review": private_hashes,
        "meaning_fields_changed": 0,
        "network_requests": 0,
        "provider_requests": 0,
        "quotation_text_changed": False,
        "schema_version": 1,
        "source_bindings": {
            quote_id: {
                "candidate_id": record["candidate_id"],
                "document_number": record["document"],
                "source_id": sources[quote_id]["source_id"],
                "verification_status": packets[quote_id]["verification_status"],
            }
            for quote_id, record in sorted(SOURCE_RECORDS.items())
        },
        "source_reverification": verified,
    }
    _atomic_json(
        project_root / "historical_context_mtf_corpus_evidence_admission_audit.json",
        output,
    )
    return output


def write_transition_manifest(project_root: Path) -> dict[str, Any]:
    """Write the exact hash-bound post-v9 transition for admitted sources."""
    from historical_context_formatter import load_and_validate_corpus
    from historical_context_public_projection_review import (
        POST_V9_TRANSITION_KIND,
        POST_V9_TRANSITION_STATUS,
        V9_POLICY,
        _post_v9_input_hashes,
    )

    research = (
        project_root
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    packets, _unresolved = load_and_validate_corpus(
        research,
        require_source_role_audit=True,
    )
    curated = _json(
        research / "historical_context_source_curated_evidence.json"
    )
    admission_audit = _json(
        project_root / "historical_context_mtf_corpus_evidence_admission_audit.json"
    )
    baseline_fields = admission_audit.get(
        "before_public_context_supported_fields"
    )
    if set(baseline_fields or {}) != set(SOURCE_RECORDS):
        raise RuntimeError("MTF admission baseline field snapshot differs")
    items: dict[str, Any] = {}
    changes = 0
    for quote_id, record in sorted(SOURCE_RECORDS.items()):
        sources = curated["items"][quote_id]["sources"]
        if len(sources) != 1:
            raise RuntimeError(f"admitted MTF source is ambiguous: {quote_id}")
        source = sources[0]
        baseline = list(baseline_fields[quote_id])
        current = list(
            packets[quote_id]["_source_role_audit"][
                "public_context_supported_fields"
            ]
        )
        changes += baseline != current
        items[quote_id] = {
            "current_public_context_supported_fields": current,
            "quote_text_sha256": quote_id,
            "source_bindings": [{
                "source_id": source["source_id"],
                "source_review_candidate_id": record["candidate_id"],
            }],
            "v9_baseline_public_context_supported_fields": baseline,
        }
    quote_ids_hash = _sha256_text(
        "".join(f"{quote_id}\n" for quote_id in sorted(items))
    )
    manifest = {
        "counts": {
            "public_field_changes": changes,
            "source_additions": len(items),
            "transition_packets": len(items),
        },
        "human_review": {
            "decisions_sha256": DECISIONS_SHA256,
            "decision": "priority_a_and_genuine_priority_b_accepted",
            "reviewed_at": TRANSITION_DATE,
        },
        "input_hashes": _post_v9_input_hashes(research),
        "items": items,
        "manifest_kind": POST_V9_TRANSITION_KIND,
        "policy_transition": {"from": V9_POLICY, "to": V9_POLICY},
        "production_base_commit": PRODUCTION_BASE_COMMIT,
        "schema_version": 1,
        "transition_quote_ids_sha256": quote_ids_hash,
        "transition_status": POST_V9_TRANSITION_STATUS,
    }
    _atomic_json(
        project_root
        / "historical_context_v9_mtf_corpus_evidence_transition_manifest.json",
        manifest,
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Run the offline, operator-reviewed MTF evidence admission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--mirror-root", type=Path, required=True)
    parser.add_argument("--write-transition-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.write_transition_only:
        result = write_transition_manifest(root)
    else:
        result = apply(
            root,
            args.review_dir.resolve(),
            args.mirror_root.resolve(),
        )
    print(json.dumps(result["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
