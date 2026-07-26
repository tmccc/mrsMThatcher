#!/usr/bin/env python3
"""Review the frozen v7-to-v9 and later public historical-context projection.

The v8-to-v9 transition manifest freezes the twelve independently reviewed
remediations so current v9 evidence cannot leak backwards into the reconstructed
v7 baseline.  An optional, separately reviewed post-v9 transition manifest can
declare later evidence additions without rewriting that historical manifest.
The review renders every cumulatively changed packet with the real public
formatter and writes only to the explicit output path.  It performs no network
access and never changes packet or evidence data.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    _v2_british_date,
    atomic_write_json,
    format_context_reply_public,
    load_and_validate_corpus,
)
from historical_context_source_roles import (
    AUDIT_FILENAME,
    POLICY_VERSION as CURRENT_POLICY,
)


ROOT = Path(__file__).resolve().parent
REVIEW_SCHEMA_VERSION = 3
REVIEW_KIND = "historical_context_public_projection_review"
V7_POLICY = "historical-context-source-roles-v7-curated-source-adjudications"
V8_POLICY = "historical-context-source-roles-v8-claim-specific-public-context"
V9_POLICY = "historical-context-source-roles-v9-archive-provenance"
TRANSITION_MANIFEST_PATH = (
    ROOT / "historical_context_v8_v9_transition_manifest.json"
)
POST_V9_TRANSITION_MANIFEST_PATH = (
    ROOT / "historical_context_v9_local_book_evidence_transition_manifest.json"
)
STATECRAFT_TRANSITION_MANIFEST_PATH = (
    ROOT / "historical_context_v9_statecraft_primary_transition_manifest.json"
)
MTF_TRANSITION_MANIFEST_PATH = (
    ROOT / "historical_context_v9_mtf_corpus_evidence_transition_manifest.json"
)
POST_V9_TRANSITION_KIND = (
    "historical_context_v9_reviewed_evidence_transition"
)
POST_V9_TRANSITION_STATUS = "canonical_admission_reviewed"
POST_V9_REVIEWED_BINDINGS = {
    "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80": (
        "a043b13b6e69783a2d61f291e0f9a16656a398a8604155ef253bcc60fb9a3777",
        "41607f622b3f8d07407bc2b1b2be68e9624037b9d1b0f380f9c7398602257f29",
    ),
    "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351": (
        "0fb8950cd36f132d3ba9baf9536fabb485e9d6bca7484bae5333e5c220c3c6f6",
        "a2f98708d029eaf4b8bd309660db763502510be5a75627d214b5530d80f8a1f5",
    ),
    "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea": (
        "667ebcde5a5ad01990f77c353e979903865c97087f12bfd2bf1062dedded6b25",
        "ab036aa67cf8241c90dcbfbe62e5887357118829a8a5c7cbd3dc223523cd7594",
    ),
    "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37": (
        "7f9ca47a606967d657418caa780c4c77dcec706af339cf4fb90b791690d08efa",
        "5a639e39d475ac8e22b59e890c45bb617d64ad3d15ca1b45a223ddc9a3628a73",
    ),
    "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea": (
        "3ded90e076eec6ee1154959853f4a5e9bc1841a8ed32aadb373491f28491bf50",
        "6759265bfb86c0564890a0435f625e8b98a9059ddb7e69c4534c51a064a7560e",
    ),
}
POST_V9_BASELINE_CURATED_SOURCE_COUNT = 14
POST_V9_BASELINE_CURATED_SOURCE_IDS_SHA256 = (
    "5b971a327a33bae293b2f89a2a2b5b1d59e638de8892e0fbc3e42442bd0b46d8"
)
POST_V9_TRANSITION_MANIFEST_SHA256 = (
    "ea4963ea1afd6e5e8b94454ddc5e491e52e0066579c1a6b29576fd69f441214f"
)
STATECRAFT_REVIEWED_BINDINGS = {
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729": (
        "fa4a0ad84a0b41b9494093de1d0e8a2db725e04b2263079db8a68f50f812a5b5",
        "75fd4da816b92425452a48ce4ffd8dab2c538f857c8de0de5c18d085bd329456",
    ),
    "685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90": (
        "9eae0aa97a4bb82e5a518e29d01c1706be4924e0bd3f765773ef49e5994f7f54",
        "192e8d138fa01dbf160ede438db59ca5c1f26321df2d3b04ffeb60d11b396b95",
    ),
    "928a6686bc6bb6d35cd1ec139373cb73b85ba9fa40807098d5572ae153dab144": (
        "81f316c816a06cff0bd3bb02f1ad5420a406efba74e80723815fe409bdd63079",
        "1541cd79b74e97ede1bfd089ad8e0fe73f3096463d3c44edc5425ac8be9d7947",
    ),
    "a4f1d422097a48114bf30a587c04cf05859ff030d2df3d5d9051c6ca57a7943c": (
        "add06ddd94e0829f5263b44e7ceb5f11136d8b789cc2e0d5b88ff53e2d93636f",
        "67610f7a0dcd4474e811f05f0b5e0a1e8fbc6632afa85553acd43cc0281466ad",
    ),
    "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7": (
        "fb04f2faf8f1297768446e9ccdd4014564f3fd34bb92ef0b3924f0e326895bbc",
        "9016f29ef388fe954005aa090add8fe86bd9fd8b1b1db4997548cd200257adf4",
    ),
    "db46e7519946d4312907a8b7c7337eea0daaf3689850c2ef35035a6bda062173": (
        "202db856ef09a53e672eb76d13d1c6f61463af2f8bce4ce4e2a8848850be5f3e",
        "38efb1e0e9571009544c592a48385902e02d0d4d3fcb453eb31f073bcb1f7416",
    ),
    "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d": (
        "076e7b6c6babc17543676607ca7cd4289378ff7d0143334cbf1c4e0372601934",
        "66276ffca1cca9b2b00344a329a7cc850652b700d26e033ca43ea659f25eb897",
    ),
}
STATECRAFT_BASELINE_CURATED_SOURCE_COUNT = 19
STATECRAFT_BASELINE_CURATED_SOURCE_IDS_SHA256 = (
    "0d017da429275711e6bb15eec441ed343c3a19bd92a8579c2670e771e0e09631"
)
STATECRAFT_NEW_COMPLETED_PACKET_IDS = frozenset({
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729",
})
MTF_REVIEWED_BINDINGS = {
    "36d8caf8b8ae9fbd20473e1ea33dc37aa74045e0e39efeb241c36113da822027": (
        "51045c02f8aeb528e66d95b35bad779e0e73d9f9a8adbfce07fb2a9afb687449",
        "6017a42f90d19f69704efb4d38579a0efd6c605171d2602f6a6aaa2455a64fe1",
    ),
    "5fbdcee710fe7e18425f4eeefe811890b3c8a03803f78b23685ad11309840679": (
        "1429e65633067db730c618f5edca3e883a23094b40487a6d15dd81dcee7bcee3",
        "38c95daa90f07d3aef657c286196bcc2c70160bbb65a798915c34d400064ffb8",
    ),
    "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab": (
        "aa3695c4df72590ded150383080e0147930c9fced14bc310be5b0ddf047e2134",
        "66e3a53345d2c26099be1d26261b61034d3771d6fd6838b387d1a5bf928a2475",
    ),
    "6cab54a1bfcbd9c79b72c39ff64eb7126436cde07c37d48aa6fcd9ddfec4f662": (
        "66dfecb37c8e8c16c7476b00bd682955c65568751feb78d5b12e74857478a463",
        "697d20def5221d948b13dbeb3558f3a0c50b42f2061fc8d8552cd2ab18afcb44",
    ),
    "7066fdf6027a1cbdc45dad3ef5cd95d0ee67a6500e519480b3a0814a266dc428": (
        "cde6083f6cffca2a6cf233f2bf612e469267d89aa772f757a024c3669c025c79",
        "dcbc02336418ac22fb320eef2ddfdef01afa37aa91107b8e942e61f4a097480b",
    ),
    "7748a7ec8ec505312e4714e9e98961453b0eecd8813a9678e28a57c332d3cd9f": (
        "6fa5c4ead6119dd1b991adb53b9aaf29a1ee36c35d01d1257dbe6cb4cce2136b",
        "c95f63a62feb46803a5c70457ae767fdd20d984358fdd9db5b0cd1ace067903d",
    ),
    "78fac4018710af853f7eac01666370afad7551c24b604d19df3b5a710f7c5682": (
        "dc53442a426141551ac3a4a0052dd20e5954e0c54d689c33cced561a90af848f",
        "8278eaf3dd974a1fc373a9b7673789c523b4a1530b52df4469b5f96fc81a1247",
    ),
    "98000f36211d96c33ca0e033551ef2a7b24f56f5d624768c13abf666f5c61fe5": (
        "1dda5cfc334ed87441a23a3042ff173679b534e768bb42178382c2f98d327995",
        "9f9940b176aaba1efeb7f3c912621b47f5cb75eceb9e9f2ad430383cd44e73fa",
    ),
    "a8b53417a59ef215988e22c6c44d52e6a8401fec6ba89400001ba1e778b04855": (
        "2074c64d598f7db44fea37d5f0b35186a09e7b79431d64fd8a8c3cb3ead90d45",
        "b12bb0ba59ea93c59a6e590deb0c1dfc6b1eeb15fa123e843e62361408819029",
    ),
    "b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8": (
        "06c28b7a0bc0cd662eeb0ce134c753bd9f09472c440c6295f56c3675a948380e",
        "d11341e3e900bfb2bf2759a7e094f27e21be7ddccbcee7773da022dac5082c8a",
    ),
}
MTF_BASELINE_CURATED_SOURCE_COUNT = 26
MTF_BASELINE_CURATED_SOURCE_IDS_SHA256 = (
    "965df9185118298b82c8ebd848cd6938344a69430b2f08108f98c87c74a464b9"
)
MTF_TRANSITION_MANIFEST_SHA256 = (
    "10d9955279e41a0908475bb083f5a0c93bddabbe152767c74c5a18da39dbdafd"
)
POST_V9_INPUT_NAMES = (
    "corpus_manifest.json",
    "historical_context_packet_corrections.json",
    "historical_context_source_curated_evidence.json",
    AUDIT_FILENAME,
    "research_packets.json",
    TRANSITION_MANIFEST_PATH.name,
)

DOCUMENT_104653_QUOTE_ID = (
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
)
B32_QUOTE_ID = (
    "b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c"
)
CLEAN_EVENT_ONLY_QUOTE_ID = (
    "3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12"
)
DOCUMENT_104653_SOURCE_ID = (
    "729ef0ab1006098f0e0b1dbfb6fefc99410908a4d980638d48c024ee268c55f1"
)
V8_ADDED_SOURCE_IDS = frozenset({DOCUMENT_104653_SOURCE_ID})

SAFE_EVENT_ONLY_FALLBACK = (
    "Context — The surviving record identifies an occasion, but does not "
    "establish a reliable date."
)
CLEAN_EVENT_ONLY_CONTEXT = (
    "Context — Publication of her memoir, The Downing Street Years."
)
DOCUMENT_104653_CONTEXT = (
    "Context — Speech to Conservative Women's Conference, 20 May 1981."
)

EXPECTED_MANUAL_HINT_IDS = frozenset({
    "19cb0b567f5dd9f7f3a15dd38c8c0455112573afe574fc3fbce0151783e5f296",
    "1a73e33d64ecdbdcce0ced1691e91457efd50720116168b28a2371d6b8a8eec6",
    "2b1356ba865985190519435bf06d5d3be6ae3bdd50deb80ec295263502c918b4",
    "89a020d133d390624dc61a59ab7ffbb59ba06792b45eac6b5337b564662146d0",
    "8d23f8984b463c3b5218c7b3c7e56b0a8dea3025d3874a9970349a69f4e1e46e",
    "8ee86fceccc2ab211e6b4f20764ba758d8b0278c1fcaa2f46c302f2d440c61b1",
    "e901673a2a91f9dadb8b896ef8232aa5d7e6f20c0602f12721d74810db8233f0",
    "eece24fa8b46957889f60d3c70e278aa5d9f646114030806262b8c530e906f6a",
    "fe8be1f80a36cb747a12ad4925152b57ae2ead8549b083ad14d31179bf418361",
})

_UNKNOWN = frozenset({
    "", "n/a", "n.a.", "none", "not available", "unknown", "unavailable",
})
_DATE_LIKE = re.compile(
    r"(?:\b(?:18|19|20)\d{2}\b|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b)",
    re.I,
)
_BARE_DATE = re.compile(
    r"(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+\d{4}|(?:18|19|20)\d{2}",
    re.I,
)
_EVENT_LIKE_SOURCE_TITLE = re.compile(
    r"\b(?:speech|interview|article|address|press release|campaign|lecture|"
    r"statement|broadcast|remarks|memoir|book|reported|reports|newspaper|"
    r"magazine|general election|hansard)\b",
    re.I,
)
_DAY_PRECISION_DATE = re.compile(
    r"\d{1,2} (?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December) \d{4}"
)
_MONTH_PRECISION_DATE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December) \d{4}"
)
_YEAR_PRECISION_DATE = re.compile(r"(?:18|19|20)\d{2}")


def _clean(value: Any) -> str:
    """Return one whitespace-normalised string."""
    return " ".join(str(value or "").split()).strip()


def _known(value: Any) -> bool:
    """Return whether a v7 packet scalar was considered known."""
    return _clean(value).casefold() not in _UNKNOWN


def _file_sha256(path: Path) -> str:
    """Hash one input without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    """Hash one canonical JSON value."""
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _load_transition_manifest() -> dict[str, Any]:
    """Load the reviewed, hash-bound v8-to-v9 transition."""
    value = json.loads(TRANSITION_MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("manifest_kind")
        != "historical_context_v8_v9_reviewed_transition"
        or value.get("policy_transition") != {
            "historical_baseline": V7_POLICY,
            "from": V8_POLICY,
            "to": V9_POLICY,
        }
        or not isinstance(value.get("items"), dict)
    ):
        raise RuntimeError("v8-to-v9 transition manifest is invalid")
    return value


def _post_v9_input_hashes(research_dir: Path) -> dict[str, str]:
    """Return the exact production inputs bound by a later transition."""
    paths = {
        "corpus_manifest.json": research_dir / "corpus_manifest.json",
        "historical_context_packet_corrections.json": (
            research_dir / "historical_context_packet_corrections.json"
        ),
        "historical_context_source_curated_evidence.json": (
            research_dir / "historical_context_source_curated_evidence.json"
        ),
        AUDIT_FILENAME: research_dir / AUDIT_FILENAME,
        "research_packets.json": research_dir / "research_packets.json",
        TRANSITION_MANIFEST_PATH.name: TRANSITION_MANIFEST_PATH,
    }
    return {name: _file_sha256(paths[name]) for name in POST_V9_INPUT_NAMES}


def _load_post_v9_transition_manifest(
    path: Path = POST_V9_TRANSITION_MANIFEST_PATH,
) -> dict[str, Any] | None:
    """Load one active post-v9 transition, or return baseline-only mode."""
    if not path.exists():
        return None
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("post-v9 transition manifest is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("manifest_kind") != POST_V9_TRANSITION_KIND
        or value.get("transition_status") != POST_V9_TRANSITION_STATUS
        or value.get("policy_transition") != {
            "from": V9_POLICY,
            "to": V9_POLICY,
        }
        or not isinstance(value.get("items"), dict)
        or not value["items"]
    ):
        raise RuntimeError("post-v9 transition manifest is invalid")
    return value


def _public_fields_without_sources(
    audit: dict[str, Any],
    excluded_source_ids: set[str],
) -> list[str]:
    """Reconstruct claim-specific fields with later sources removed."""
    remaining = [
        source
        for source in audit.get("renderable_sources", [])
        if source.get("source_id") not in excluded_source_ids
    ]
    return [
        field
        for field in ("source_event", "date", "historical_context")
        if (
            field != "date"
            or audit.get("confidence_after", {}).get("date") != "unknown"
        )
        and any(
            field in source.get("claims_supported", [])
            and source.get("source_quality_class") in {
                "strong_primary_evidence",
                "reliable_secondary_evidence",
                "secondary_recollection",
            }
            for source in remaining
        )
    ]


def _validate_post_v9_transition(
    manifest: dict[str, Any],
    *,
    packets: dict[str, dict[str, Any]],
    curated: dict[str, Any],
    current_field_map: dict[str, list[str]],
    historical_transition_ids: set[str],
    expected_input_hashes: dict[str, str],
    expected_bindings: dict[str, tuple[str, str]],
    expected_baseline_source_count: int,
    expected_baseline_source_ids_sha256: str,
    later_source_ids: set[str] | None = None,
    expected_declared_baseline_fields: (
        dict[str, list[str]] | None
    ) = None,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Validate and apply one hash-bound, disjoint post-v9 transition."""
    items = manifest["items"]
    transition_ids = set(items)
    if transition_ids != set(expected_bindings):
        raise RuntimeError("post-v9 transition does not contain the reviewed scope")
    if (
        expected_declared_baseline_fields is not None
        and set(expected_declared_baseline_fields) != transition_ids
    ):
        raise RuntimeError("post-v9 declared baseline scope differs")
    transition_id_hash = hashlib.sha256("".join(
        f"{quote_id}\n" for quote_id in sorted(transition_ids)
    ).encode("utf-8")).hexdigest()
    if (
        transition_ids & historical_transition_ids
        or not transition_ids <= set(packets)
        or set(manifest.get("input_hashes", {})) != set(POST_V9_INPUT_NAMES)
        or manifest.get("input_hashes") != expected_input_hashes
        or manifest.get("transition_quote_ids_sha256") != transition_id_hash
    ):
        raise RuntimeError("post-v9 transition scope or inputs differ")

    curated_items = curated.get("items", {})
    if not isinstance(curated_items, dict):
        raise RuntimeError("post-v9 transition curated evidence is invalid")
    all_curated_source_ids = {
        _clean(source.get("source_id"))
        for curated_item in curated_items.values()
        if isinstance(curated_item, dict)
        for source in curated_item.get("sources", [])
        if isinstance(source, dict)
    }
    reviewed_source_ids = {
        source_id for source_id, _candidate_id in expected_bindings.values()
    }
    later_source_ids = set(later_source_ids or ())
    active_curated_source_ids = all_curated_source_ids - later_source_ids
    baseline_source_ids = all_curated_source_ids - reviewed_source_ids
    baseline_source_ids -= later_source_ids
    baseline_source_ids_hash = hashlib.sha256("".join(
        f"{source_id}\n" for source_id in sorted(baseline_source_ids)
    ).encode("utf-8")).hexdigest()
    if (
        len(active_curated_source_ids)
        != expected_baseline_source_count + len(reviewed_source_ids)
        or len(baseline_source_ids) != expected_baseline_source_count
        or baseline_source_ids_hash
        != expected_baseline_source_ids_sha256
    ):
        raise RuntimeError("post-v9 transition contains undeclared curated sources")

    baseline_field_map = dict(current_field_map)
    records: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_candidate_ids: set[str] = set()
    source_addition_count = 0
    public_field_change_count = 0
    for quote_id in sorted(transition_ids):
        item = items[quote_id]
        if not isinstance(item, dict):
            raise RuntimeError(
                f"post-v9 transition item is invalid for {quote_id}"
            )
        bindings = item.get("source_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise RuntimeError(
                f"post-v9 transition bindings are invalid for {quote_id}"
            )
        canonical_bindings: list[dict[str, str]] = []
        for binding in bindings:
            if not isinstance(binding, dict):
                raise RuntimeError(
                    f"post-v9 transition binding is invalid for {quote_id}"
                )
            source_id = _clean(binding.get("source_id"))
            candidate_id = _clean(binding.get("source_review_candidate_id"))
            if (
                not re.fullmatch(r"[0-9a-f]{64}", source_id)
                or not re.fullmatch(r"[0-9a-f]{64}", candidate_id)
                or source_id in seen_source_ids
                or candidate_id in seen_candidate_ids
            ):
                raise RuntimeError(
                    f"post-v9 transition binding is invalid for {quote_id}"
                )
            seen_source_ids.add(source_id)
            seen_candidate_ids.add(candidate_id)
            canonical_bindings.append({
                "source_id": source_id,
                "source_review_candidate_id": candidate_id,
            })
        if canonical_bindings != sorted(
            canonical_bindings,
            key=lambda row: (
                row["source_id"], row["source_review_candidate_id"],
            ),
        ):
            raise RuntimeError(
                f"post-v9 transition bindings are not canonical for {quote_id}"
            )

        declared_pairs = {
            (row["source_id"], row["source_review_candidate_id"])
            for row in canonical_bindings
        }
        if declared_pairs != {expected_bindings[quote_id]}:
            raise RuntimeError(
                f"post-v9 transition binding is not reviewed for {quote_id}"
            )
        curated_pairs = {
            (
                _clean(source.get("source_id")),
                _clean(source.get("source_review_candidate_id")),
            )
            for source in curated_items.get(quote_id, {}).get("sources", [])
            if isinstance(source, dict)
            and (
                _clean(source.get("source_id")),
                _clean(source.get("source_review_candidate_id")),
            )
            in declared_pairs
        }
        audit = packets[quote_id]["_source_role_audit"]
        audited_source_ids = {
            _clean(source.get("source_id"))
            for source in audit.get("curated_sources", [])
            if isinstance(source, dict)
        }
        source_ids = {row["source_id"] for row in canonical_bindings}
        reconstructed_baseline_fields = _public_fields_without_sources(
            audit, source_ids
        )
        baseline_fields = (
            list(expected_declared_baseline_fields[quote_id])
            if expected_declared_baseline_fields is not None
            else reconstructed_baseline_fields
        )
        current_fields = list(current_field_map[quote_id])
        declared_baseline = item.get(
            "v9_baseline_public_context_supported_fields"
        )
        declared_current = item.get(
            "current_public_context_supported_fields"
        )
        if (
            item.get("quote_text_sha256") != quote_id
            or curated_pairs != declared_pairs
            or not source_ids <= audited_source_ids
            or declared_baseline != baseline_fields
            or declared_current != current_fields
        ):
            raise RuntimeError(
                f"post-v9 transition evidence differs for {quote_id}"
            )

        baseline_field_map[quote_id] = baseline_fields
        source_addition_count += len(canonical_bindings)
        if baseline_fields != current_fields:
            public_field_change_count += 1
        records.append({
            "current_public_context_supported_fields": current_fields,
            "quote_id": quote_id,
            "source_bindings": canonical_bindings,
            "v9_baseline_public_context_supported_fields": baseline_fields,
        })

    if manifest.get("counts") != {
        "transition_packets": len(transition_ids),
        "source_additions": source_addition_count,
        "public_field_changes": public_field_change_count,
    }:
        raise RuntimeError("post-v9 transition counts differ")
    return baseline_field_map, records


def _v7_public_context_supported_fields(
    packet: dict[str, Any],
    transition_item: dict[str, Any] | None = None,
    *,
    later_source_ids: set[str] | None = None,
) -> list[str]:
    """Reconstruct v7 fields without importing v9 evidence into history."""
    if transition_item is not None:
        return list(
            transition_item["v7_public_context_supported_fields"]
        )
    audit = packet["_source_role_audit"]
    roles: set[str] = set()
    for source in audit.get("renderable_sources", []):
        if source.get("source_id") in (
            V8_ADDED_SOURCE_IDS | (later_source_ids or set())
        ):
            continue
        roles.update(source.get("assigned_roles", []))
    return [
        field
        for field, role in (
            ("source_event", "source_event_support"),
            ("date", "source_event_support"),
            ("historical_context", "historical_context_support"),
        )
        if role in roles and (field != "date" or _known(packet.get("date")))
    ]


def _context_line(public_reply_text: str) -> str:
    """Extract the first public Context section without altering it."""
    return public_reply_text.split("\n\n", 1)[0]


def _packet_without_later_sources(
    packet: dict[str, Any],
    source_ids: set[str],
    public_fields: list[str],
) -> dict[str, Any]:
    """Reconstruct a frozen projection without later curated sources."""
    reconstructed = copy.deepcopy(packet)
    audit = reconstructed["_source_role_audit"]
    for collection in ("curated_sources", "renderable_sources"):
        audit[collection] = [
            source
            for source in audit.get(collection, [])
            if source.get("source_id") not in source_ids
        ]
    audit["public_context_supported_fields"] = list(public_fields)
    return reconstructed


def _public_source_projection(rendered: dict[str, Any]) -> list[dict[str, Any]]:
    """Return concise public source identity fields used by this review."""
    return [
        {
            "claims_supported": list(source.get("claims_supported", [])),
            "title": _clean(source.get("title")),
            "url": _clean(source.get("url")),
        }
        for source in rendered.get("sources", [])
    ]


def _public_date_precision(
    packet: dict[str, Any], current_fields: list[str],
) -> str:
    """Classify the displayed precision of a date-only projection."""
    if current_fields != ["date"]:
        return "not_applicable"
    public_date = _v2_british_date(packet.get("date"))
    for precision, pattern in (
        ("day", _DAY_PRECISION_DATE),
        ("month", _MONTH_PRECISION_DATE),
        ("year", _YEAR_PRECISION_DATE),
    ):
        if pattern.fullmatch(public_date):
            return precision
    return "other"


def _base_presentation_flags(
    packet: dict[str, Any],
    current_fields: list[str],
    context_line: str,
    public_sources: list[dict[str, Any]],
) -> list[str]:
    """Classify deterministic presentation properties of one Context line."""
    flags: list[str] = []
    prefix = "Context — "
    body = context_line[len(prefix):] if context_line.startswith(prefix) else ""
    if not body:
        flags.append("empty_context")
    if body and _BARE_DATE.fullmatch(body.rstrip(".!?")):
        flags.append("bare_date_context")
    if (
        not context_line.startswith(prefix)
        or "\n" in context_line
        or not body
        or not context_line.endswith((".", "?", "!"))
    ):
        flags.append("malformed_context")
    if "date" not in current_fields and _DATE_LIKE.search(body):
        flags.append("unadmitted_date_in_context")
    if "/" in body:
        flags.append("diagnostic_slash_in_context")

    if current_fields == ["date"]:
        expected = (
            "Context — The surviving record dates this wording to "
            f"{_v2_british_date(packet.get('date'))}, but does not establish "
            "its occasion."
        )
        if context_line == expected:
            flags.append("safe_date_only_context")
        matching_titles = sorted({
            source["title"] for source in public_sources
            if _EVENT_LIKE_SOURCE_TITLE.search(source["title"])
        })
        if matching_titles:
            flags.append("source_title_event_tension_manual_hint")
    elif current_fields == ["source_event"]:
        if context_line == SAFE_EVENT_ONLY_FALLBACK:
            flags.append("safe_event_only_fallback")
        elif not {
            "empty_context", "malformed_context", "unadmitted_date_in_context",
            "diagnostic_slash_in_context",
        } & set(flags):
            flags.append("safe_event_only_context")
    elif current_fields == ["source_event", "date"]:
        flags.append("event_and_date_context")
    return sorted(flags)


def _duplicate_groups(
    records: list[dict[str, Any]], field: str,
) -> list[dict[str, Any]]:
    """Return deterministic duplicate-value groups for one record field."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for record in records:
        grouped[record[field]].append(record["quote_id"])
    return [
        {field: value, "quote_ids": sorted(quote_ids)}
        for value, quote_ids in sorted(grouped.items())
        if len(quote_ids) > 1
    ]


def _assert_review(review: dict[str, Any]) -> None:
    """Reject any result that no longer describes the reviewed transition."""
    failed = sorted(
        name for name, passed in review["invariants"].items() if not passed
    )
    if failed:
        raise RuntimeError(
            "public projection review invariants failed: " + ", ".join(failed)
        )


def build_review(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
) -> dict[str, Any]:
    """Build the deterministic, offline public projection review."""
    research_dir = research_dir.resolve()
    transition = _load_transition_manifest()
    transition_items = transition["items"]
    transition_ids = set(transition_items)
    post_v9_transition = _load_post_v9_transition_manifest()
    statecraft_transition = _load_post_v9_transition_manifest(
        STATECRAFT_TRANSITION_MANIFEST_PATH
    )
    mtf_transition = _load_post_v9_transition_manifest(
        MTF_TRANSITION_MANIFEST_PATH
    )
    packets, _ = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    curated = json.loads(
        (research_dir / "historical_context_source_curated_evidence.json")
        .read_text(encoding="utf-8")
    )
    independently_reviewed_ids = {
        quote_id
        for quote_id, item in curated.get("items", {}).items()
        if isinstance(item, dict)
        and any(
            str(source.get("evidence_origin") or "").startswith(
                "independently_reviewed_"
            )
            for source in item.get("sources", [])
            if isinstance(source, dict)
        )
    }
    independently_reviewed_ids -= set(MTF_REVIEWED_BINDINGS)
    current_field_map = {
        quote_id: list(
            packet["_source_role_audit"].get(
                "public_context_supported_fields", []
            )
        )
        for quote_id, packet in packets.items()
    }
    historical_v9_field_map = dict(current_field_map)
    post_v9_records: list[dict[str, Any]] = []
    statecraft_source_ids = {
        source_id for source_id, _candidate_id
        in STATECRAFT_REVIEWED_BINDINGS.values()
    }
    mtf_source_ids = {
        source_id for source_id, _candidate_id
        in MTF_REVIEWED_BINDINGS.values()
    }
    if mtf_transition is not None:
        if (
            _file_sha256(MTF_TRANSITION_MANIFEST_PATH)
            != MTF_TRANSITION_MANIFEST_SHA256
        ):
            raise RuntimeError("frozen MTF transition manifest differs")
        historical_v9_field_map, mtf_records = (
            _validate_post_v9_transition(
                mtf_transition,
                packets=packets,
                curated=curated,
                current_field_map=current_field_map,
                historical_transition_ids=transition_ids,
                expected_input_hashes=_post_v9_input_hashes(research_dir),
                expected_bindings=MTF_REVIEWED_BINDINGS,
                expected_baseline_source_count=(
                    MTF_BASELINE_CURATED_SOURCE_COUNT
                ),
                expected_baseline_source_ids_sha256=(
                    MTF_BASELINE_CURATED_SOURCE_IDS_SHA256
                ),
                expected_declared_baseline_fields={
                    quote_id: list(
                        item["v9_baseline_public_context_supported_fields"]
                    )
                    for quote_id, item in mtf_transition["items"].items()
                },
            )
        )
        post_v9_records.extend(mtf_records)
    if statecraft_transition is not None:
        historical_v9_field_map, statecraft_records = (
            _validate_post_v9_transition(
                statecraft_transition,
                packets=packets,
                curated=curated,
                current_field_map=historical_v9_field_map,
                historical_transition_ids=transition_ids,
                expected_input_hashes=statecraft_transition["input_hashes"],
                expected_bindings=STATECRAFT_REVIEWED_BINDINGS,
                expected_baseline_source_count=(
                    STATECRAFT_BASELINE_CURATED_SOURCE_COUNT
                ),
                expected_baseline_source_ids_sha256=(
                    STATECRAFT_BASELINE_CURATED_SOURCE_IDS_SHA256
                ),
                expected_declared_baseline_fields={
                    quote_id: [] for quote_id in STATECRAFT_REVIEWED_BINDINGS
                },
                later_source_ids=mtf_source_ids,
            )
        )
        post_v9_records.extend(statecraft_records)
    if post_v9_transition is not None:
        if (
            _file_sha256(POST_V9_TRANSITION_MANIFEST_PATH)
            != POST_V9_TRANSITION_MANIFEST_SHA256
        ):
            raise RuntimeError("frozen local-book transition manifest differs")
        historical_v9_field_map, local_book_records = (
            _validate_post_v9_transition(
                post_v9_transition,
                packets=packets,
                curated=curated,
                current_field_map=historical_v9_field_map,
                historical_transition_ids=transition_ids,
                expected_input_hashes=post_v9_transition["input_hashes"],
                expected_bindings=POST_V9_REVIEWED_BINDINGS,
                expected_baseline_source_count=(
                    POST_V9_BASELINE_CURATED_SOURCE_COUNT
                ),
                expected_baseline_source_ids_sha256=(
                    POST_V9_BASELINE_CURATED_SOURCE_IDS_SHA256
                ),
                later_source_ids=statecraft_source_ids | mtf_source_ids,
            )
        )
        post_v9_records.extend(local_book_records)
    post_v9_records.sort(key=lambda record: record["quote_id"])
    unchanged_field_map = {
        quote_id: fields
        for quote_id, fields in historical_v9_field_map.items()
        if quote_id not in transition_ids
        and quote_id not in STATECRAFT_NEW_COMPLETED_PACKET_IDS
    }
    transition_id_hash = hashlib.sha256("".join(
        f"{quote_id}\n" for quote_id in sorted(transition_ids)
    ).encode("utf-8")).hexdigest()
    if (
        CURRENT_POLICY != V9_POLICY
        or set(packets) != set(current_field_map)
        or len(packets) != 627
        or transition_ids != independently_reviewed_ids
        or len(transition_ids) != 12
        or transition_id_hash != transition.get(
            "transition_quote_ids_sha256"
        )
        or _canonical_json_sha256(unchanged_field_map)
        != transition.get("unchanged_614_public_field_map_sha256")
        or transition.get("counts") != {
            "completed_packets": 626,
            "reviewed_transition_packets": 12,
            "unchanged_packets": 614,
            "v8_to_v9_public_field_changes": 6,
        }
    ):
        raise RuntimeError("v8-to-v9 transition scope differs")

    records: list[dict[str, Any]] = []
    incremental_records: list[dict[str, Any]] = []
    post_v9_source_ids = {
        binding["source_id"]
        for record in post_v9_records
        for binding in record["source_bindings"]
    }
    post_v9_quote_ids = {
        record["quote_id"] for record in post_v9_records
    }
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        audit = packet["_source_role_audit"]
        if audit.get("policy_version") != V9_POLICY:
            raise RuntimeError(f"unexpected source-role policy for {quote_id}")
        transition_item = transition_items.get(quote_id)
        v7_fields = (
            list(historical_v9_field_map[quote_id])
            if (
                transition_item is None
                and quote_id in post_v9_quote_ids
                and quote_id not in MTF_REVIEWED_BINDINGS
            )
            else _v7_public_context_supported_fields(
                packet,
                transition_item,
                later_source_ids=post_v9_source_ids,
            )
        )
        v9_fields = list(historical_v9_field_map[quote_id])
        v8_fields = (
            list(transition_item["v8_public_context_supported_fields"])
            if transition_item is not None
            else list(v9_fields)
        )
        if transition_item is not None:
            if (
                transition_item.get("quote_text_sha256") != quote_id
                or list(
                    transition_item.get(
                        "v9_public_context_supported_fields", []
                    )
                )
                != v9_fields
            ):
                raise RuntimeError(
                    f"v9 transition record differs for {quote_id}"
                )
            if v8_fields != v9_fields:
                incremental_records.append({
                    "quote_id": quote_id,
                    "v8_public_context_supported_fields": v8_fields,
                    "v9_public_context_supported_fields": v9_fields,
                })
        if v7_fields == v9_fields:
            continue
        render_packet = (
            _packet_without_later_sources(packet, mtf_source_ids, v9_fields)
            if quote_id in MTF_REVIEWED_BINDINGS
            else packet
        )
        rendered = format_context_reply_public(render_packet)
        if rendered is None:
            raise RuntimeError(f"public formatter failed for {quote_id}")
        public_text = rendered["text"]
        context = _context_line(public_text)
        public_sources = _public_source_projection(rendered)
        records.append({
            "change_kind": (
                "downgraded" if len(v9_fields) < len(v7_fields) else "upgraded"
            ),
            "context_line": context,
            "date": _clean(packet.get("date")),
            "presentation_flags": _base_presentation_flags(
                packet, v9_fields, context, public_sources,
            ),
            "public_date_precision": _public_date_precision(packet, v9_fields),
            "public_reply_text": public_text,
            "public_sources": public_sources,
            "quote_id": quote_id,
            "quote_text": packet["quote_text"],
            "source_event": _clean(packet.get("source_event")),
            "v7_public_context_supported_fields": v7_fields,
            "v8_public_context_supported_fields": v8_fields,
            "v9_public_context_supported_fields": v9_fields,
        })

    downgraded = [row for row in records if row["change_kind"] == "downgraded"]
    upgraded = [row for row in records if row["change_kind"] == "upgraded"]
    duplicate_contexts = _duplicate_groups(downgraded, "context_line")
    duplicate_full_replies = _duplicate_groups(downgraded, "public_reply_text")
    duplicate_context_ids = {
        quote_id
        for group in duplicate_contexts
        for quote_id in group["quote_ids"]
    }
    duplicate_reply_ids = {
        quote_id
        for group in duplicate_full_replies
        for quote_id in group["quote_ids"]
    }
    for record in records:
        flags = set(record["presentation_flags"])
        if record["quote_id"] in duplicate_context_ids:
            flags.add("duplicate_context_line")
        if record["quote_id"] in duplicate_reply_ids:
            flags.add("duplicate_full_reply")
        record["presentation_flags"] = sorted(flags)

    for record in post_v9_records:
        quote_id = record["quote_id"]
        packet = packets[quote_id]
        current_fields = record["current_public_context_supported_fields"]
        rendered = format_context_reply_public(packet)
        if rendered is None:
            raise RuntimeError(
                f"post-v9 public formatter failed for {quote_id}"
            )
        public_text = rendered["text"]
        context = _context_line(public_text)
        public_sources = _public_source_projection(rendered)
        record.update({
            "context_line": context,
            "presentation_flags": _base_presentation_flags(
                packet, current_fields, context, public_sources,
            ),
            "public_reply_text": public_text,
            "public_sources": public_sources,
        })

    manual_hints = [
        {
            "kind": "source_title_event_tension",
            "promotes_public_fields": False,
            "public_source_titles": sorted({
                source["title"] for source in record["public_sources"]
                if _EVENT_LIKE_SOURCE_TITLE.search(source["title"])
            }),
            "quote_id": record["quote_id"],
            "recommended_action": "manual_evidence_review_only",
        }
        for record in downgraded
        if "source_title_event_tension_manual_hint"
        in record["presentation_flags"]
    ]
    flag_counts = Counter(
        flag for record in records for flag in record["presentation_flags"]
    )
    pattern_counts = Counter(
        (
            tuple(record["v7_public_context_supported_fields"]),
            tuple(record["v9_public_context_supported_fields"]),
        )
        for record in records
    )
    date_precision_counts = Counter(
        record["public_date_precision"] for record in downgraded
        if record["v9_public_context_supported_fields"] == ["date"]
    )
    by_id = {record["quote_id"]: record for record in records}
    blocker_flags = {
        "empty_context", "bare_date_context", "malformed_context",
        "unadmitted_date_in_context", "diagnostic_slash_in_context",
        "duplicate_full_reply",
    }
    invariants = {
        "change_count_is_72": len(records) == 72,
        "downgraded_count_is_66": len(downgraded) == 66,
        "upgraded_count_is_6": len(upgraded) == 6,
        "date_only_downgrade_count_is_64": pattern_counts[
            (("source_event", "date"), ("date",))
        ] == 64,
        "event_only_downgrade_count_is_2": pattern_counts[
            (("source_event", "date"), ("source_event",))
        ] == 2,
        "event_and_date_upgrade_count_is_6": pattern_counts[
            ((), ("source_event", "date"))
        ] == 6,
        "all_64_date_only_contexts_are_safe": flag_counts[
            "safe_date_only_context"
        ] == 64,
        "date_only_precision_is_59_day_3_month_2_year": (
            date_precision_counts
            == {"day": 59, "month": 3, "year": 2}
        ),
        "v8_to_v9_field_change_count_is_6": len(incremental_records) == 6,
        "post_v9_transition_is_disjoint": not (
            set(transition_items)
            & {record["quote_id"] for record in post_v9_records}
        ),
        "post_v9_projections_are_safe": all(
            not blocker_flags & set(record["presentation_flags"])
            and bool(record["public_reply_text"])
            and bool(record["public_sources"])
            for record in post_v9_records
        ),
        "b32_uses_safe_fallback": (
            by_id.get(B32_QUOTE_ID, {}).get("context_line")
            == SAFE_EVENT_ONLY_FALLBACK
            and all(
                marker not in by_id[B32_QUOTE_ID]["context_line"]
                for marker in ("1979", "1984", "/")
            )
        ),
        "clean_event_only_context_is_preserved": (
            by_id.get(CLEAN_EVENT_ONLY_QUOTE_ID, {}).get("context_line")
            == CLEAN_EVENT_ONLY_CONTEXT
        ),
        "document_104653_upgrade_is_correct": (
            by_id.get(DOCUMENT_104653_QUOTE_ID, {}).get("change_kind")
            == "upgraded"
            and by_id[DOCUMENT_104653_QUOTE_ID]["context_line"]
            == DOCUMENT_104653_CONTEXT
            and by_id[DOCUMENT_104653_QUOTE_ID][
                "v7_public_context_supported_fields"
            ] == []
            and by_id[DOCUMENT_104653_QUOTE_ID][
                "v9_public_context_supported_fields"
            ] == ["source_event", "date"]
        ),
        "no_blocking_presentation_flags": not any(
            blocker_flags & set(record["presentation_flags"])
            for record in records
        ),
        "no_duplicate_full_reply": not duplicate_full_replies,
        "manual_hint_count_is_9": len(manual_hints) == 9,
        "manual_hint_ids_match_reviewed_set": (
            {row["quote_id"] for row in manual_hints}
            == EXPECTED_MANUAL_HINT_IDS
        ),
        "manual_hints_do_not_promote_fields": all(
            row["promotes_public_fields"] is False for row in manual_hints
        ),
    }
    input_hashes = {
        AUDIT_FILENAME: _file_sha256(research_dir / AUDIT_FILENAME),
        "corpus_manifest.json": _file_sha256(
            research_dir / "corpus_manifest.json"
        ),
        "historical_context_formatter.py": _file_sha256(
            ROOT / "historical_context_formatter.py"
        ),
        "historical_context_public_projection_review.py": _file_sha256(
            Path(__file__).resolve()
        ),
        TRANSITION_MANIFEST_PATH.name: _file_sha256(
            TRANSITION_MANIFEST_PATH
        ),
        "research_packets.json": _file_sha256(
            research_dir / "research_packets.json"
        ),
    }
    if post_v9_transition is not None:
        input_hashes[POST_V9_TRANSITION_MANIFEST_PATH.name] = _file_sha256(
            POST_V9_TRANSITION_MANIFEST_PATH
        )
    if statecraft_transition is not None:
        input_hashes[STATECRAFT_TRANSITION_MANIFEST_PATH.name] = _file_sha256(
            STATECRAFT_TRANSITION_MANIFEST_PATH
        )
    if mtf_transition is not None:
        input_hashes[MTF_TRANSITION_MANIFEST_PATH.name] = _file_sha256(
            MTF_TRANSITION_MANIFEST_PATH
        )
    counts = {
        "change_count": len(records),
        "date_only_day_precision_count": date_precision_counts["day"],
        "date_only_month_precision_count": date_precision_counts["month"],
        "date_only_year_precision_count": date_precision_counts["year"],
        "downgraded_count": len(downgraded),
        "duplicate_context_group_count": len(duplicate_contexts),
        "duplicate_context_record_count": len(duplicate_context_ids),
        "duplicate_full_reply_group_count": len(duplicate_full_replies),
        "event_only_downgrade_count": pattern_counts[
            (("source_event", "date"), ("source_event",))
        ],
        "manual_hint_count": len(manual_hints),
        "post_v9_public_field_change_count": sum(
            record["v9_baseline_public_context_supported_fields"]
            != record["current_public_context_supported_fields"]
            for record in post_v9_records
        ),
        "post_v9_source_addition_count": sum(
            len(record["source_bindings"]) for record in post_v9_records
        ),
        "post_v9_transition_packet_count": len(post_v9_records),
        "v8_to_v9_public_field_change_count": len(incremental_records),
        "safe_date_only_count": flag_counts["safe_date_only_context"],
        "safe_event_only_context_count": flag_counts[
            "safe_event_only_context"
        ],
        "safe_event_only_fallback_count": flag_counts[
            "safe_event_only_fallback"
        ],
        "upgraded_count": len(upgraded),
    }
    review = {
        "audit_kind": REVIEW_KIND,
        "counts": counts,
        "downgraded_records": downgraded,
        "duplicate_context_groups": duplicate_contexts,
        "duplicate_full_reply_groups": duplicate_full_replies,
        "input_hashes": input_hashes,
        "interpretation": {
            "duplicate_context_lines": (
                "Shared dates legitimately reuse the deterministic date-only "
                "sentence; only duplicate full replies are blocking."
            ),
            "manual_hints": (
                "Source-title/event tensions are review hints only and never "
                "promote a public field without claim-specific evidence."
            ),
            "policy_transition": (
                "The cumulative v7-to-v9 projection uses the reviewed "
                "v8-to-v9 transition manifest for the twelve changed packets; "
                "the other 614 packets are hash-proven field-identical at the "
                "frozen v9 baseline. Any later reviewed evidence transition is "
                "hash-bound and reported separately."
            ),
        },
        "invariants": invariants,
        "incremental_v8_to_v9_records": incremental_records,
        "manual_hints": manual_hints,
        "policy_comparison": {
            "historical_baseline": V7_POLICY,
            "from": V8_POLICY,
            "to": V9_POLICY,
        },
        "post_v9_transition_records": post_v9_records,
        "review_ready": all(invariants.values()),
        "schema_version": REVIEW_SCHEMA_VERSION,
        "upgraded_records": upgraded,
    }
    _assert_review(review)
    return review


def _validate_output_path(
    research_dir: Path,
    output: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Allow replacement only of an existing artifact of this exact kind."""
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    research = research_dir.resolve()
    resolved = output.resolve(strict=False)
    if resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside the immutable research directory")
    root = Path(__file__).resolve().parent
    protected = {
        (root / name).resolve(strict=False)
        for name in (
            "historical_context_formatter.py",
            "historical_context_public_projection_review.py",
            "historical_context_v8_v9_transition_manifest.json",
            "historical_context_v9_local_book_evidence_transition_manifest.json",
            "historical_context_v9_mtf_corpus_evidence_transition_manifest.json",
            "historical_context_v9_statecraft_primary_transition_manifest.json",
            "historical_context_source_roles.py",
            "mrsMThatcher.txt",
            "quote_analysis.json",
        )
    }
    if resolved in protected:
        raise ValueError("--output resolves to a protected project input")
    if resolved.exists():
        if not overwrite:
            raise FileExistsError(
                "--output already exists; pass --overwrite only for a prior "
                "projection-review artifact"
            )
        try:
            existing = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "--overwrite target is not a prior projection-review artifact"
            ) from exc
        if (
            not isinstance(existing, dict)
            or existing.get("audit_kind") != REVIEW_KIND
            or existing.get("schema_version") not in {
                2, REVIEW_SCHEMA_VERSION,
            }
        ):
            raise ValueError(
                "--overwrite target is not a prior projection-review artifact"
            )
    return resolved


def main(argv: list[str] | None = None) -> int:
    """Write one explicit deterministic review artifact."""
    parser = argparse.ArgumentParser(
        description=(
            "Review frozen v7-to-v9 and later public historical-context "
            "projections"
        ),
    )
    parser.add_argument(
        "--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace only an existing artifact of this exact review kind",
    )
    args = parser.parse_args(argv)
    output = _validate_output_path(
        args.research_dir,
        args.output,
        overwrite=args.overwrite,
    )
    review = build_review(args.research_dir)
    atomic_write_json(output, review)
    print(json.dumps({
        "counts": review["counts"],
        "output": str(output),
        "review_ready": review["review_ready"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
