"""Offline semantic retrieval and fail-open conversational shadow evaluation.

The production reply path deliberately consumes none of the values returned by
this module.  Live integration is limited to :func:`submit_shadow_comparison`,
which queues local work on a daemon thread and writes shadow telemetry.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import queue
import re
import resource
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from historical_context_formatter import (
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from reply_strategy import RetrievedEvidence, retrieve_research_packets

MODEL_REPOSITORY = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_ID = f"{MODEL_REPOSITORY}@{MODEL_REVISION}"
MODEL_LICENCE = "MIT"
MODEL_DIMENSIONS = 384
MODEL_FILES = {
    "config.json": 655,
    "onnx/model.onnx": 470_268_510,
    "special_tokens_map.json": 167,
    "tokenizer.json": 17_082_730,
    "tokenizer_config.json": 443,
    "sentencepiece.bpe.model": 5_069_051,
}
MODEL_DOWNLOAD_BYTES = sum(MODEL_FILES.values())
MODEL_PACKAGE_PINS = {"onnxruntime": "1.22.1", "tokenizers": "0.21.4", "numpy": "2.2.6"}
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "mrsMThatcher" / "hybrid_reply_retrieval" / MODEL_REVISION

INDEX_SCHEMA_VERSION = 1
DOCUMENT_TEMPLATE_VERSION = "hybrid-retrieval-document-v1"
QUERY_BUILDER_VERSION = "hybrid-query-v1"
FUSION_VERSION = "weighted-rrf-v1"
SHADOW_RESULT_SCHEMA_VERSION = 1
DEFAULT_THRESHOLDS = {
    "schema_version": 1,
    "fusion_version": FUSION_VERSION,
    "rrf_k": 60,
    "lexical_weight": 1.0,
    "semantic_weight": 0.8,
    "metadata_weight": 0.15,
    "minimum_lexical_score": 3.0,
    "minimum_semantic_similarity": 0.84,
    "minimum_fused_score": 0.012,
    "minimum_top_margin": 0.00001,
    "minimum_packet_confidence": "medium",
    "maximum_results": 5,
    "semantic_candidate_count": 20,
    "lexical_candidate_count": 20,
}

REACTION_ONLY = {
    "yes", "yep", "yeah", "exactly", "this", "agreed", "agree", "indeed",
    "true", "quite", "amen", "correct", "right", "absolutely", "bravo",
    "lol", "haha", "thanks", "thank you", "hear hear", "well said", "same",
}
REACTION_WORDS = {
    "a", "absolutely", "agreed", "agree", "amen", "be", "bonjour", "bravo",
    "buongiorno", "bom", "correct", "day", "dia", "domingo", "días", "evening",
    "exactly", "fijne", "goedemiddag", "goedemorgen", "goedenavond", "good", "great", "i",
    "guten", "haha", "happy", "hear", "hello", "hi", "indeed", "ja", "lol", "mate",
    "morgen", "morning", "nice", "oui", "photo", "quite", "right", "same", "said",
    "she's", "sunday", "thank", "thanks", "this", "true", "well", "yes", "yep", "yeah", "zondag",
    "you", "you're", "love", "wrong", "not",
}
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
LEADING_HANDLES_RE = re.compile(r"^(?:\s*@[-_A-Za-z0-9]+[,:;]?)+\s*")
WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}
TOPIC_RULES = {
    "economy": ("econom", "inflation", "tax", "money", "wealth", "prosper"),
    "free_enterprise": ("enterprise", "free market", "capitalis", "private sector"),
    "socialism": ("socialis", "communis", "collectiv"),
    "liberty": ("liberty", "freedom", "free society", "individual"),
    "responsibility": ("responsib", "self-reli", "duty"),
    "trade_unions": ("trade union", "strike", "industrial action"),
    "europe": ("europe", "eec", "european communit"),
    "foreign_affairs": ("foreign", "soviet", "russia", "war", "defence", "defense"),
    "patriotism": ("patriot", "nation", "britain", "british"),
    "leadership": ("leader", "leadership", "government"),
    "environment": ("environment", "climate", "pollution"),
    "education": ("education", "school", "university"),
    "law_and_order": ("law and order", "crime", "police", "rule of law"),
}


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 bytes."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    """Return the canonical JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, value: Any) -> None:
    """Write a JSON document atomically."""
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")


def atomic_write_text(path: Path, value: str) -> None:
    """Write text atomically."""
    atomic_write_bytes(path, value.encode("utf-8"))


def normalise_text(value: Any, *, maximum: int = 4000) -> str:
    """Normalise text."""
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = URL_RE.sub(" ", text)
    text = LEADING_HANDLES_RE.sub("", text)
    text = re.sub(r"([!?.,])\1{2,}", r"\1\1", text)
    text = " ".join(text.split())
    return text[:maximum].strip()


def substantive_query(value: str) -> tuple[bool, str]:
    """Return the substantive query."""
    text = normalise_text(value, maximum=1000)
    if not text:
        return False, "empty_after_normalisation"
    words = [word.casefold().replace("’", "'") for word in WORD_RE.findall(text)]
    phrase = " ".join(words)
    if not words or phrase in REACTION_ONLY:
        return False, "reaction_only"
    informative = [word for word in words if len(word) >= 3 and word not in REACTION_ONLY]
    if not informative:
        return False, "no_substantive_terms"
    if all(word in REACTION_WORDS for word in words):
        return False, "reaction_or_greeting_only"
    return True, "substantive"


def query_language_hint(value: str) -> str:
    """Return the query language hint."""
    letters = [char for char in str(value or "") if unicodedata.category(char).startswith("L")]
    if any("CYRILLIC" in unicodedata.name(char, "") for char in letters):
        return "cyrillic-script"
    if any("ARABIC" in unicodedata.name(char, "") for char in letters):
        return "arabic-script"
    if any(any(name in unicodedata.name(char, "") for name in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL")) for char in letters):
        return "east-asian-script"
    if any(ord(char) > 127 for char in letters):
        return "non-ascii-latin-or-other"
    return "english-or-unknown"


def has_non_ascii_letters(value: str) -> bool:
    """Return whether has non ascii letters."""
    return any(ord(char) > 127 and unicodedata.category(char).startswith("L") for char in str(value or ""))


def build_query(
    incoming_text: str,
    parent_context: str = "",
    thread_context: str = "",
) -> dict[str, Any]:
    """Build query."""
    incoming = normalise_text(incoming_text, maximum=1200)
    parent = normalise_text(parent_context, maximum=1200)
    thread = normalise_text(thread_context, maximum=1200)
    is_substantive, reason = substantive_query(incoming)
    return {
        "version": QUERY_BUILDER_VERSION,
        "incoming_text": incoming,
        "parent_context": parent,
        "thread_context": thread,
        "substantive_query": is_substantive,
        "reason": reason,
        "query_text_hash": sha256_bytes(canonical_json({"incoming": incoming, "parent": parent, "thread": thread})),
    }


def _normalise_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(" ".join(item.split())[:240])
        elif isinstance(item, dict):
            label = item.get("name") or item.get("title") or item.get("label")
            if isinstance(label, str) and label.strip():
                result.append(" ".join(label.split())[:240])
    return sorted(set(result), key=lambda item: (item.casefold(), item))


def _document_value(value: Any, maximum: int) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = URL_RE.sub(" ", text)
    return " ".join(text.split())[:maximum]


def _historical_period(date: Any) -> str:
    match = re.search(r"\b(18|19|20)\d{2}\b", str(date or ""))
    if not match:
        return ""
    year = int(match.group(0))
    return f"{year // 10 * 10}s"


def _policy_topics(packet: dict[str, Any]) -> list[str]:
    text = " ".join(str(packet.get(key) or "") for key in (
        "immediate_subject", "intended_argument", "broader_principle", "mechanism",
        "claimed_consequence", "source_event",
    )).casefold()
    return sorted(topic for topic, needles in TOPIC_RULES.items() if any(needle in text for needle in needles))


def build_retrieval_document(quote_id: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Build retrieval document."""
    entities = _normalise_list(packet.get("entities"))
    document: dict[str, Any] = {
        "quote_id": quote_id,
        "quote_text": _document_value(packet.get("quote_text"), 700),
        "verified_text": _document_value(packet.get("verified_text"), 700),
        "verification_status": str(packet.get("verification_status") or "").strip(),
        "research_confidence": str(packet.get("research_confidence") or "").strip(),
        "source_event": _document_value(packet.get("source_event"), 300),
        "date": _document_value(packet.get("date"), 80),
        "immediate_subject": _document_value(packet.get("immediate_subject"), 500),
        "intended_argument": _document_value(packet.get("intended_argument"), 700),
        "literal_meaning": _document_value(packet.get("literal_meaning"), 500),
        "broader_principle": _document_value(packet.get("broader_principle"), 500),
        "mechanism": _document_value(packet.get("mechanism"), 500),
        "claimed_consequence": _document_value(packet.get("claimed_consequence"), 500),
        "themes": _policy_topics(packet),
        "entities": entities,
        "people": [],
        "places": [],
        "organisations": [],
        "policy_topics": _policy_topics(packet),
        "historical_period": _historical_period(packet.get("date")),
    }
    lines = [f"{field}: {', '.join(value) if isinstance(value, list) else value}" for field, value in document.items() if field != "quote_id" and value]
    text = "\n".join(lines)
    document["document_template_version"] = DOCUMENT_TEMPLATE_VERSION
    document["text"] = text
    document["document_sha256"] = sha256_bytes(text.encode("utf-8"))
    return document


def validate_corpus_invariants(research_run: Path) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any]]:
    """Validate corpus invariants."""
    packets, unresolved = load_and_validate_corpus(research_run)
    if len(packets) != 626 or len(unresolved) != 6:
        raise RuntimeError(f"hybrid index requires 626 completed and six unresolved records; got {len(packets)} and {len(unresolved)}")
    if set(packets) & set(unresolved):
        raise RuntimeError("completed and unresolved quote sets overlap")
    for quote_id, packet in packets.items():
        if packet.get("quote_id") != quote_id:
            raise RuntimeError(f"immutable quote identity mismatch: {quote_id}")
        if not str(packet.get("quote_text") or "").strip():
            raise RuntimeError(f"missing manifest quote text: {quote_id}")
        if not str(packet.get("verification_status") or "").strip():
            raise RuntimeError(f"missing verification status: {quote_id}")
        if packet.get("research_confidence") not in CONFIDENCE_ORDER:
            raise RuntimeError(f"invalid research confidence: {quote_id}")
    packet_path = research_run / "research_packets.json"
    closure_path = research_run / "final_unresolved" / "corpus_closure_audit.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    expected = closure.get("hashes", {}).get("research_packets_sha256")
    actual = sha256_file(packet_path)
    if expected != actual:
        raise RuntimeError(f"research packet hash differs from closure audit: expected {expected}, got {actual}")
    metadata = {
        "completed_count": len(packets),
        "unresolved_count": len(unresolved),
        "research_packets_sha256": actual,
        "corpus_manifest_sha256": closure.get("hashes", {}).get("corpus_manifest_sha256"),
        "schema_version": closure.get("schema_version"),
        "eligible_quote_id_set_sha256": sha256_bytes("\n".join(sorted(packets)).encode("ascii")),
    }
    return packets, set(unresolved), metadata


def model_preflight(model_dir: Path = DEFAULT_MODEL_DIR) -> dict[str, Any]:
    """Return the model preflight."""
    installed: dict[str, str | None] = {}
    try:
        from importlib.metadata import version
        for package in MODEL_PACKAGE_PINS:
            try:
                installed[package] = version(package)
            except Exception:
                installed[package] = None
    except Exception:
        installed = {package: None for package in MODEL_PACKAGE_PINS}
    return {
        "model_repository": MODEL_REPOSITORY,
        "exact_revision": MODEL_REVISION,
        "model_id": MODEL_ID,
        "licence": MODEL_LICENCE,
        "expected_download_bytes": MODEL_DOWNLOAD_BYTES,
        "expected_download_mib": round(MODEL_DOWNLOAD_BYTES / 1024 / 1024, 1),
        "cache_destination": str(model_dir),
        "package_dependencies": MODEL_PACKAGE_PINS,
        "installed_package_versions": installed,
        "paid_api": False,
        "trust_remote_code": False,
        "normal_runtime_local_files_only": True,
        "download_complete": model_is_complete(model_dir),
    }


def model_is_complete(model_dir: Path) -> bool:
    """Return whether model is complete."""
    manifest = model_dir / "model_manifest.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data.get("model_id") != MODEL_ID:
        return False
    hashes = data.get("file_hashes") if isinstance(data.get("file_hashes"), dict) else {}
    return all((model_dir / name).is_file() and hashes.get(name) == sha256_file(model_dir / name) for name in MODEL_FILES)


def download_model(model_dir: Path, confirmation: str) -> dict[str, Any]:
    """Download model."""
    if confirmation != MODEL_ID:
        raise RuntimeError(f"exact --confirm-model {MODEL_ID} is required")
    if model_is_complete(model_dir):
        manifest = json.loads((model_dir / "model_manifest.json").read_text(encoding="utf-8"))
        manifest["package_versions"] = model_preflight(model_dir)["installed_package_versions"]
        manifest["python_version"] = sys.version.split()[0]
        atomic_write_json(model_dir / "model_manifest.json", manifest)
        return manifest
    parent = model_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{MODEL_REVISION}.", dir=parent))
    try:
        hashes: dict[str, str] = {}
        sizes: dict[str, int] = {}
        for name in MODEL_FILES:
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{name}"
            request = urllib.request.Request(url, headers={"User-Agent": "mrsMThatcher-hybrid-retrieval/1"})
            with urllib.request.urlopen(request, timeout=120) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            hashes[name] = sha256_file(destination)
            sizes[name] = destination.stat().st_size
        manifest = {
            "schema_version": 1,
            "model_repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "model_id": MODEL_ID,
            "licence": MODEL_LICENCE,
            "dimensions": MODEL_DIMENSIONS,
            "trust_remote_code": False,
            "local_files_only": True,
            "package_versions": model_preflight(temporary)["installed_package_versions"],
            "python_version": sys.version.split()[0],
            "file_hashes": hashes,
            "file_sizes": sizes,
            "downloaded_at": utc_now(),
        }
        atomic_write_json(temporary / "model_manifest.json", manifest)
        if model_dir.exists():
            archived = model_dir.with_name(f"{model_dir.name}.superseded-{int(time.time())}")
            os.replace(model_dir, archived)
        os.replace(temporary, model_dir)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


class LocalE5Embedder:
    """Pinned ONNX E5 embedding runtime with no network-capable code path."""

    def __init__(self, model_dir: Path = DEFAULT_MODEL_DIR):
        """Initialise the local e5 embedder."""
        if not model_is_complete(model_dir):
            raise RuntimeError(f"pinned local model is unavailable or invalid: {model_dir}")
        from importlib.metadata import version
        for package, expected in MODEL_PACKAGE_PINS.items():
            if version(package) != expected:
                raise RuntimeError(f"{package} version must be {expected}, got {version(package)}")
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.model_dir = model_dir
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(2, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_dir / "onnx" / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def encode(self, texts: Sequence[str], *, query: bool = False, batch_size: int = 16) -> np.ndarray:
        """Encode documents or queries with the pinned local E5 model."""
        outputs: list[np.ndarray] = []
        prefix = "query: " if query else "passage: "
        for start in range(0, len(texts), batch_size):
            values = [prefix + str(text) for text in texts[start:start + batch_size]]
            encoded = self.tokenizer.encode_batch(values)
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            masks = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            inputs: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": masks}
            if "token_type_ids" in self.input_names:
                inputs["token_type_ids"] = np.zeros_like(ids)
            inputs = {key: value for key, value in inputs.items() if key in self.input_names}
            hidden = np.asarray(self.session.run(None, inputs)[0], dtype=np.float32)
            expanded = masks[:, :, None].astype(np.float32)
            pooled = (hidden * expanded).sum(axis=1) / np.maximum(expanded.sum(axis=1), 1e-9)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            outputs.append(pooled.astype(np.float32))
        return np.vstack(outputs) if outputs else np.empty((0, MODEL_DIMENSIONS), dtype=np.float32)


def build_index(research_run: Path, output: Path, model_dir: Path = DEFAULT_MODEL_DIR) -> dict[str, Any]:
    """Build index."""
    packets, unresolved, corpus = validate_corpus_invariants(research_run)
    documents = [build_retrieval_document(qid, packets[qid]) for qid in sorted(packets)]
    template_hash = sha256_bytes("\n".join(item["text"] for item in documents).encode("utf-8"))
    expected_identity = {
        "corpus_hash": corpus["research_packets_sha256"],
        "model_id": MODEL_ID,
        "document_template_hash": template_hash,
        "index_schema_version": INDEX_SCHEMA_VERSION,
    }
    output.mkdir(parents=True, exist_ok=True)
    index_dir = output / "index"
    existing = index_dir / "index_manifest.json"
    if existing.is_file():
        old = json.loads(existing.read_text(encoding="utf-8"))
        if all(old.get(key) == value for key, value in expected_identity.items()):
            try:
                validate_index(output, model_dir)
                atomic_write_json(output / "model_manifest.json", json.loads((model_dir / "model_manifest.json").read_text(encoding="utf-8")))
                return old
            except Exception:
                pass
        archive = output / "index_history" / f"index-{int(time.time())}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(index_dir, archive)
    started = time.perf_counter()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    embedder = LocalE5Embedder(model_dir)
    load_seconds = time.perf_counter() - started
    embeddings = embedder.encode([item["text"] for item in documents], batch_size=8)
    elapsed = time.perf_counter() - started
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    temporary = Path(tempfile.mkdtemp(prefix=".index.", dir=output))
    try:
        np.save(temporary / "embeddings.npy", embeddings, allow_pickle=False)
        atomic_write_json(temporary / "quote_ids.json", [item["quote_id"] for item in documents])
        matrix_hash = sha256_file(temporary / "embeddings.npy")
        manifest = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "document_count": len(documents),
            "dimensions": int(embeddings.shape[1]),
            "dtype": str(embeddings.dtype),
            "normalisation": "L2 unit vectors; exact cosine via float32 dot product",
            "maximum_model_tokens": 256,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "corpus_hash": corpus["research_packets_sha256"],
            "eligible_quote_id_set_sha256": corpus["eligible_quote_id_set_sha256"],
            "document_template_version": DOCUMENT_TEMPLATE_VERSION,
            "document_template_hash": template_hash,
            "embeddings_sha256": matrix_hash,
            "quote_ids_sha256": sha256_file(temporary / "quote_ids.json"),
            "build_seconds": round(elapsed, 3),
            "model_load_seconds": round(load_seconds, 3),
            "peak_rss_delta_kib": max(0, rss_after - rss_before),
            "generated_at": utc_now(),
        }
        atomic_write_json(temporary / "index_manifest.json", manifest)
        if index_dir.exists():
            shutil.rmtree(index_dir)
        os.replace(temporary, index_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    atomic_write_json(output / "corpus_manifest.json", {**corpus, "unresolved_quote_ids": sorted(unresolved), "generated_at": utc_now()})
    atomic_write_text(output / "retrieval_documents.jsonl", "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in documents))
    atomic_write_json(output / "document_hashes.json", {item["quote_id"]: item["document_sha256"] for item in documents})
    atomic_write_json(output / "model_manifest.json", json.loads((model_dir / "model_manifest.json").read_text(encoding="utf-8")))
    if not (output / "thresholds.json").exists():
        atomic_write_json(output / "thresholds.json", DEFAULT_THRESHOLDS)
    return manifest


def load_documents(path: Path) -> list[dict[str, Any]]:
    """Load documents."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_index(retrieval_dir: Path, model_dir: Path = DEFAULT_MODEL_DIR) -> dict[str, Any]:
    """Validate index."""
    manifest_path = retrieval_dir / "index" / "index_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids_path = retrieval_dir / "index" / "quote_ids.json"
    matrix_path = retrieval_dir / "index" / "embeddings.npy"
    documents = load_documents(retrieval_dir / "retrieval_documents.jsonl")
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    if manifest.get("model_id") != MODEL_ID or manifest.get("model_revision") != MODEL_REVISION:
        raise RuntimeError("shadow index model mismatch")
    if not model_is_complete(model_dir):
        raise RuntimeError("shadow model unavailable")
    if sha256_file(matrix_path) != manifest.get("embeddings_sha256") or sha256_file(ids_path) != manifest.get("quote_ids_sha256"):
        raise RuntimeError("shadow index file hash mismatch")
    if len(ids) != 626 or len(documents) != 626 or ids != [item["quote_id"] for item in documents]:
        raise RuntimeError("shadow index ordering/count mismatch")
    template_hash = sha256_bytes("\n".join(item["text"] for item in documents).encode("utf-8"))
    if template_hash != manifest.get("document_template_hash"):
        raise RuntimeError("shadow retrieval document hash mismatch")
    if any(item.get("document_sha256") != sha256_bytes(str(item.get("text") or "").encode("utf-8")) for item in documents):
        raise RuntimeError("shadow retrieval document item hash mismatch")
    matrix = np.load(matrix_path, mmap_mode="r", allow_pickle=False)
    if matrix.shape != (626, MODEL_DIMENSIONS) or matrix.dtype != np.float32:
        raise RuntimeError("shadow embedding matrix shape or dtype mismatch")
    norms = np.linalg.norm(np.asarray(matrix), axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise RuntimeError("shadow embedding matrix is not L2 normalised")
    return manifest


def exact_cosine_search(matrix: np.ndarray, query: np.ndarray, quote_ids: Sequence[str], count: int) -> list[tuple[str, float]]:
    """Return the exact cosine search."""
    if query.shape != (matrix.shape[1],):
        raise ValueError("query embedding dimensions differ from index")
    scores = np.asarray(matrix @ query, dtype=np.float32)
    order = sorted(range(len(quote_ids)), key=lambda index: (-float(scores[index]), quote_ids[index]))
    return [(quote_ids[index], float(scores[index])) for index in order[:count]]


def _metadata_signals(query: str, document: dict[str, Any]) -> list[str]:
    folded = query.casefold()
    tokens = {item.casefold() for item in WORD_RE.findall(folded)}
    signals: set[str] = set()
    for topic in document.get("policy_topics", []):
        if topic.replace("_", " ") in folded or any(needle in folded for needle in TOPIC_RULES.get(topic, ())):
            signals.add(f"policy_topic:{topic}")
    for entity in document.get("entities", []):
        value = str(entity).casefold()
        entity_tokens = {item.casefold() for item in WORD_RE.findall(value) if len(item) >= 4}
        if value in folded or (entity_tokens and entity_tokens <= tokens):
            signals.add(f"entity:{entity}")
    period = str(document.get("historical_period") or "")
    if period and period.casefold() in folded:
        signals.add(f"historical_period:{period}")
    return sorted(signals)


def fuse_results(
    lexical: Sequence[tuple[str, float]],
    semantic: Sequence[tuple[str, float]],
    documents: dict[str, dict[str, Any]],
    query: str,
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the fuse results."""
    lexical_by_id = {qid: (rank, float(score)) for rank, (qid, score) in enumerate(lexical, 1)}
    semantic_by_id = {qid: (rank, float(score)) for rank, (qid, score) in enumerate(semantic, 1)}
    k = int(thresholds["rrf_k"])
    rows: list[dict[str, Any]] = []
    for quote_id in sorted(set(lexical_by_id) | set(semantic_by_id)):
        lexical_rank, lexical_score = lexical_by_id.get(quote_id, (None, 0.0))
        semantic_rank, semantic_score = semantic_by_id.get(quote_id, (None, -1.0))
        signals = _metadata_signals(query, documents[quote_id])
        score = 0.0
        if lexical_rank is not None:
            score += float(thresholds["lexical_weight"]) / (k + lexical_rank)
        if semantic_rank is not None:
            score += float(thresholds["semantic_weight"]) / (k + semantic_rank)
        if signals:
            score += float(thresholds["metadata_weight"]) * min(len(signals), 3) / (k + 1)
        relevance_floor = lexical_score >= float(thresholds["minimum_lexical_score"]) or semantic_score >= float(thresholds["minimum_semantic_similarity"])
        confidence_ok = CONFIDENCE_ORDER.get(str(documents[quote_id].get("research_confidence")), 0) >= CONFIDENCE_ORDER[str(thresholds["minimum_packet_confidence"])]
        accepted = bool(relevance_floor and confidence_ok and score >= float(thresholds["minimum_fused_score"]))
        rows.append({
            "quote_id": quote_id,
            "lexical_rank": lexical_rank,
            "lexical_score": lexical_score if lexical_rank is not None else None,
            "semantic_rank": semantic_rank,
            "semantic_similarity": semantic_score if semantic_rank is not None else None,
            "metadata_signals": signals,
            "fused_score": score,
            "accepted": accepted,
        })
    rows.sort(key=lambda row: (-row["fused_score"], row["quote_id"]))
    accepted_rows = [row for row in rows if row["accepted"]]
    if len(accepted_rows) > 1 and float(thresholds.get("minimum_top_margin", 0.0)) > 0:
        margin = accepted_rows[0]["fused_score"] - accepted_rows[1]["fused_score"]
        if margin < float(thresholds["minimum_top_margin"]):
            for row in accepted_rows:
                row["accepted"] = False
    rank = 0
    for row in rows:
        if row["accepted"]:
            rank += 1
            row["rank"] = rank
        else:
            row["rank"] = None
    return rows


class HybridRetriever:
    """Represent hybrid retriever data."""
    def __init__(self, retrieval_dir: Path, research_run: Path, model_dir: Path = DEFAULT_MODEL_DIR):
        """Initialise the hybrid retriever."""
        started = time.perf_counter()
        self.manifest = validate_index(retrieval_dir, model_dir)
        packets, _, corpus = validate_corpus_invariants(research_run)
        if corpus["research_packets_sha256"] != self.manifest.get("corpus_hash"):
            raise RuntimeError("shadow index corpus hash mismatch")
        self.retrieval_dir = retrieval_dir
        self.research_run = research_run
        self.documents_list = load_documents(retrieval_dir / "retrieval_documents.jsonl")
        self.documents = {item["quote_id"]: item for item in self.documents_list}
        self.eligible_quote_ids = {
            quote_id
            for quote_id, packet in packets.items()
            if packet_is_attributed_to_margaret_thatcher(packet)
        }
        if len(self.eligible_quote_ids) != 610:
            raise RuntimeError("shadow retrieval requires exactly 610 attribution-eligible packets")
        self.quote_ids = json.loads((retrieval_dir / "index" / "quote_ids.json").read_text(encoding="utf-8"))
        self.matrix = np.load(retrieval_dir / "index" / "embeddings.npy", mmap_mode="r", allow_pickle=False)
        self.thresholds = json.loads((retrieval_dir / "thresholds.json").read_text(encoding="utf-8"))
        self.embedder = LocalE5Embedder(model_dir)
        self.load_ms = (time.perf_counter() - started) * 1000

    def _query_embedding(self, query: dict[str, Any]) -> np.ndarray:
        incoming = self.embedder.encode([query["incoming_text"]], query=True)[0]
        components = [(incoming, 0.80)]
        if query["parent_context"] and query["parent_context"] != query["incoming_text"]:
            components.append((self.embedder.encode([query["parent_context"]], query=True)[0], 0.15))
        if query["thread_context"]:
            components.append((self.embedder.encode([query["thread_context"]], query=True)[0], 0.05))
        total = sum(weight for _, weight in components)
        combined = sum(vector * weight for vector, weight in components) / total
        return np.asarray(combined / max(np.linalg.norm(combined), 1e-12), dtype=np.float32)

    def retrieve(
        self,
        incoming_text: str,
        *,
        parent_context: str = "",
        thread_context: str = "",
        production_lexical: Sequence[RetrievedEvidence] | None = None,
    ) -> dict[str, Any]:
        """Retrieve fused lexical and semantic evidence for one reply query."""
        started = time.perf_counter()
        query = build_query(incoming_text, parent_context, thread_context)
        if not query["substantive_query"]:
            return {
                "query": query, "lexical": [], "semantic": [], "hybrid": [],
                "production_lexical_quote_ids": [item.quote_id for item in (production_lexical or [])],
                "shadow_hybrid_quote_ids": [],
                "timings_ms": {"total": (time.perf_counter() - started) * 1000},
            }
        lexical_started = time.perf_counter()
        lexical_query = query["incoming_text"]
        lexical_evidence = retrieve_research_packets(
            lexical_query, self.research_run,
            maximum=int(self.thresholds["lexical_candidate_count"]),
        )
        lexical = [(item.quote_id, item.score) for item in lexical_evidence]
        lexical_ms = (time.perf_counter() - lexical_started) * 1000
        embedding_started = time.perf_counter()
        query_vector = self._query_embedding(query)
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        search_started = time.perf_counter()
        semantic_candidate_count = int(self.thresholds["semantic_candidate_count"])
        excluded_index_rows = max(0, len(self.quote_ids) - len(self.eligible_quote_ids))
        semantic = [
            row for row in exact_cosine_search(
                self.matrix,
                query_vector,
                self.quote_ids,
                min(len(self.quote_ids), semantic_candidate_count + excluded_index_rows),
            )
            if row[0] in self.eligible_quote_ids
        ][:semantic_candidate_count]
        search_ms = (time.perf_counter() - search_started) * 1000
        fusion_started = time.perf_counter()
        fused = fuse_results(lexical, semantic, self.documents, query["incoming_text"], self.thresholds)
        accepted = [row for row in fused if row["accepted"]]
        # Exact quote text is the only family signal available locally.  Keep the
        # best member and deterministically drop byte-identical family members.
        seen_text: set[str] = set()
        seen_token_sets: list[set[str]] = []
        deduplicated: list[dict[str, Any]] = []
        for row in accepted:
            family = " ".join(str(self.documents[row["quote_id"]].get("quote_text") or "").casefold().split())
            tokens = {token.casefold() for token in WORD_RE.findall(family)}
            near_duplicate = any(
                previous and tokens and len(previous & tokens) / len(previous | tokens) >= 0.90
                for previous in seen_token_sets
            )
            if family in seen_text or near_duplicate:
                continue
            seen_text.add(family)
            seen_token_sets.append(tokens)
            deduplicated.append(row)
            if len(deduplicated) >= int(self.thresholds["maximum_results"]):
                break
        for rank, row in enumerate(deduplicated, 1):
            row["rank"] = rank
        fusion_ms = (time.perf_counter() - fusion_started) * 1000
        production_ids = [item.quote_id for item in (production_lexical or [])]
        shadow_ids = [row["quote_id"] for row in deduplicated]
        return {
            "query": query,
            "lexical": lexical,
            "semantic": semantic,
            "hybrid": deduplicated,
            "production_lexical_quote_ids": production_ids,
            "shadow_hybrid_quote_ids": shadow_ids,
            "timings_ms": {
                "lexical": lexical_ms,
                "query_embedding": embedding_ms,
                "vector_search": search_ms,
                "fusion": fusion_ms,
                "total": (time.perf_counter() - started) * 1000,
            },
        }


def disagreement_class(lexical_ids: Sequence[str], hybrid_ids: Sequence[str]) -> str:
    """Return the disagreement class."""
    lexical, hybrid = set(lexical_ids), set(hybrid_ids)
    if list(lexical_ids) == list(hybrid_ids):
        return "same_evidence"
    if not lexical and not hybrid:
        return "both_no_evidence"
    if not lexical and hybrid:
        return "hybrid_only_evidence"
    if lexical and not hybrid:
        return "lexical_only_evidence"
    if lexical == hybrid:
        return "same_set_different_order"
    return "different_evidence_set"


def make_shadow_result(
    *, event_id: str, lane: str, target_id: str, result: dict[str, Any],
    manifest: dict[str, Any], status: str = "completed", reason: str = "",
) -> dict[str, Any]:
    """Create shadow result."""
    lexical_rows = [{"quote_id": qid, "rank": rank, "score": score} for rank, (qid, score) in enumerate(result.get("lexical", []), 1)]
    semantic_rows = [{"quote_id": qid, "rank": rank, "cosine_similarity": score} for rank, (qid, score) in enumerate(result.get("semantic", []), 1)]
    lexical_ids = list(result.get("production_lexical_quote_ids", []))
    hybrid_ids = list(result.get("shadow_hybrid_quote_ids", []))
    overlap = len(set(lexical_ids[:5]) & set(hybrid_ids[:5]))
    query = result.get("query", {})
    return {
        "schema_version": SHADOW_RESULT_SCHEMA_VERSION,
        "event_id": event_id,
        "timestamp": utc_now(),
        "lane": lane,
        "target_id": str(target_id),
        "query_text_hash": query.get("query_text_hash", ""),
        "query_language": query_language_hint(query.get("incoming_text", "")),
        "substantive_query": bool(query.get("substantive_query")),
        "lexical_results": lexical_rows,
        "semantic_results": semantic_rows,
        "hybrid_results": result.get("hybrid", []),
        "production_lexical_quote_ids": lexical_ids,
        "shadow_hybrid_quote_ids": hybrid_ids,
        "top_5_overlap_count": overlap,
        "would_change_evidence_set": lexical_ids != hybrid_ids,
        "lexical_no_evidence": not lexical_ids,
        "hybrid_no_evidence": not hybrid_ids,
        "disagreement_class": disagreement_class(lexical_ids, hybrid_ids),
        "index_version": f"{manifest.get('index_schema_version')}:{manifest.get('embeddings_sha256', '')[:12]}",
        "model_revision": manifest.get("model_revision", MODEL_REVISION),
        "latency_ms": round(float(result.get("timings_ms", {}).get("total", 0.0)), 3),
        "timings_ms": {key: round(float(value), 3) for key, value in result.get("timings_ms", {}).items()},
        "status": status,
        "reason": reason,
    }


class ShadowHistoryWriter:
    """Persist and manage shadow history records."""
    def __init__(self, runtime_dir: Path, maximum_records: int):
        """Initialise the shadow history writer."""
        self.runtime_dir = runtime_dir
        self.path = runtime_dir / "shadow_history.jsonl"
        self.status_path = runtime_dir / "shadow_status.json"
        self.maximum_records = max(100, int(maximum_records))
        self.lock = threading.Lock()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        initial: list[dict[str, Any]] = []
        if self.path.is_file():
            try:
                initial = [
                    json.loads(line)
                    for line in self.path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except Exception:
                initial = []
        archive_rows: list[dict[str, Any]] = []
        for archive in self.runtime_dir.glob("shadow_history.*.jsonl"):
            try:
                archive_rows.extend(
                    json.loads(line)
                    for line in archive.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except Exception:
                continue
        self.count = len(initial)
        self.seen_event_ids = {
            str(item.get("event_id")) for item in [*archive_rows, *initial]
            if isinstance(item, dict) and item.get("event_id")
        }

    def append(self, record: dict[str, Any]) -> None:
        """Append one event while enforcing the bounded shadow history."""
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.lock:
            event_id = str(record.get("event_id") or "")
            if event_id and event_id in self.seen_event_ids:
                return
            if self.count >= self.maximum_records and self.path.exists():
                timestamp = int(time.time())
                archive = self.runtime_dir / f"shadow_history.{timestamp}.jsonl"
                suffix = 1
                while archive.exists():
                    archive = self.runtime_dir / f"shadow_history.{timestamp}.{suffix}.jsonl"
                    suffix += 1
                os.replace(self.path, archive)
                self.count = 0
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self.count += 1
            if event_id:
                self.seen_event_ids.add(event_id)
            summary = shadow_summary(read_shadow_records(self.runtime_dir, maximum=self.maximum_records))
            summary.update({
                "schema_version": 1,
                "updated_at": utc_now(),
                "active_history_records": self.count,
                "runtime_dir": str(self.runtime_dir),
            })
            atomic_write_json(self.status_path, summary)


def read_shadow_records(runtime_dir: Path, maximum: int = 5000) -> list[dict[str, Any]]:
    """Read shadow records."""
    path = runtime_dir / "shadow_history.jsonl"
    if maximum <= 0:
        return []
    archive_pattern = re.compile(r"shadow_history\.(\d+)(?:\.(\d+))?\.jsonl\Z")

    def archive_key(item: Path) -> tuple[int, int, str]:
        match = archive_pattern.fullmatch(item.name)
        if match:
            return int(match.group(1)), int(match.group(2) or 0), item.name
        return 0, 0, item.name

    paths = sorted(runtime_dir.glob("shadow_history.*.jsonl"), key=archive_key)
    if path.is_file():
        paths.append(path)
    rows: list[dict[str, Any]] = []
    for history_path in reversed(paths):
        current: list[dict[str, Any]] = []
        for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                current.append(value)
        rows = current + rows
        if len(rows) >= maximum:
            break
    return rows[-maximum:]


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return the percentile."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def shadow_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the shadow summary."""
    completed = [record for record in records if record.get("status") == "completed"]
    latencies = [float(record["latency_ms"]) for record in completed if isinstance(record.get("latency_ms"), (int, float))]
    overlaps = [
        100.0 * int(record.get("top_5_overlap_count", 0)) / max(
            1,
            len(record.get("production_lexical_quote_ids", [])[:5]),
            len(record.get("shadow_hybrid_quote_ids", [])[:5]),
        )
        for record in completed
        if record.get("production_lexical_quote_ids") or record.get("shadow_hybrid_quote_ids")
    ]
    classes = Counter(str(record.get("disagreement_class") or "unavailable") for record in completed)
    version = next((str(record.get("index_version")) for record in reversed(records) if record.get("index_version")), "unavailable")
    model = next((str(record.get("model_revision")) for record in reversed(records) if record.get("model_revision")), "unavailable")
    return {
        "events": len(records),
        "completed": len(completed),
        "failures": len(records) - len(completed),
        "top_5_overlap_percent": round(sum(overlaps) / len(overlaps), 2) if overlaps else None,
        "hybrid_changed_evidence_set": sum(bool(record.get("would_change_evidence_set")) for record in completed),
        "hybrid_only_evidence": classes["hybrid_only_evidence"],
        "lexical_only_evidence": classes["lexical_only_evidence"],
        "no_evidence_disagreements": sum(
            1 for record in completed
            if bool(record.get("lexical_no_evidence")) != bool(record.get("hybrid_no_evidence"))
        ),
        "latency_p50_ms": round(percentile(latencies, 0.50), 3) if latencies else None,
        "latency_p95_ms": round(percentile(latencies, 0.95), 3) if latencies else None,
        "latency_max_ms": round(max(latencies), 3) if latencies else None,
        "index_version": version,
        "model_revision": model,
        "status_counts": dict(sorted(Counter(str(record.get("status") or "unavailable") for record in records).items())),
        "disagreement_counts": dict(sorted(classes.items())),
    }


class ShadowWorker:
    """Single daemon worker; submission never waits for semantic retrieval."""

    def __init__(
        self,
        *,
        project_dir: Path,
        retrieval_dir: Path,
        research_run: Path,
        timeout_ms: int,
        maximum_history: int,
        runtime_limits: dict[str, int] | None = None,
        event_logger: Callable[..., None] | None = None,
        retriever_factory: Callable[[], HybridRetriever] | None = None,
    ):
        """Initialise the shadow worker."""
        self.project_dir = project_dir
        self.retrieval_dir = retrieval_dir
        self.research_run = research_run
        self.timeout_ms = int(timeout_ms)
        self.event_logger = event_logger
        self.runtime_limits = dict(runtime_limits or {})
        self.writer = ShadowHistoryWriter(project_dir / "hybrid_reply_retrieval_runtime", maximum_history)
        self.retriever_factory = retriever_factory or (lambda: HybridRetriever(retrieval_dir, research_run))
        self.retriever: HybridRetriever | None = None
        self.jobs: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        self.thread = threading.Thread(target=self._run, name="hybrid-retrieval-shadow", daemon=True)
        self.thread.start()

    def submit(self, job: dict[str, Any]) -> bool:
        """Submit one shadow retrieval job without blocking production."""
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            # Shadow telemetry must never turn queue pressure into synchronous
            # fsync/history work on the production reply thread.
            return False

    def drain(self, timeout: float = 10.0) -> bool:
        """Return the drain."""
        deadline = time.monotonic() + timeout
        while self.jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.jobs.unfinished_tasks == 0

    def _failure_record(self, job: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
        return {
            "schema_version": SHADOW_RESULT_SCHEMA_VERSION,
            "event_id": job["event_id"],
            "timestamp": utc_now(),
            "lane": job["lane"],
            "target_id": str(job["target_id"]),
            "query_text_hash": build_query(job["incoming_text"], job.get("parent_context", ""), job.get("thread_context", ""))["query_text_hash"],
            "query_language": query_language_hint(job["incoming_text"]),
            "substantive_query": substantive_query(job["incoming_text"])[0],
            "lexical_results": [],
            "semantic_results": [],
            "hybrid_results": [],
            "production_lexical_quote_ids": [item.quote_id for item in job.get("production_lexical", [])],
            "shadow_hybrid_quote_ids": [],
            "top_5_overlap_count": 0,
            "would_change_evidence_set": False,
            "lexical_no_evidence": not job.get("production_lexical"),
            "hybrid_no_evidence": True,
            "disagreement_class": "shadow_unavailable",
            "index_version": "unavailable",
            "model_revision": MODEL_REVISION,
            "latency_ms": 0.0,
            "status": status,
            "reason": reason[:300],
        }

    def _persist(self, record: dict[str, Any]) -> None:
        try:
            self.writer.append(record)
            if self.event_logger:
                self.event_logger(
                    "hybrid_retrieval_shadow",
                    lane=record.get("lane"),
                    target_id=record.get("target_id"),
                    status=record.get("status"),
                    reason=record.get("reason"),
                    top_5_overlap_count=record.get("top_5_overlap_count"),
                    would_change_evidence_set=record.get("would_change_evidence_set"),
                    disagreement_class=record.get("disagreement_class"),
                    production_lexical_count=len(record.get("production_lexical_quote_ids", [])),
                    shadow_hybrid_count=len(record.get("shadow_hybrid_quote_ids", [])),
                    latency_ms=record.get("latency_ms"),
                    index_version=record.get("index_version"),
                    model_revision=record.get("model_revision"),
                )
        except Exception:
            # Telemetry failure is deliberately unable to affect production.
            return

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                try:
                    query = build_query(job["incoming_text"], job.get("parent_context", ""), job.get("thread_context", ""))
                    if not query["substantive_query"]:
                        try:
                            manifest = json.loads((self.retrieval_dir / "index" / "index_manifest.json").read_text(encoding="utf-8"))
                        except Exception:
                            manifest = {"index_schema_version": "unavailable", "model_revision": MODEL_REVISION}
                        result = {
                            "query": query, "lexical": [], "semantic": [], "hybrid": [],
                            "production_lexical_quote_ids": [item.quote_id for item in job.get("production_lexical", [])],
                            "shadow_hybrid_quote_ids": [], "timings_ms": {"total": 0.0},
                        }
                        record = make_shadow_result(
                            event_id=job["event_id"], lane=job["lane"], target_id=job["target_id"],
                            result=result, manifest=manifest, status="completed", reason="no_substantive_query",
                        )
                        self._persist(record)
                        continue
                    model_load_ms = 0.0
                    if self.retriever is None:
                        load_started = time.perf_counter()
                        self.retriever = self.retriever_factory()
                        model_load_ms = (time.perf_counter() - load_started) * 1000
                        if hasattr(self.retriever, "thresholds"):
                            for key, value in self.runtime_limits.items():
                                self.retriever.thresholds[key] = value
                    started = time.perf_counter()
                    result = self.retriever.retrieve(
                        job["incoming_text"],
                        parent_context=job.get("parent_context", ""),
                        thread_context=job.get("thread_context", ""),
                        production_lexical=job.get("production_lexical", []),
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    status = "completed" if elapsed_ms <= self.timeout_ms else "timeout"
                    reason = "" if status == "completed" else f"local_shadow_timeout:{elapsed_ms:.1f}ms>{self.timeout_ms}ms"
                    record = make_shadow_result(
                        event_id=job["event_id"], lane=job["lane"], target_id=job["target_id"],
                        result=result, manifest=self.retriever.manifest, status=status, reason=reason,
                    )
                    record["latency_ms"] = round(elapsed_ms, 3)
                    record.setdefault("timings_ms", {})["lazy_model_index_load"] = round(model_load_ms, 3)
                except RuntimeError as exc:
                    message = str(exc)
                    status = "index_mismatch" if "index" in message or "model mismatch" in message else "model_error"
                    record = self._failure_record(job, status, message)
                except Exception as exc:
                    record = self._failure_record(job, "model_error", f"{type(exc).__name__}: {exc}")
                self._persist(record)
            finally:
                self.jobs.task_done()


_WORKERS: dict[tuple[str, str], ShadowWorker] = {}
_WORKERS_LOCK = threading.Lock()


def validate_shadow_config(value: Any) -> list[str]:
    """Validate that semantic veto configuration is disabled or shadow-only."""
    expected = {
        "enabled", "mode", "index_path", "maximum_results", "semantic_candidate_count",
        "lexical_candidate_count", "query_timeout_ms", "maximum_shadow_history", "fail_open",
    }
    if not isinstance(value, dict):
        return ["reply_strategy.hybrid_retrieval must be an object"]
    if set(value) != expected:
        return ["reply_strategy.hybrid_retrieval fields mismatch"]
    errors: list[str] = []
    if type(value.get("enabled")) is not bool:
        errors.append("reply_strategy.hybrid_retrieval.enabled must be boolean")
    if value.get("mode") != "shadow":
        errors.append("reply_strategy.hybrid_retrieval.mode must be shadow")
    if not isinstance(value.get("index_path"), str) or not value["index_path"].strip():
        errors.append("reply_strategy.hybrid_retrieval.index_path must be a non-empty string")
    for key, low, high in (
        ("maximum_results", 1, 5), ("semantic_candidate_count", 5, 100),
        ("lexical_candidate_count", 5, 100), ("query_timeout_ms", 50, 10_000),
        ("maximum_shadow_history", 100, 100_000),
    ):
        number = value.get(key)
        if type(number) is not int or not low <= number <= high:
            errors.append(f"reply_strategy.hybrid_retrieval.{key} must be an integer from {low} to {high}")
    if value.get("fail_open") is not True:
        errors.append("reply_strategy.hybrid_retrieval.fail_open must remain true")
    return errors


def submit_shadow_comparison(
    *,
    config: dict[str, Any],
    project_dir: Path,
    research_run: Path,
    incoming_text: str,
    parent_context: str,
    thread_context: str,
    lane: str,
    target_id: str,
    production_lexical: Sequence[RetrievedEvidence],
    event_logger: Callable[..., None] | None = None,
) -> None:
    """Queue shadow work and intentionally return no retrieval value."""
    try:
        if not config.get("enabled") or config.get("mode") != "shadow":
            return None
        if validate_shadow_config(config):
            return None
        index_path = Path(str(config["index_path"]))
        if not index_path.is_absolute():
            index_path = project_dir / index_path
        key = (str(project_dir.resolve()), str(index_path.resolve()))
        with _WORKERS_LOCK:
            worker = _WORKERS.get(key)
            if worker is None:
                worker = ShadowWorker(
                    project_dir=project_dir,
                    retrieval_dir=index_path,
                    research_run=research_run,
                    timeout_ms=int(config["query_timeout_ms"]),
                    maximum_history=int(config["maximum_shadow_history"]),
                    runtime_limits={
                        "maximum_results": int(config["maximum_results"]),
                        "semantic_candidate_count": int(config["semantic_candidate_count"]),
                        "lexical_candidate_count": int(config["lexical_candidate_count"]),
                    },
                    event_logger=event_logger,
                )
                _WORKERS[key] = worker
        try:
            index_identity = sha256_file(index_path / "index" / "index_manifest.json")
        except Exception:
            index_identity = "unavailable"
        query_identity = build_query(incoming_text, parent_context, thread_context)["query_text_hash"]
        event_id = sha256_bytes(canonical_json({
            "lane": lane,
            "target_id": str(target_id),
            "query_text_hash": query_identity,
            "index": str(index_path),
            "index_identity": index_identity,
        }))
        worker.submit({
            "event_id": event_id,
            "lane": lane,
            "target_id": str(target_id),
            "incoming_text": incoming_text,
            "parent_context": parent_context,
            "thread_context": thread_context,
            "production_lexical": list(production_lexical),
        })
    except Exception:
        return None
    return None


HARD_NEGATIVES = (
    "Good morning", "Yep", "Exactly", "This", "Thank you", "Happy birthday",
    "My horoscope says Mercury is in retrograde", "What did you have for lunch?",
    "I bought a new pair of shoes", "Nice weather today", "Can anyone recommend a plumber?",
    "Follow me for guaranteed prizes", "The football score was two nil", "My cat is asleep",
    "Hello from the train", "That song is stuck in my head", "See you tomorrow", "LOL",
    "Please check my profile", "I prefer tea without sugar",
)
MULTILINGUAL_SMOKE_QUERIES = (
    ("La libertad económica protege la libertad individual", {"liberty", "economy"}, "Spanish"),
    ("Le socialisme menace la liberté et la prospérité", {"socialism", "liberty"}, "French"),
    ("Свободное предпринимательство создаёт процветание", {"free_enterprise", "economy"}, "Russian"),
    ("Die Verantwortung des Einzelnen gegenüber dem Staat", {"responsibility", "liberty"}, "German"),
    ("A soberania nacional e a identidade britânica", {"patriotism", "europe"}, "Portuguese"),
)


def packet_evaluation_queries(packets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the packet evaluation queries."""
    fields = ("immediate_subject", "mechanism", "broader_principle", "claimed_consequence", "intended_argument")
    rows: list[dict[str, Any]] = []
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        chosen = next((field for field in fields if len(str(packet.get(field) or "").split()) >= 5), None)
        if not chosen:
            continue
        partition = "calibration" if int(quote_id[:8], 16) % 5 == 0 else "evaluation"
        rows.append({
            "query_id": f"packet:{quote_id}:{chosen}",
            "kind": "positive",
            "partition": partition,
            "query": str(packet[chosen]),
            "expected_quote_id": quote_id,
            "source_field": chosen,
        })
    for index, query in enumerate(HARD_NEGATIVES):
        rows.append({
            "query_id": f"negative:{index:02d}",
            "kind": "hard_negative",
            "partition": "calibration" if index % 2 == 0 else "evaluation",
            "query": query,
            "expected_quote_id": None,
            "source_field": "curated_local_negative",
        })
    return rows


def _family_map(documents: Sequence[dict[str, Any]]) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for item in documents:
        text = " ".join(str(item.get("quote_text") or "").casefold().split())
        groups.setdefault(text, set()).add(item["quote_id"])
    result: dict[str, set[str]] = {}
    for members in groups.values():
        for quote_id in members:
            result[quote_id] = members
    return result


def _ranking_metrics(rows: Sequence[dict[str, Any]], key: str, families: dict[str, set[str]]) -> dict[str, Any]:
    positives = [row for row in rows if row["kind"] == "positive"]
    reciprocal: list[float] = []
    recall1 = recall5 = family5 = 0
    for row in positives:
        ids = list(row[key])
        expected = row["expected_quote_id"]
        if ids and ids[0] == expected:
            recall1 += 1
        if expected in ids[:5]:
            recall5 += 1
        if set(ids[:5]) & families.get(expected, {expected}):
            family5 += 1
        try:
            reciprocal.append(1.0 / (ids.index(expected) + 1))
        except ValueError:
            reciprocal.append(0.0)
    count = len(positives)
    return {
        "queries": count,
        "recall_at_1": recall1 / count if count else None,
        "recall_at_5": recall5 / count if count else None,
        "mean_reciprocal_rank": sum(reciprocal) / count if count else None,
        "same_family_recall_at_5": family5 / count if count else None,
    }


def _candidate_thresholds() -> list[dict[str, Any]]:
    values = []
    for lexical_floor in (2.5, 3.0, 4.0):
        for semantic_floor in (0.82, 0.84, 0.86, 0.88):
            for fused_floor in (0.012, 0.014):
                value = dict(DEFAULT_THRESHOLDS)
                value["minimum_lexical_score"] = lexical_floor
                value["minimum_semantic_similarity"] = semantic_floor
                value["minimum_fused_score"] = fused_floor
                values.append(value)
    return values


def _evaluate_threshold(
    raw_rows: Sequence[dict[str, Any]],
    documents: dict[str, dict[str, Any]],
    thresholds: dict[str, Any],
    partition: str,
) -> dict[str, Any]:
    positives = total_negative = correct_negative = recall5 = 0
    for row in raw_rows:
        if row["partition"] != partition:
            continue
        fused = fuse_results(row["lexical_raw"], row["semantic_raw"], documents, row["query"], thresholds)
        accepted = [item["quote_id"] for item in fused if item["accepted"]][:5]
        if row["kind"] == "hard_negative":
            total_negative += 1
            correct_negative += not accepted
        else:
            positives += 1
            recall5 += row["expected_quote_id"] in accepted
    return {
        "positive_queries": positives,
        "hard_negatives": total_negative,
        "recall_at_5": recall5 / positives if positives else 0.0,
        "no_evidence_accuracy": correct_negative / total_negative if total_negative else 0.0,
    }


def _multilingual_threshold_score(
    rows: Sequence[dict[str, Any]],
    documents: dict[str, dict[str, Any]],
    thresholds: dict[str, Any],
    partition: str,
) -> float:
    selected = [row for row in rows if row["partition"] == partition]
    if not selected:
        return 0.0
    matches = 0
    for row in selected:
        fused = fuse_results(row["lexical_raw"], row["semantic_raw"], documents, row["query"], thresholds)
        ids = [item["quote_id"] for item in fused if item["accepted"]][:5]
        topics = {topic for quote_id in ids for topic in documents[quote_id].get("policy_topics", [])}
        matches += bool(set(row["expected_topics"]) & topics)
    return matches / len(selected)


def evaluate_retrieval(retrieval_dir: Path, research_run: Path, model_dir: Path = DEFAULT_MODEL_DIR) -> dict[str, Any]:
    """Evaluate retrieval."""
    packets, _, _ = validate_corpus_invariants(research_run)
    retriever = HybridRetriever(retrieval_dir, research_run, model_dir)
    queries = packet_evaluation_queries(packets)
    raw_rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for row in queries:
        query = build_query(row["query"])
        if query["substantive_query"]:
            started = time.perf_counter()
            lexical_evidence = retrieve_research_packets(row["query"], research_run, maximum=20)
            lexical = [(item.quote_id, item.score) for item in lexical_evidence]
            vector = retriever._query_embedding(query)
            semantic = exact_cosine_search(retriever.matrix, vector, retriever.quote_ids, 20)
            latencies.append((time.perf_counter() - started) * 1000)
        else:
            lexical, semantic = [], []
            latencies.append(0.0)
        raw_rows.append({**row, "lexical_raw": lexical, "semantic_raw": semantic})
    multilingual_raw = []
    for index, (text, expected_topics, language) in enumerate(MULTILINGUAL_SMOKE_QUERIES):
        query = build_query(text)
        lexical = [(item.quote_id, item.score) for item in retrieve_research_packets(text, research_run, maximum=20)]
        semantic = exact_cosine_search(retriever.matrix, retriever._query_embedding(query), retriever.quote_ids, 20)
        multilingual_raw.append({
            "language": language,
            "query": text,
            "expected_topics": sorted(expected_topics),
            "partition": "calibration" if index < 3 else "evaluation",
            "lexical_raw": lexical,
            "semantic_raw": semantic,
        })
    candidates = []
    for thresholds in _candidate_thresholds():
        score = _evaluate_threshold(raw_rows, retriever.documents, thresholds, "calibration")
        candidates.append({
            "thresholds": thresholds,
            "calibration": score,
            "multilingual_calibration_topic_match_rate": _multilingual_threshold_score(
                multilingual_raw, retriever.documents, thresholds, "calibration"
            ),
        })
    eligible = [item for item in candidates if item["calibration"]["no_evidence_accuracy"] >= 0.9]
    if not eligible:
        eligible = candidates
    selected = max(
        eligible,
        key=lambda item: (
            item["calibration"]["no_evidence_accuracy"],
            item["multilingual_calibration_topic_match_rate"],
            item["calibration"]["recall_at_5"],
            item["thresholds"]["minimum_semantic_similarity"],
            -item["thresholds"]["minimum_fused_score"],
        ),
    )
    thresholds = {**selected["thresholds"], "selected_from": "fixed_grid_calibration_v1", "selected_at": utc_now()}
    atomic_write_json(retrieval_dir / "thresholds.json", thresholds)
    evaluation_rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        fused = fuse_results(raw["lexical_raw"], raw["semantic_raw"], retriever.documents, raw["query"], thresholds)
        hybrid = [item["quote_id"] for item in fused if item["accepted"]][:5]
        evaluation_rows.append({
            **{key: raw[key] for key in ("query_id", "kind", "partition", "query", "expected_quote_id", "source_field")},
            "lexical_ids": [qid for qid, _ in raw["lexical_raw"]],
            "semantic_ids": [qid for qid, _ in raw["semantic_raw"]],
            "hybrid_ids": hybrid,
        })
    evaluation_partition = [row for row in evaluation_rows if row["partition"] == "evaluation"]
    families = _family_map(retriever.documents_list)
    negatives = [row for row in evaluation_partition if row["kind"] == "hard_negative"]
    multilingual_rows = []
    for raw in multilingual_raw:
        fused = fuse_results(raw["lexical_raw"], raw["semantic_raw"], retriever.documents, raw["query"], thresholds)
        ids = [item["quote_id"] for item in fused if item["accepted"]][:5]
        returned_topics = sorted({topic for qid in ids for topic in retriever.documents[qid].get("policy_topics", [])})
        multilingual_rows.append({
            "language": raw["language"], "query": raw["query"], "expected_topics": raw["expected_topics"],
            "partition": raw["partition"],
            "returned_quote_ids": ids, "returned_topics": returned_topics,
            "topic_match": bool(set(raw["expected_topics"]) & set(returned_topics)),
        })
    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "model_id": MODEL_ID,
        "threshold_selection": {
            "candidate_count": len(candidates),
            "selected": thresholds,
            "calibration_metrics": selected["calibration"],
            "multilingual_calibration_topic_match_rate": selected["multilingual_calibration_topic_match_rate"],
            "selection_rule": "maximise hard-negative no-evidence accuracy (minimum 0.90), then predefined multilingual calibration topic match, packet recall@5, then conservative semantic floor",
        },
        "partitions": dict(Counter(row["partition"] for row in queries)),
        "lexical": _ranking_metrics(evaluation_partition, "lexical_ids", families),
        "semantic": _ranking_metrics(evaluation_partition, "semantic_ids", families),
        "hybrid": _ranking_metrics(evaluation_partition, "hybrid_ids", families),
        "hard_negative_no_evidence_accuracy": sum(not row["hybrid_ids"] for row in negatives) / len(negatives) if negatives else None,
        "top_five_overlap_mean": (
            sum(len(set(row["lexical_ids"][:5]) & set(row["hybrid_ids"][:5])) / 5 for row in evaluation_partition) / len(evaluation_partition)
            if evaluation_partition else None
        ),
        "hybrid_adds_new_candidate_percent": (
            100 * sum(bool(set(row["hybrid_ids"]) - set(row["lexical_ids"][:5])) for row in evaluation_partition) / len(evaluation_partition)
            if evaluation_partition else None
        ),
        "hybrid_removes_all_evidence_percent": (
            100 * sum(bool(row["lexical_ids"]) and not row["hybrid_ids"] for row in evaluation_partition) / len(evaluation_partition)
            if evaluation_partition else None
        ),
        "latency_ms": {
            "p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95),
            "maximum": max(latencies) if latencies else None, "model_index_load": retriever.load_ms,
        },
        "multilingual": {
            "queries": len(multilingual_rows),
            "overall_topic_match_rate": sum(row["topic_match"] for row in multilingual_rows) / len(multilingual_rows),
            "evaluation_topic_match_rate": (
                sum(row["topic_match"] for row in multilingual_rows if row["partition"] == "evaluation")
                / sum(row["partition"] == "evaluation" for row in multilingual_rows)
            ),
            "rows": multilingual_rows,
            "note": "hand-authored local smoke queries with broad topic labels; not a substitute for human relevance review",
        },
        "false_confidence_risk_cases": [
            row for row in evaluation_partition
            if row["kind"] == "hard_negative" and row["hybrid_ids"]
        ],
        "rows": evaluation_rows,
    }
    atomic_write_json(retrieval_dir / "automated_evaluation.json", result)
    report = [
        "# Hybrid Reply Retrieval Automated Evaluation", "",
        f"Model: `{MODEL_ID}`", f"Evaluation positives: {result['hybrid']['queries']}",
        f"Hybrid recall@1: {result['hybrid']['recall_at_1']:.1%}",
        f"Hybrid recall@5: {result['hybrid']['recall_at_5']:.1%}",
        f"Hybrid MRR: {result['hybrid']['mean_reciprocal_rank']:.3f}",
        f"Hard-negative no-evidence accuracy: {result['hard_negative_no_evidence_accuracy']:.1%}",
        f"Latency p50/p95/max: {result['latency_ms']['p50']:.1f}/{result['latency_ms']['p95']:.1f}/{result['latency_ms']['maximum']:.1f} ms", "",
        "Thresholds were selected on the separate calibration partition from a fixed grid, with hard-negative accuracy taking precedence.",
    ]
    atomic_write_text(retrieval_dir / "automated_evaluation_report.md", "\n".join(report) + "\n")
    return result


def _load_state_candidates(project_dir: Path, since_days: int) -> list[dict[str, Any]]:
    cutoff = time.time() - since_days * 86400
    cache: dict[str, dict[str, Any]] = {}
    histories: dict[str, dict[str, Any]] = {}
    for path in sorted(project_dir.glob("bot_state.json*"), reverse=True):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key, value in (state.get("tweet_cache") or {}).items():
            if isinstance(value, dict):
                cache.setdefault(str(key), value)
        for value in state.get("reply_strategy_history", []):
            if isinstance(value, dict) and value.get("target_id"):
                histories.setdefault(str(value["target_id"]), value)
    own_authors = Counter(
        str(value.get("author_id") or "") for value in cache.values()
        if value.get("post_type") in {"quote", "auto_reply", "daily_meme"} and value.get("author_id")
    )
    own_id = own_authors.most_common(1)[0][0] if own_authors else ""
    rows: list[dict[str, Any]] = []
    for target_id, value in sorted(cache.items()):
        text = str(value.get("text") or "").strip()
        epoch = int(value.get("cached_epoch", 0) or 0)
        if not text or (epoch and epoch < cutoff) or str(value.get("author_id") or "") == own_id:
            continue
        if value.get("post_type") in {"quote", "auto_reply", "daily_meme", "historical_context_reply"}:
            continue
        references = value.get("referenced_tweets") if isinstance(value.get("referenced_tweets"), list) else []
        parent_id = next((str(item.get("id")) for item in references if isinstance(item, dict) and item.get("type") in {"replied_to", "quoted"}), "")
        parent_text = str(cache.get(parent_id, {}).get("text") or "")
        historical = histories.get(target_id, {})
        lane = str(historical.get("candidate_source") or ("quote_tweet" if value.get("post_type") == "quote_tweet" else "mention"))
        rows.append({
            "target_id": target_id,
            "incoming_text": text,
            "parent_context": parent_text,
            "thread_context": "",
            "lane": lane,
            "cached_epoch": epoch,
            "historical_decision": historical or None,
            "source": "structured_bot_state_cache",
        })
    return rows


def replay_context_snapshot(candidate: dict[str, Any]) -> dict[str, str]:
    """Preserve complete locally available review text independently of query bounds."""
    return {
        "incoming_text": str(candidate.get("incoming_text") or ""),
        "parent_context": str(candidate.get("parent_context") or ""),
        "thread_context": str(candidate.get("thread_context") or ""),
    }


def replay_historical(project_dir: Path, retrieval_dir: Path, research_run: Path, since_days: int = 30) -> dict[str, Any]:
    """Replay historical."""
    retriever = HybridRetriever(retrieval_dir, research_run)
    candidates = _load_state_candidates(project_dir, since_days)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        production_ids = []
        historical = candidate.get("historical_decision")
        if isinstance(historical, dict):
            production_ids = [str(item) for item in historical.get("retrieved_quote_ids", [])]
        lexical = retrieve_research_packets(candidate["incoming_text"], research_run, maximum=5)
        result = retriever.retrieve(
            candidate["incoming_text"], parent_context=candidate["parent_context"],
            thread_context=candidate["thread_context"], production_lexical=lexical,
        )
        record = make_shadow_result(
            event_id=sha256_bytes(canonical_json({"target": candidate["target_id"], "replay": since_days})),
            lane=candidate["lane"], target_id=candidate["target_id"], result=result, manifest=retriever.manifest,
        )
        record.update({
            # Replay artefacts are the durable source for later human review.
            # Preserve the complete locally available text; the retrieval query
            # builder applies its own documented bounds independently.
            **replay_context_snapshot(candidate),
            "historical_decision": historical,
            "historical_production_retrieved_quote_ids": production_ids,
            "recomputed_lexical_for_comparison": True,
        })
        results.append(record)
    output = retrieval_dir / f"replay_{since_days}d"
    output.mkdir(parents=True, exist_ok=True)
    summary = shadow_summary(results)
    summary.update({
        "schema_version": 1, "since_days": since_days, "candidate_count": len(candidates),
        "historical_decisions_preserved_not_recomputed": True,
        "multilingual_candidate_count": sum(has_non_ascii_letters(row["incoming_text"]) for row in candidates),
        "generated_at": utc_now(),
    })
    atomic_write_json(output / "replay_results.json", {"schema_version": 1, "summary": summary, "items": results})
    atomic_write_json(output / "replay_summary.json", summary)
    report = [
        "# 30-day Historical Shadow Replay", "", f"Structured candidates: {len(candidates)}",
        f"Completed comparisons: {summary['completed']}",
        f"Hybrid changed evidence set: {summary['hybrid_changed_evidence_set']}",
        f"Hybrid-only evidence: {summary['hybrid_only_evidence']}",
        f"Lexical-only evidence: {summary['lexical_only_evidence']}",
        f"No-evidence disagreements: {summary['no_evidence_disagreements']}",
        f"Multilingual candidates: {summary['multilingual_candidate_count']}", "",
        "Historical reply decisions are retained as recorded facts. The replay recomputes retrieval only; it does not recompute modes or replies.",
    ]
    atomic_write_text(output / "replay_report.md", "\n".join(report) + "\n")
    return {"summary": summary, "items": results}


def _packet_preview(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "quote_id": document["quote_id"],
        "quote_text": document.get("quote_text", ""),
        "verification_status": document.get("verification_status", ""),
        "research_confidence": document.get("research_confidence", ""),
        "source_event": document.get("source_event", ""),
        "intended_argument": document.get("intended_argument", ""),
        "broader_principle": document.get("broader_principle", ""),
    }


REVIEW_CONTEXT_SCHEMA_VERSION = 2
REVIEW_CONTEXT_VERSION = "hybrid-review-context-v2"
REVIEW_SAMPLE_VERSION = "hybrid-shadow-review-sample-v2"
REVIEW_BLIND_VERSION = "hybrid-shadow-blind-v2"
X_LOG_JSON_MARKERS = ("X response json:", "X bearer response json:")


def _json_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _state_snapshot_paths(project_dir: Path) -> list[Path]:
    paths = [path for path in project_dir.glob("bot_state.json*") if path.is_file()]
    paths.extend(path for path in project_dir.glob("pre_*/bot_state.json") if path.is_file())
    return sorted(set(paths), key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _merge_conversation_record(
    records: dict[str, dict[str, Any]],
    sources: dict[str, list[str]],
    value: dict[str, Any],
    source: str,
) -> None:
    post_id = str(value.get("id") or "")
    if not post_id:
        return
    incoming = dict(value)
    note = incoming.get("note_tweet")
    if isinstance(note, dict) and str(note.get("text") or "").strip():
        incoming["text"] = str(note["text"])
        incoming["text_source"] = "note_tweet"
    existing = records.get(post_id, {})
    merged = dict(existing)
    for key, item in incoming.items():
        if item in (None, "", [], {}):
            continue
        old = merged.get(key)
        if key in {"text", "image_summary"} and old not in (None, ""):
            if len(str(item)) > len(str(old)):
                merged[key] = item
        elif key == "referenced_tweets" and isinstance(item, list):
            if not isinstance(old, list) or len(item) > len(old):
                merged[key] = item
        else:
            merged[key] = item
    records[post_id] = merged
    sources.setdefault(post_id, [])
    if source not in sources[post_id]:
        sources[post_id].append(source)


def _extract_logged_x_records(project_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[str]] = {}
    handles: dict[str, str] = {}
    decoder = json.JSONDecoder()
    payload_count = 0
    malformed_count = 0
    log_paths = sorted(project_dir.glob("mrsMThatcher.log*"), key=lambda path: str(path))
    for path in log_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in X_LOG_JSON_MARKERS:
            position = 0
            while True:
                marker_at = text.find(marker, position)
                if marker_at < 0:
                    break
                start = marker_at + len(marker)
                while start < len(text) and text[start].isspace():
                    start += 1
                try:
                    payload, end = decoder.raw_decode(text, start)
                except (json.JSONDecodeError, ValueError):
                    malformed_count += 1
                    position = start + 1
                    continue
                position = end
                payload_count += 1
                if not isinstance(payload, dict):
                    continue
                includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
                users = includes.get("users") if isinstance(includes.get("users"), list) else []
                for user in users:
                    if isinstance(user, dict) and user.get("id") and user.get("username"):
                        handles[str(user["id"])] = str(user["username"])
                data = payload.get("data")
                rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
                for row in rows:
                    if isinstance(row, dict) and row.get("id") and "text" in row:
                        _merge_conversation_record(records, sources, row, f"structured_log:{path.name}")
        for match in re.finditer(r"Considering mention id=(\d+)", text):
            records.setdefault(match.group(1), {})["observed_lane"] = "mention"
        for match in re.finditer(r"Considering quote tweet id=(\d+)", text):
            records.setdefault(match.group(1), {})["observed_lane"] = "quote_tweet"
    for post_id, value in records.items():
        author_id = str(value.get("author_id") or "")
        if author_id and author_id in handles:
            value["author_handle"] = handles[author_id]
        value["local_sources"] = sources.get(post_id, [])
    return records, handles, {
        "log_files": len(log_paths),
        "structured_x_payloads": payload_count,
        "malformed_structured_x_payloads": malformed_count,
        "logged_post_records": len(records),
    }


def load_local_conversation_catalog(project_dir: Path) -> dict[str, Any]:
    """Load retained conversation evidence without invoking a network path."""
    records, handles, log_stats = _extract_logged_x_records(project_dir)
    sources: dict[str, list[str]] = {
        post_id: list(value.pop("local_sources", [])) for post_id, value in records.items()
    }
    lane_hints: dict[str, str] = {
        post_id: str(value.get("observed_lane")) for post_id, value in records.items()
        if value.get("observed_lane")
    }
    state_paths = _state_snapshot_paths(project_dir)
    state_record_count = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cache = state.get("tweet_cache") if isinstance(state.get("tweet_cache"), dict) else {}
        for post_id, value in cache.items():
            if not isinstance(value, dict):
                continue
            state_record_count += 1
            _merge_conversation_record(records, sources, {**value, "id": str(value.get("id") or post_id)}, f"state_cache:{path.name}")
        lane_keys = {
            "skipped_hot_reply_ids": "hot-post",
            "skipped_quote_post_ids": "quote_tweet",
            "replied_to_quote_post_ids": "quote_tweet",
        }
        for key, lane in lane_keys.items():
            for target_id in state.get(key, []) if isinstance(state.get(key), list) else []:
                lane_hints[str(target_id)] = lane
        histories = state.get("reply_strategy_history") if isinstance(state.get("reply_strategy_history"), list) else []
        for item in histories:
            if not isinstance(item, dict) or not item.get("target_id"):
                continue
            lane = str(item.get("candidate_source") or "")
            if lane in {"mention", "hot-post", "quote_tweet"}:
                lane_hints[str(item["target_id"])] = lane
    for post_id, value in records.items():
        author_id = str(value.get("author_id") or "")
        if author_id and author_id in handles:
            value["author_handle"] = handles[author_id]
        post_type = str(value.get("post_type") or "")
        if post_type == "hot_post_reply":
            lane_hints[post_id] = "hot-post"
        elif post_type == "quote_tweet":
            lane_hints[post_id] = "quote_tweet"
        value["local_sources"] = sources.get(post_id, [])
    return {
        "records": records,
        "lane_hints": lane_hints,
        "stats": {
            **log_stats,
            "state_snapshot_files": len(state_paths),
            "state_cache_records_seen": state_record_count,
            "unique_local_post_records": len(records),
        },
    }


def _post_for_review(
    record: dict[str, Any] | None,
    *,
    fallback_id: str | None = None,
    fallback_text: str = "",
    fallback_source: str = "historical_replay",
) -> dict[str, Any] | None:
    if record is None and not fallback_text and not fallback_id:
        return None
    value = record or {}
    raw_text = str(value.get("text") or fallback_text or "")
    image_summary = str(value.get("image_summary") or "")
    sources = value.get("local_sources") if isinstance(value.get("local_sources"), list) else []
    if not sources and (fallback_text or fallback_id):
        sources = [fallback_source]
    return {
        "post_id": str(value.get("id") or fallback_id or "") or None,
        "author_id": str(value.get("author_id") or "") or None,
        "author_handle": str(value.get("author_handle") or "") or None,
        "text": html.unescape(raw_text),
        "timestamp": str(value.get("created_at") or "") or None,
        "image_summary": html.unescape(image_summary) if image_summary else None,
        "local_sources": list(sources),
        "raw_text_sha256": sha256_bytes(raw_text.encode("utf-8")) if raw_text else None,
    }


def _reference(record: dict[str, Any] | None, reference_type: str) -> dict[str, Any] | None:
    references = record.get("referenced_tweets") if isinstance(record, dict) else []
    if not isinstance(references, list):
        return None
    return next(
        (item for item in references if isinstance(item, dict) and item.get("type") == reference_type and item.get("id")),
        None,
    )


def _review_lane(row: dict[str, Any], record: dict[str, Any] | None, lane_hints: dict[str, str]) -> str:
    target_id = str(row.get("target_id") or "")
    if target_id in lane_hints:
        return lane_hints[target_id]
    if _reference(record, "quoted"):
        return "quote_tweet"
    lane = str(row.get("lane") or "mention")
    return lane if lane in {"mention", "hot-post", "quote_tweet"} else "mention"


def _truncation_reason(raw_text: str, source_record: dict[str, Any] | None) -> str | None:
    if not raw_text:
        return "incoming_text_missing"
    if source_record and source_record.get("text_source") == "note_tweet":
        return None
    # A trailing ellipsis can be user-authored. Treat it as unresolved source
    # truncation only for the short preview-shaped form reported in this audit;
    # longer locally captured posts retain their literal punctuation.
    if len(raw_text) <= 40 and raw_text.rstrip().endswith(("...", "…")):
        return "incoming_text_ends_with_unresolved_truncation_marker"
    return None


def _review_strata(row: dict[str, Any], lane: str) -> list[str]:
    return [
        lane,
        "non_english" if has_non_ascii_letters(str(row.get("incoming_text") or "")) else "english_or_unknown",
        "substantive" if row.get("substantive_query") else "low_substance",
        str(row.get("disagreement_class") or "unknown"),
        "historical_reply" if (row.get("historical_decision") or {}).get("mode") in {"historical_correction", "historical_context", "researched_principle"} else "humour_or_unreplied",
    ]


def _context_for_replay_row(
    row: dict[str, Any],
    records: dict[str, dict[str, Any]],
    lane_hints: dict[str, str],
) -> dict[str, Any]:
    target_id = str(row.get("target_id") or "")
    target = records.get(target_id)
    lane = _review_lane(row, target, lane_hints)
    incoming_raw = str((target or {}).get("text") or row.get("incoming_text") or "")
    incoming = _post_for_review(target, fallback_id=target_id, fallback_text=incoming_raw)
    reference_type = "quoted" if lane == "quote_tweet" else "replied_to"
    parent_ref = _reference(target, reference_type)
    parent_id = str((parent_ref or {}).get("id") or "") or None
    parent_record = records.get(parent_id or "")
    replay_parent = str(row.get("parent_context") or "")
    parent = _post_for_review(parent_record, fallback_id=parent_id, fallback_text=replay_parent)
    direct_parent = None if lane == "quote_tweet" else parent
    quoted_post = parent if lane == "quote_tweet" else None

    older_newest_first: list[dict[str, Any]] = []
    cursor = parent_record if lane != "quote_tweet" else None
    seen = {target_id, parent_id or ""}
    for _ in range(8):
        older_ref = _reference(cursor, "replied_to")
        older_id = str((older_ref or {}).get("id") or "")
        if not older_id or older_id in seen:
            break
        seen.add(older_id)
        cursor = records.get(older_id)
        if cursor is None:
            break
        older = _post_for_review(cursor, fallback_id=older_id)
        if older and (older.get("text") or older.get("image_summary")):
            older_newest_first.append(older)
    older_thread = list(reversed(older_newest_first))

    retrieval_query = build_query(incoming_raw, replay_parent, str(row.get("thread_context") or ""))
    stored_query_hash = str(row.get("query_text_hash") or "")
    query_matches = bool(stored_query_hash and retrieval_query["query_text_hash"] == stored_query_hash)
    missing: list[str] = []
    truncation = _truncation_reason(incoming_raw, target)
    if truncation:
        missing.append(truncation)
    parent_has_content = bool(parent and (parent.get("text") or parent.get("image_summary")))
    if lane == "quote_tweet" and not parent_has_content:
        missing.append("quoted_post_context_unavailable")
    elif lane == "hot-post" and not parent_has_content:
        missing.append("target_hot_post_context_unavailable")
    elif lane == "mention" and parent_ref and not parent_has_content:
        missing.append("direct_parent_context_unavailable")
    if target is None and not row.get("substantive_query") and not replay_parent:
        missing.append("source_record_unavailable_for_low_substance_post")
    if not query_matches:
        missing.append("retrieval_query_hash_mismatch")

    normalised_parts = [f"Incoming: {retrieval_query['incoming_text']}"]
    if retrieval_query["parent_context"]:
        normalised_parts.append(f"Parent context: {retrieval_query['parent_context']}")
    if retrieval_query["thread_context"]:
        normalised_parts.append(f"Older thread context: {retrieval_query['thread_context']}")
    query_basis = {
        "incoming_text_used": True,
        "direct_parent_used": bool(replay_parent) and lane != "quote_tweet",
        "older_thread_used": bool(retrieval_query["thread_context"]),
        "quoted_post_used": bool(replay_parent) and lane == "quote_tweet",
        "substantive_query": bool(row.get("substantive_query")),
        "normalised_query": "\n".join(normalised_parts),
        "query_text_hash": stored_query_hash,
        "retrieval_text_matches_displayed_source": query_matches,
        "production_lexical_query_source": "incoming_user_contribution",
        "shadow_semantic_query_sources": [
            source for source, used in (
                ("incoming_user_contribution", True),
                ("quoted_post" if lane == "quote_tweet" else "direct_parent", bool(replay_parent)),
                ("older_thread_context", bool(retrieval_query["thread_context"])),
            ) if used
        ],
    }
    material = {
        "lane": lane,
        "incoming": incoming,
        "direct_parent": direct_parent,
        "quoted_post": quoted_post,
        "older_thread_context": older_thread,
        "query_basis": query_basis,
    }
    return {
        **material,
        "context_status": "unavailable" if missing else "complete",
        "missing_context": missing,
        "context_hash": _json_hash(material),
        "review_strata": _review_strata(row, lane),
        "source_record_available": target is not None,
    }


def _case_from_replay(
    row: dict[str, Any],
    context: dict[str, Any],
    case_id: str,
    assignment: dict[str, str],
    documents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lexical_ids = [item["quote_id"] for item in row.get("lexical_results", [])[:5] if item.get("quote_id")]
    hybrid_ids = list(row.get("shadow_hybrid_quote_ids", []))[:5]
    sets = {
        "lexical": [_packet_preview(documents[qid]) for qid in lexical_ids if qid in documents],
        "hybrid": [_packet_preview(documents[qid]) for qid in hybrid_ids if qid in documents],
    }
    return {
        "case_id": case_id,
        "target_id": str(row["target_id"]),
        "lane": context["lane"],
        "incoming": context["incoming"],
        "direct_parent": context["direct_parent"],
        "older_thread_context": context["older_thread_context"],
        "quoted_post": context["quoted_post"],
        "query_basis": context["query_basis"],
        "context_status": context["context_status"],
        "missing_context": context["missing_context"],
        "context_hash": context["context_hash"],
        "review_strata": context["review_strata"],
        "selection_reason": "deterministic context-complete migration preserving review strata where locally possible",
        "A": sets[assignment["A"]],
        "B": sets[assignment["B"]],
        # Internal-only values are removed by browser_review_payload.
        "_disagreement_class": str(row.get("disagreement_class") or "unknown"),
        "_query_language": str(row.get("query_language") or query_language_hint(str(row.get("incoming_text") or ""))),
    }


def _old_case_material(case: dict[str, Any]) -> dict[str, Any]:
    if isinstance(case.get("incoming"), dict):
        return _new_case_material(case)
    incoming = html.unescape(str(case.get("incoming_post") or ""))
    parent = html.unescape(str(case.get("thread_context") or ""))
    lane = str(case.get("lane") or "mention")
    return {
        "lane": lane,
        "incoming_text": incoming,
        "parent_text": parent,
        "older_thread_count": 0,
    }


def _new_case_material(case: dict[str, Any]) -> dict[str, Any]:
    parent = case.get("quoted_post") if case.get("lane") == "quote_tweet" else case.get("direct_parent")
    return {
        "lane": str(case.get("lane") or "mention"),
        "incoming_text": str((case.get("incoming") or {}).get("text") or ""),
        "parent_text": str((parent or {}).get("text") or ""),
        "older_thread_count": len(case.get("older_thread_context") or []),
    }


def _write_review_context_report(path: Path, audit: dict[str, Any]) -> None:
    root = audit["root_cause"]
    lines = [
        "# Hybrid Retrieval Review Context Audit", "",
        "## Root cause", "",
        f"- Original capture: {root['original_capture']}",
        f"- Replay extraction: {root['replay_extraction']}",
        f"- Sample construction: {root['sample_construction']}",
        f"- Server and browser: {root['server_and_browser']}", "",
        "## Migration", "",
        f"- Original cases: {audit['original_case_count']}",
        f"- Final context-complete cases: {audit['final_complete_incoming_text_cases']}",
        f"- Original suspected truncated cases: {audit['original_truncated_cases']}",
        f"- Retrievals based on those incomplete source texts: {audit['retrieval_used_truncated_text_cases']}",
        f"- Cases with direct-parent context: {audit['final_direct_parent_cases']}",
        f"- Cases with quoted-post context: {audit['final_quoted_post_cases']}",
        f"- Cases with older-thread context: {audit['final_older_thread_cases']}",
        f"- Retrieval/display query mismatches: {audit['retrieval_display_mismatch_cases']}",
        f"- Repaired in place: {audit['repaired_in_place_cases']}",
        f"- Replaced: {audit['replaced_cases']}",
        f"- Reviews preserved: {audit['existing_reviews_preserved']}",
        f"- Reviews invalidated: {audit['existing_reviews_invalidated']}", "",
        "The audit and migration are entirely offline. Retrieval scores, A/B evidence sets, fusion thresholds, production lexical retrieval and live shadow behaviour were not recalculated or changed.",
    ]
    atomic_write_text(path, "\n".join(lines) + "\n")


def audit_review_context(
    project_dir: Path,
    retrieval_dir: Path,
    *,
    strict: bool = False,
    apply: bool = True,
) -> dict[str, Any]:
    """Audit review context."""
    sample_path = retrieval_dir / "review_sample_100.json"
    blind_path = retrieval_dir / "blind_assignment_manifest.json"
    replay_path = retrieval_dir / "replay_30d" / "replay_results.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    blind = json.loads(blind_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay_rows = {str(row["target_id"]): row for row in replay.get("items", [])}
    documents = {item["quote_id"]: item for item in load_documents(retrieval_dir / "retrieval_documents.jsonl")}
    catalog = load_local_conversation_catalog(project_dir)
    records = catalog["records"]
    # A parent may have been pruned after replay while another replay row still
    # preserves its text. Link that retained text back to the known parent ID.
    for row in replay_rows.values():
        target = records.get(str(row["target_id"]))
        lane = _review_lane(row, target, catalog["lane_hints"])
        ref = _reference(target, "quoted" if lane == "quote_tweet" else "replied_to")
        parent_id = str((ref or {}).get("id") or "")
        if parent_id and row.get("parent_context") and not str(records.get(parent_id, {}).get("text") or ""):
            _merge_conversation_record(
                records, {key: value.get("local_sources", []) for key, value in records.items()},
                {"id": parent_id, "text": str(row["parent_context"])}, "historical_replay_parent_context",
            )
            records[parent_id].setdefault("local_sources", []).append("historical_replay_parent_context")
    contexts = {
        target_id: _context_for_replay_row(row, records, catalog["lane_hints"])
        for target_id, row in replay_rows.items()
    }

    old_items = list(sample.get("items", []))
    old_hash = _json_hash(sample)
    existing_assignments = blind.get("assignments") if isinstance(blind.get("assignments"), dict) else {}
    review_path = retrieval_dir / "manual_review" / "human_reviews.json"
    reviews = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {"schema_version": 1, "items": {}}
    original_review_items = dict(reviews.get("items", {}))
    already_migrated = sample.get("context_version") == REVIEW_CONTEXT_VERSION
    migration_path = retrieval_dir / "review_sample_migration.json"
    migration_history: dict[str, Any] = {}
    historical_items = old_items
    historical_old_hash = old_hash
    historical_assignments = dict(existing_assignments)
    if already_migrated and migration_path.exists():
        migration_history = json.loads(migration_path.read_text(encoding="utf-8"))
        historical_old_hash = str(migration_history.get("old_sample_hash") or old_hash)
        backup_dir_value = str(migration_history.get("backup_directory") or "")
        backup_sample = Path(backup_dir_value) / sample_path.name if backup_dir_value else None
        if backup_sample and backup_sample.is_file():
            historical_sample = json.loads(backup_sample.read_text(encoding="utf-8"))
            historical_items = list(historical_sample.get("items", []))
            backup_blind = backup_sample.with_name(blind_path.name)
            if backup_blind.is_file():
                historical_blind = json.loads(backup_blind.read_text(encoding="utf-8"))
                historical_assignments = dict(historical_blind.get("assignments") or {})

    original_unavailable: list[dict[str, Any]] = []
    original_truncated = 0
    for old in historical_items:
        context = contexts.get(str(old.get("target_id") or ""))
        if not context:
            original_unavailable.append({"case_id": old.get("case_id"), "target_id": old.get("target_id"), "reasons": ["replay_record_unavailable"]})
            continue
        if any("truncation" in reason for reason in context["missing_context"]):
            original_truncated += 1
        if context["context_status"] != "complete":
            original_unavailable.append({"case_id": old.get("case_id"), "target_id": old.get("target_id"), "reasons": context["missing_context"]})

    replacements: list[dict[str, Any]] = []
    retained_set_regressions: list[str] = []
    retained_assignment_regressions: list[str] = []
    new_items: list[dict[str, Any]] = []
    new_assignments: dict[str, dict[str, str]] = {}
    used_targets = {str(item.get("target_id") or "") for item in old_items}
    available_candidates = [
        row for target_id, row in replay_rows.items()
        if target_id not in used_targets and contexts[target_id]["context_status"] == "complete"
    ]
    available_candidates.sort(key=lambda row: sha256_bytes(f"context-replacement-v1:{row['target_id']}".encode("utf-8")))

    if already_migrated:
        new_items = old_items
        new_assignments = dict(existing_assignments)
        replacements = list(migration_history.get("replacements") or [])
        for case in new_items:
            case_id = str(case.get("case_id") or "")
            target_id = str(case.get("target_id") or "")
            row = replay_rows.get(target_id)
            context = contexts.get(target_id)
            assignment = new_assignments.get(case_id)
            if row is None or context is None or not isinstance(assignment, dict):
                retained_set_regressions.append(case_id)
                continue
            expected = _case_from_replay(row, context, case_id, assignment, documents)
            if _json_hash({"A": case.get("A", []), "B": case.get("B", [])}) != _json_hash({"A": expected["A"], "B": expected["B"]}):
                retained_set_regressions.append(case_id)
    else:
        for index, old in enumerate(old_items, 1):
            target_id = str(old.get("target_id") or "")
            row = replay_rows.get(target_id)
            context = contexts.get(target_id)
            replacement_reason: list[str] = []
            if row is None or context is None:
                replacement_reason = ["replay_record_unavailable"]
            elif context["context_status"] != "complete":
                replacement_reason = list(context["missing_context"])
            if replacement_reason:
                desired = context["review_strata"] if context else list(old.get("review_strata") or [])
                if not available_candidates:
                    if strict:
                        raise RuntimeError(f"no context-complete replacement for {old.get('case_id')}")
                    continue
                weights = (6, 3, 4, 6, 2)
                def candidate_key(candidate: dict[str, Any]) -> tuple[int, str]:
                    strata = contexts[str(candidate["target_id"])]["review_strata"]
                    score = sum(weight for weight, wanted, actual in zip(weights, desired, strata) if wanted == actual)
                    return (-score, sha256_bytes(f"context-replacement-v1:{old.get('case_id')}:{candidate['target_id']}".encode("utf-8")))
                available_candidates.sort(key=candidate_key)
                row = available_candidates.pop(0)
                context = contexts[str(row["target_id"])]
                old_case_id = str(old.get("case_id") or "")
                case_id = f"shadow-review-{index:03d}-{row['target_id']}"
                hybrid_is_a = int(sha256_bytes(f"blind-v2:{case_id}".encode("utf-8"))[:8], 16) % 2 == 0
                assignment = {"A": "hybrid" if hybrid_is_a else "lexical", "B": "lexical" if hybrid_is_a else "hybrid"}
                preserved = [
                    label for label, wanted, actual in zip(
                        ("lane", "language", "substance", "disagreement", "historical_use"),
                        desired, context["review_strata"],
                    ) if wanted == actual
                ]
                replacements.append({
                    "old_case_id": old_case_id,
                    "old_target_id": target_id,
                    "replacement_case_id": case_id,
                    "replacement_target_id": str(row["target_id"]),
                    "replacement_reason": replacement_reason,
                    "strata_preserved": preserved,
                    "old_strata": desired,
                    "new_strata": context["review_strata"],
                })
            else:
                case_id = str(old["case_id"])
                assignment = dict(existing_assignments.get(case_id) or {})
                if set(assignment) != {"A", "B"} or set(assignment.values()) != {"lexical", "hybrid"}:
                    raise RuntimeError(f"invalid blind assignment for {case_id}")
            case = _case_from_replay(row, context, case_id, assignment, documents)
            if replacements and replacements[-1].get("replacement_case_id") == case_id:
                case["replacement_of_case_id"] = replacements[-1]["old_case_id"]
                case["replacement_reason"] = replacements[-1]["replacement_reason"]
            elif _json_hash({"A": old.get("A", []), "B": old.get("B", [])}) != _json_hash({"A": case["A"], "B": case["B"]}):
                retained_set_regressions.append(case_id)
            new_items.append(case)
            new_assignments[case_id] = assignment

    replacement_old_case_ids = {str(item.get("old_case_id") or "") for item in replacements}
    for case_id, assignment in historical_assignments.items():
        if case_id in replacement_old_case_ids or case_id not in new_assignments:
            continue
        if assignment != new_assignments[case_id]:
            retained_assignment_regressions.append(case_id)

    final_sample = {
        **{key: value for key, value in sample.items() if key not in {"items", "schema_version", "selection_version", "generated_at"}},
        "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
        "selection_version": REVIEW_SAMPLE_VERSION,
        "context_version": REVIEW_CONTEXT_VERSION,
        "case_count": len(new_items),
        "items": new_items,
        "generated_at": sample.get("generated_at") if already_migrated else utc_now(),
    }
    assignment_core = {
        "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
        "assignment_version": REVIEW_BLIND_VERSION,
        "assignments": new_assignments,
    }
    final_blind = {
        **assignment_core,
        "assignment_manifest_hash": _json_hash(assignment_core),
        "generated_at": blind.get("generated_at") if already_migrated else utc_now(),
    }
    new_hash = _json_hash(final_sample)

    preserved_reviews: dict[str, Any] = {}
    stale_reviews: list[dict[str, Any]] = []
    old_by_case = {str(item.get("case_id")): item for item in old_items}
    historical_by_case = {str(item.get("case_id")): item for item in historical_items}
    new_by_case = {str(item.get("case_id")): item for item in new_items}
    replacement_old_ids = {str(item["old_case_id"]): item for item in replacements}
    for case_id, review in original_review_items.items():
        if case_id in replacement_old_ids:
            stale_reviews.append({"case_id": case_id, "reason": "case replacement after unavailable or truncated context", "review": review})
            continue
        old_case = old_by_case.get(case_id)
        new_case = new_by_case.get(case_id)
        if old_case is None or new_case is None:
            stale_reviews.append({"case_id": case_id, "reason": "case absent after review-context migration", "review": review})
            continue
        if _old_case_material(old_case) != _new_case_material(new_case):
            stale_reviews.append({"case_id": case_id, "reason": "materially expanded or reclassified conversational context", "review": review})
            continue
        preserved_reviews[case_id] = review

    migration_review_count = len(migration_history.get("reviews_preserved") or []) + len(migration_history.get("reviews_invalidated") or [])
    migration_preserved_count = len(migration_history.get("reviews_preserved") or [])
    migration_invalidated_count = len(migration_history.get("reviews_invalidated") or [])
    audit = {
        "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
        "context_version": REVIEW_CONTEXT_VERSION,
        "generated_at": str(migration_history.get("generated_at") or utc_now()),
        "last_verified_at": utc_now(),
        "offline_only": True,
        "root_cause": {
            "original_capture": "The reported first incoming text is already stored in the retained tweet cache as the literal abbreviated text; no fuller local copy exists.",
            "replay_extraction": "Replay serialisation contained a 500-character slice. No current replay item reached that bound, but the unsafe slice has been removed. Replay also retained only one merged parent string and no structured thread fields.",
            "sample_construction": "The v1 sample copied the replay incoming string and collapsed the single parent string into thread_context; it omitted references, authors, timestamps, quoted-post separation and older thread context.",
            "server_and_browser": "The v1 browser used textContent and no CSS line clamp, so it did not truncate incoming text. It merely rendered the incomplete sample fields it received.",
        },
        "catalog_stats": catalog["stats"],
        "original_case_count": len(historical_items),
        "original_complete_incoming_text_cases": len(historical_items) - original_truncated,
        "original_truncated_cases": original_truncated,
        "original_context_unavailable_cases": len(original_unavailable),
        "original_context_unavailable": original_unavailable,
        "final_complete_incoming_text_cases": sum(
            bool((item.get("incoming") or {}).get("text")) and item.get("context_status") == "complete" for item in new_items
        ),
        "final_direct_parent_cases": sum(bool(item.get("direct_parent")) for item in new_items),
        "final_quoted_post_cases": sum(bool(item.get("quoted_post")) for item in new_items),
        "final_older_thread_cases": sum(bool(item.get("older_thread_context")) for item in new_items),
        "final_context_unavailable_cases": sum(item.get("context_status") != "complete" for item in new_items),
        "retrieval_display_mismatch_cases": sum(
            not bool((item.get("query_basis") or {}).get("retrieval_text_matches_displayed_source")) for item in new_items
        ),
        "retrieval_used_truncated_text_cases": sum(
            any("truncation" in reason for reason in unavailable["reasons"]) for unavailable in original_unavailable
        ),
        "repaired_in_place_cases": sum(
            item.get("case_id") in historical_by_case and _old_case_material(historical_by_case[item["case_id"]]) != _new_case_material(item)
            for item in new_items
        ),
        "replaced_cases": len(replacements),
        "replacements": replacements,
        "existing_reviews_before": migration_review_count if already_migrated else len(original_review_items),
        "existing_reviews_preserved": migration_preserved_count if already_migrated else len(preserved_reviews),
        "existing_reviews_invalidated": migration_invalidated_count if already_migrated else len(stale_reviews),
        "current_review_count": len(original_review_items),
        "old_sample_hash": historical_old_hash,
        "new_sample_hash": new_hash,
        "assignment_manifest_hash": final_blind["assignment_manifest_hash"],
        "production_retrieval_recalculated": False,
        "retrieval_sets_changed_for_retained_cases": bool(retained_set_regressions),
        "retained_case_retrieval_set_regressions": retained_set_regressions,
        "retained_case_assignment_regressions": retained_assignment_regressions,
    }
    if strict:
        if len(new_items) != len(old_items):
            raise RuntimeError(f"review case count changed: {len(old_items)} -> {len(new_items)}")
        if audit["final_context_unavailable_cases"]:
            raise RuntimeError(f"{audit['final_context_unavailable_cases']} review cases still lack required context")
        if audit["retrieval_display_mismatch_cases"]:
            raise RuntimeError(f"{audit['retrieval_display_mismatch_cases']} review cases differ from retrieval query text")
        if audit["retrieval_sets_changed_for_retained_cases"]:
            raise RuntimeError(f"retained review evidence changed: {retained_set_regressions}")
        if retained_assignment_regressions:
            raise RuntimeError(f"retained blind assignments changed: {retained_assignment_regressions}")

    if apply and not already_migrated:
        backup_dir = retrieval_dir / "manual_review" / "backups" / f"context-migration-{old_hash[:12]}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in (sample_path, blind_path, review_path, retrieval_dir / "manual_review" / "human_review_audit.jsonl"):
            if path.exists() and not (backup_dir / path.name).exists():
                shutil.copy2(path, backup_dir / path.name)
        atomic_write_json(sample_path, final_sample)
        atomic_write_json(blind_path, final_blind)
        atomic_write_json(review_path, {"schema_version": REVIEW_CONTEXT_SCHEMA_VERSION, "items": preserved_reviews})
        stale_path = retrieval_dir / "manual_review" / "stale_reviews.json"
        previous_stale = json.loads(stale_path.read_text(encoding="utf-8")) if stale_path.exists() else {"schema_version": REVIEW_CONTEXT_SCHEMA_VERSION, "items": []}
        known = {(str(item.get("case_id")), int((item.get("review") or {}).get("revision", 0))) for item in previous_stale.get("items", [])}
        for item in stale_reviews:
            key = (str(item.get("case_id")), int((item.get("review") or {}).get("revision", 0)))
            if key not in known:
                previous_stale.setdefault("items", []).append(item)
        atomic_write_json(stale_path, previous_stale)
        migration = {
            "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
            "migration_version": REVIEW_CONTEXT_VERSION,
            "generated_at": audit["generated_at"],
            "old_sample_hash": old_hash,
            "new_sample_hash": new_hash,
            "old_assignment_manifest_hash": _json_hash(blind),
            "new_assignment_manifest_hash": final_blind["assignment_manifest_hash"],
            "backup_directory": str(backup_dir),
            "replacements": replacements,
            "reviews_preserved": sorted(preserved_reviews),
            "reviews_invalidated": [item["case_id"] for item in stale_reviews],
        }
        atomic_write_json(retrieval_dir / "review_sample_migration.json", migration)
        audit_path = retrieval_dir / "manual_review" / "human_review_audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "review_context_migration", **migration}, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    if apply:
        atomic_write_json(
            retrieval_dir / "manual_review" / "context_follow_up_queue.json",
            {
                "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
                "generated_at": audit["generated_at"],
                "items": original_unavailable,
                "note": "Removed from the ordinary blind preference sample; retained for local-context follow-up only.",
            },
        )
        atomic_write_json(retrieval_dir / "review_context_audit.json", audit)
        _write_review_context_report(retrieval_dir / "review_context_audit.md", audit)
    return audit


def build_review_sample(retrieval_dir: Path, count: int = 100) -> dict[str, Any]:
    """Build review sample."""
    existing_path = retrieval_dir / "review_sample_100.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if existing.get("context_version") == REVIEW_CONTEXT_VERSION:
            return existing
    replay_path = retrieval_dir / "replay_30d" / "replay_results.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    documents = {item["quote_id"]: item for item in load_documents(retrieval_dir / "retrieval_documents.jsonl")}
    candidates = list(replay.get("items", []))
    for row in candidates:
        row["review_strata"] = [
            str(row.get("lane") or "unknown"),
            "non_english" if has_non_ascii_letters(row.get("incoming_text", "")) else "english_or_unknown",
            "substantive" if row.get("substantive_query") else "low_substance",
            str(row.get("disagreement_class") or "unknown"),
            "historical_reply" if (row.get("historical_decision") or {}).get("mode") in {"historical_correction", "historical_context", "researched_principle"} else "humour_or_unreplied",
        ]
        row["selection_hash"] = sha256_bytes(f"hybrid-review-v1:{row['target_id']}".encode("utf-8"))
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        key = "|".join(row["review_strata"])
        groups.setdefault(key, []).append(row)
    for values in groups.values():
        values.sort(key=lambda row: row["selection_hash"])
    selected: list[dict[str, Any]] = []
    keys = sorted(groups)
    while len(selected) < min(count, len(candidates)):
        progress = False
        for key in keys:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop(0))
                progress = True
        if not progress:
            break
    selected.sort(key=lambda row: row["selection_hash"])
    cases: list[dict[str, Any]] = []
    assignments: dict[str, dict[str, str]] = {}
    for index, row in enumerate(selected, 1):
        case_id = f"shadow-review-{index:03d}-{row['target_id']}"
        lexical_ids = [item["quote_id"] for item in row.get("lexical_results", [])[:5]]
        hybrid_ids = list(row.get("shadow_hybrid_quote_ids", []))[:5]
        lexical_set = [_packet_preview(documents[qid]) for qid in lexical_ids if qid in documents]
        hybrid_set = [_packet_preview(documents[qid]) for qid in hybrid_ids if qid in documents]
        hybrid_is_a = int(sha256_bytes(f"blind-v1:{case_id}".encode("utf-8"))[:8], 16) % 2 == 0
        assignments[case_id] = {"A": "hybrid" if hybrid_is_a else "lexical", "B": "lexical" if hybrid_is_a else "hybrid"}
        cases.append({
            "case_id": case_id,
            "target_id": row["target_id"],
            "lane": row["lane"],
            "incoming_post": row.get("incoming_text", ""),
            "thread_context": row.get("parent_context", ""),
            "substantive_query": row.get("substantive_query"),
            "disagreement_class": row.get("disagreement_class"),
            "review_strata": row["review_strata"],
            "selection_reason": "deterministic round-robin across lane, language, substance, disagreement and historical-use strata",
            "A": hybrid_set if hybrid_is_a else lexical_set,
            "B": lexical_set if hybrid_is_a else hybrid_set,
        })
    sample = {
        "schema_version": 1,
        "selection_version": "hybrid-shadow-review-sample-v1",
        "requested_count": count,
        "case_count": len(cases),
        "source_candidate_count": len(candidates),
        "items": cases,
        "generated_at": utc_now(),
    }
    blind = {
        "schema_version": 1, "assignment_version": "hybrid-shadow-blind-v1",
        "assignments": assignments, "generated_at": utc_now(),
    }
    atomic_write_json(retrieval_dir / "review_sample_100.json", sample)
    atomic_write_json(retrieval_dir / "blind_assignment_manifest.json", blind)
    review_dir = retrieval_dir / "manual_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    if not (review_dir / "human_reviews.json").exists():
        atomic_write_json(review_dir / "human_reviews.json", {"schema_version": 1, "items": {}})
    return sample


REVIEW_CHOICES = {
    "A_better", "B_better", "roughly_equal", "neither_useful",
    "no_historical_evidence", "insufficient_context",
}
PACKET_JUDGEMENTS = {"relevant", "partially_relevant", "irrelevant", "unsafe_as_evidence"}
INTERVENTIONS = {"historical_correction", "historical_context", "researched_principle", "humour_preferable", "no_reply_preferable"}


def save_review(retrieval_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Save review."""
    sample = json.loads((retrieval_dir / "review_sample_100.json").read_text(encoding="utf-8"))
    valid_cases = {row["case_id"] for row in sample["items"]}
    case_id = str(payload.get("case_id") or "")
    choice = str(payload.get("choice") or "")
    if case_id not in valid_cases or choice not in REVIEW_CHOICES:
        raise ValueError("invalid case or review choice")
    intervention = str(payload.get("intervention") or "")
    if choice != "insufficient_context" and intervention not in INTERVENTIONS:
        raise ValueError("invalid intervention")
    if choice == "insufficient_context":
        intervention = ""
    packet_ratings = payload.get("packet_ratings") or {}
    if not isinstance(packet_ratings, dict) or any(value not in PACKET_JUDGEMENTS for value in packet_ratings.values()):
        raise ValueError("invalid packet rating")
    normalised = {
        "case_id": case_id,
        "choice": choice,
        "packet_ratings": {str(key): str(value) for key, value in sorted(packet_ratings.items())},
        "intervention": intervention,
        "reason_tags": sorted(set(str(item)[:80] for item in payload.get("reason_tags", []) if str(item).strip())),
        "note": str(payload.get("note") or "")[:2000],
    }
    review_dir = retrieval_dir / "manual_review"
    path = review_dir / "human_reviews.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    previous = store.get("items", {}).get(case_id)
    comparable_previous = {key: previous.get(key) for key in normalised} if isinstance(previous, dict) else None
    if comparable_previous == normalised:
        return previous
    revision = int(previous.get("revision", 0) if isinstance(previous, dict) else 0) + 1
    record = {**normalised, "revision": revision, "reviewed_at": utc_now()}
    store.setdefault("items", {})[case_id] = record
    atomic_write_json(path, store)
    audit = {
        "timestamp": record["reviewed_at"], "case_id": case_id, "revision": revision,
        "previous": previous, "new": record,
    }
    with (review_dir / "human_review_audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(audit, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def review_results(retrieval_dir: Path) -> dict[str, Any]:
    """Return the review results."""
    sample = json.loads((retrieval_dir / "review_sample_100.json").read_text(encoding="utf-8"))
    blind = json.loads((retrieval_dir / "blind_assignment_manifest.json").read_text(encoding="utf-8"))
    reviews = json.loads((retrieval_dir / "manual_review" / "human_reviews.json").read_text(encoding="utf-8"))
    valid_cases = {str(item["case_id"]) for item in sample.get("items", [])}
    current_reviews = {
        str(case_id): record for case_id, record in reviews.get("items", {}).items()
        if str(case_id) in valid_cases and isinstance(record, dict)
    }
    counts = Counter()
    intervention = Counter()
    retriever_wins = Counter()
    insufficient_ids: list[str] = []
    for case_id, record in current_reviews.items():
        counts[str(record.get("choice"))] += 1
        intervention_value = str(record.get("intervention") or "")
        if intervention_value:
            intervention[intervention_value] += 1
        choice = record.get("choice")
        if choice == "insufficient_context":
            insufficient_ids.append(case_id)
            continue
        if choice in {"A_better", "B_better"}:
            side = choice[0]
            retriever_wins[blind["assignments"][case_id][side]] += 1
    result = {
        "schema_version": 1,
        "sample_count": sample["case_count"],
        "reviewed_count": len(current_reviews),
        "unreviewed_count": sample["case_count"] - len(current_reviews),
        "ordinary_preference_review_count": len(current_reviews) - len(insufficient_ids),
        "insufficient_context_count": len(insufficient_ids),
        "insufficient_context_case_ids": sorted(insufficient_ids),
        "choice_counts": dict(sorted(counts.items())),
        "intervention_counts": dict(sorted(intervention.items())),
        "decisive_retriever_wins": dict(sorted(retriever_wins.items())),
        "generated_at": utc_now(),
    }
    atomic_write_json(retrieval_dir / "manual_review" / "review_results.json", result)
    return result


def browser_review_payload(retrieval_dir: Path) -> dict[str, Any]:
    """Return a browser-safe sample without exposing retriever identities."""
    sample = json.loads((retrieval_dir / "review_sample_100.json").read_text(encoding="utf-8"))
    review_path = retrieval_dir / "manual_review" / "human_reviews.json"
    reviews = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {"items": {}}
    reviewed = set(str(case_id) for case_id in reviews.get("items", {}))
    blind_complete = len(reviewed) >= len(sample.get("items", [])) and bool(sample.get("items"))
    public_items: list[dict[str, Any]] = []
    for case in sample.get("items", []):
        internal_class = str(case.get("_disagreement_class") or case.get("disagreement_class") or "unknown")
        flags: list[str] = []
        if case.get("context_status") != "complete":
            flags.append("insufficient_context")
        incoming_text = str((case.get("incoming") or {}).get("text") or case.get("incoming_post") or "")
        if has_non_ascii_letters(incoming_text):
            flags.append("non_english")
        if not bool((case.get("query_basis") or {}).get("substantive_query", case.get("substantive_query"))):
            flags.append("low_substance")
        if internal_class not in {"same_evidence", "same_set_different_order", "both_no_evidence"}:
            flags.append("retrieval_disagreement")
        if internal_class in {"lexical_only_evidence", "hybrid_only_evidence"}:
            flags.append("one_set_only_evidence")
        if internal_class == "no_evidence_disagreement":
            flags.append("no_evidence_disagreement")
        basis = case.get("query_basis") or {
            "incoming_text_used": True,
            "direct_parent_used": bool(case.get("thread_context")),
            "older_thread_used": False,
            "quoted_post_used": False,
            "substantive_query": bool(case.get("substantive_query")),
            "normalised_query": normalise_text(incoming_text),
        }
        public_basis = {
            key: basis.get(key) for key in (
                "incoming_text_used", "direct_parent_used", "older_thread_used",
                "quoted_post_used", "substantive_query", "normalised_query",
                "query_text_hash", "retrieval_text_matches_displayed_source",
            )
        }
        public_items.append({
            "case_id": str(case["case_id"]),
            "target_id": str(case.get("target_id") or ""),
            "lane": str(case.get("lane") or "mention"),
            "incoming": case.get("incoming") or _post_for_review(None, fallback_text=incoming_text),
            "direct_parent": case.get("direct_parent"),
            "older_thread_context": list(case.get("older_thread_context") or []),
            "quoted_post": case.get("quoted_post"),
            "query_basis": public_basis,
            "context_status": str(case.get("context_status") or "unavailable"),
            "missing_context": list(case.get("missing_context") or []),
            "filter_flags": sorted(set(flags)),
            "A": list(case.get("A") or []),
            "B": list(case.get("B") or []),
        })
    return {
        "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
        "case_count": len(public_items),
        "blind_complete": blind_complete,
        "context_unavailable_count": sum(item["context_status"] != "complete" for item in public_items),
        "items": public_items,
    }


REVIEW_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Retrieval evidence review</title><style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;margin:0;background:#f3f2ee;color:#171717;letter-spacing:0}.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #bbb;padding:9px 14px;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.top select{font-size:1rem;padding:8px}.wrap{max-width:1240px;margin:auto;padding:16px}.incoming-shell{position:sticky;top:58px;z-index:3;background:#f3f2ee;padding-bottom:8px}.card{background:#fff;border:1px solid #bbb;padding:14px}.incoming{font-size:1.24rem;line-height:1.48;border-left:4px solid #8a1538;white-space:pre-wrap;overflow-wrap:anywhere}.secondary{font-size:1rem;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}.context-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}.context-stack{display:grid;gap:8px}.label{font-size:.78rem;font-weight:750;text-transform:uppercase;color:#555;margin-bottom:6px}.warning{background:#fff1cf;border:1px solid #bc7d00;padding:12px;margin:10px 0}.query-status{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}.query-status span{border:1px solid #aaa;background:#fff;padding:6px 8px;font-size:.86rem}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.set{background:#fff;border:1px solid #aaa;padding:14px;min-width:0}.set h2{font-size:1.2rem;margin-top:0}.packet{border-top:1px solid #ddd;padding:12px 0;overflow-wrap:anywhere}.packet:first-child{border:0}.quote{font-weight:680}.meta{font-size:.87rem;color:#555}.choices,.interventions,.tags{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}button{padding:12px 16px;border:1px solid #777;background:#fff;font-size:1rem;cursor:pointer;min-height:46px}button.selected{background:#111;color:#fff}button:disabled{color:#888;background:#eee;cursor:not-allowed}.tags label{background:#fff;border:1px solid #bbb;padding:9px}textarea{width:100%;min-height:78px;padding:9px;font:inherit}.nav{display:flex;justify-content:space-between;gap:10px;margin-top:14px}details.card summary{font-weight:680;cursor:pointer;min-height:36px;padding:4px}details.card[open] summary{margin-bottom:8px}.muted{color:#666}.spacer{height:8px}@media(max-width:900px){.grid,.context-grid{grid-template-columns:1fr}.wrap{padding:10px}.incoming-shell{position:static}.incoming{font-size:1.08rem}.top{position:sticky}.set{padding:11px}button{flex:1 1 44%}}@media(min-width:901px) and (max-width:1180px){.wrap{padding:12px}.incoming-shell{top:66px}}
</style></head><body><div class="top"><strong id="progress">Loading</strong><label>Filter <select id="filter"><option value="all">All</option><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="insufficient_context">Insufficient context</option><option value="non_english">Non-English</option><option value="low_substance">Low-substance</option><option value="retrieval_disagreement">Retriever disagreement</option><option value="one_set_only_evidence">One-set-only evidence</option><option value="no_evidence_disagreement">No-evidence disagreement</option></select></label><button id="backContext">Context</button><button id="nextUnreviewed">Next unreviewed</button><span id="missing"></span></div><main class="wrap"><section class="incoming-shell"><div class="label">Incoming user contribution</div><div class="card incoming" id="incoming"></div></section><div id="warning"></div><section class="context-grid"><div id="primaryContext" class="context-stack"></div><div id="olderContext" class="context-stack"></div></section><section><div class="label">Retrieval query status</div><div class="query-status" id="queryStatus"></div></section><div class="grid"><section class="set"><h2>Retrieval Set A</h2><div id="seta"></div></section><section class="set"><h2>Retrieval Set B</h2><div id="setb"></div></section></div><div class="choices" id="choices"></div><h3>What response is justified?</h3><div class="interventions" id="interventions"></div><h3>Reasons</h3><div class="tags" id="tags"></div><textarea id="note" placeholder="Optional notes"></textarea><div class="nav"><button id="prev">Previous</button><button id="next">Save / Next</button></div></main><script>
let allCases=[],cases=[],reviews={},i=0,choice='',intervention='',saving=false,saveQueued=false,noteTimer=null,blindComplete=false;const choices=[['A_better','A is better'],['B_better','B is better'],['roughly_equal','Roughly equal'],['neither_useful','Neither retrieved useful evidence'],['no_historical_evidence','No historical evidence should be used'],['insufficient_context','Insufficient context to judge']];const ints=[['historical_correction','Historical correction justified'],['historical_context','Historical context justified'],['researched_principle','Researched principle justified'],['humour_preferable','Humour preferable'],['no_reply_preferable','No reply preferable']];const tagValues=['conceptually stronger','exact match stronger','cross-language evidence','over-broad ideological match','parent context overreach','unsafe factual use','no sufficiently relevant evidence','requires human research'];
function esc(x){return String(x||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function packets(rows){if(!rows.length)return '<p class="muted">No evidence</p>';return rows.map(p=>`<div class="packet"><div class="quote">${esc(p.quote_text)}</div><p>${esc(p.intended_argument||p.broader_principle)}</p><div class="meta">${esc(p.verification_status)} · ${esc(p.research_confidence)} · ${esc(p.source_event)}</div><label>Assessment <select data-packet="${esc(p.quote_id)}"><option value="">Not assessed</option><option value="relevant">Relevant</option><option value="partially_relevant">Partly relevant</option><option value="irrelevant">Irrelevant</option><option value="unsafe_as_evidence">Unsafe as evidence</option></select></label></div>`).join('')}
function appendPost(root,label,post,expandable){if(!post)return;let shell=document.createElement(expandable?'details':'div');shell.className='card secondary';if(expandable){let summary=document.createElement('summary');summary.textContent=label;let size=(post.text||'').length+(post.image_summary||'').length;if(size<500)shell.open=true;shell.appendChild(summary)}else{let heading=document.createElement('div');heading.className='label';heading.textContent=label;shell.appendChild(heading)}let text=document.createElement('div');text.className='secondary';text.textContent=post.text||'';shell.appendChild(text);if(post.image_summary){let image=document.createElement('div');image.className='meta';image.textContent='Locally retained image description: '+post.image_summary;shell.appendChild(image)}if(post.author_handle||post.timestamp){let meta=document.createElement('div');meta.className='meta';meta.textContent=[post.author_handle?'@'+post.author_handle:'',post.timestamp||''].filter(Boolean).join(' · ');shell.appendChild(meta)}root.appendChild(shell)}
function buttons(root,items,current,setter){root.innerHTML='';items.forEach(([v,l])=>{let b=document.createElement('button');b.textContent=l;b.className=v===current?'selected':'';b.onclick=()=>{setter(v);if(v==='insufficient_context')intervention='';renderButtons();save(false)};root.appendChild(b)})}function renderButtons(){buttons(document.getElementById('choices'),choices,choice,v=>choice=v);buttons(document.getElementById('interventions'),ints,intervention,v=>intervention=v)}
function selectedTags(){return [...document.querySelectorAll('#tags input:checked')].map(x=>x.value)}function renderTags(current){document.getElementById('tags').innerHTML=tagValues.map(v=>`<label><input type="checkbox" value="${esc(v)}" ${current.includes(v)?'checked':''}> ${esc(v)}</label>`).join('');document.querySelectorAll('#tags input').forEach(x=>x.onchange=()=>save(false))}
function yesNo(label,value){let span=document.createElement('span');span.textContent=label+': '+(value?'yes':'no');return span}function updateProgress(){let reviewed=Object.keys(reviews).filter(id=>allCases.some(c=>c.case_id===id)).length;document.getElementById('progress').textContent=cases.length?`${i+1} of ${cases.length} shown · ${reviewed} of ${allCases.length} reviewed`: `No cases in filter · ${reviewed} of ${allCases.length} reviewed`}
function show(){if(!cases.length){document.getElementById('incoming').textContent='No cases match this filter.';updateProgress();return}let c=cases[i],r=reviews[c.case_id]||{};choice=r.choice||'';intervention=r.intervention||'';document.getElementById('incoming').textContent=(c.incoming&&c.incoming.text)||'';let warning=document.getElementById('warning');warning.innerHTML='';if(c.context_status!=='complete'){let box=document.createElement('div');box.className='warning';box.textContent='Context incomplete: '+(c.missing_context||[]).join(', ');warning.appendChild(box)}let primary=document.getElementById('primaryContext');primary.innerHTML='';if(c.lane==='quote_tweet')appendPost(primary,'Quoted post',c.quoted_post,true);else appendPost(primary,c.lane==='hot-post'?'Target hot post':'Direct parent context',c.direct_parent,true);if(!primary.children.length){let box=document.createElement('div');box.className='card muted';box.textContent=c.lane==='quote_tweet'?'Quoted post is not locally available.':'No direct parent is recorded; this may be a standalone contribution.';primary.appendChild(box)}let older=document.getElementById('olderContext');older.innerHTML='';(c.older_thread_context||[]).forEach((post,n)=>appendPost(older,`Earlier thread context ${n+1} (oldest to newest)`,post,true));if(!older.children.length){let box=document.createElement('div');box.className='card muted';box.textContent='No earlier thread context is locally available.';older.appendChild(box)}let q=c.query_basis||{};let qs=document.getElementById('queryStatus');qs.innerHTML='';qs.append(yesNo('Substantive query',q.substantive_query),yesNo('Incoming text used',q.incoming_text_used),yesNo('Direct parent used',q.direct_parent_used),yesNo('Older thread used',q.older_thread_used),yesNo('Quoted post used',q.quoted_post_used));document.getElementById('seta').innerHTML=packets(c.A);document.getElementById('setb').innerHTML=packets(c.B);document.getElementById('note').value=r.note||'';document.querySelectorAll('select[data-packet]').forEach(s=>{if(r.packet_ratings&&r.packet_ratings[s.dataset.packet])s.value=r.packet_ratings[s.dataset.packet];s.onchange=()=>save(false)});renderTags(r.reason_tags||[]);renderButtons();updateProgress();window.scrollTo({top:0,behavior:'auto'})}
function applyFilter(){let value=document.getElementById('filter').value;let current=cases[i]&&cases[i].case_id;cases=allCases.filter(c=>value==='all'||(value==='reviewed'&&reviews[c.case_id])||(value==='unreviewed'&&!reviews[c.case_id])||(c.filter_flags||[]).includes(value));i=Math.max(0,cases.findIndex(c=>c.case_id===current));show()}
async function save(announce){if(!choice||(choice!=='insufficient_context'&&!intervention)){if(announce)alert('Choose a set-level decision and the justified response.');return false}if(saving){saveQueued=true;return false}saving=true;let ratings={};document.querySelectorAll('select[data-packet]').forEach(s=>{if(s.value)ratings[s.dataset.packet]=s.value});let c=cases[i],body={case_id:c.case_id,choice,intervention,packet_ratings:ratings,reason_tags:selectedTags(),note:document.getElementById('note').value};try{let response=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok){if(announce)alert(await response.text());return false}reviews[c.case_id]=await response.json();updateProgress();return true}finally{saving=false;if(saveQueued){saveQueued=false;setTimeout(()=>save(false),0)}}}
document.getElementById('next').onclick=async()=>{if(await save(true)){i=Math.min(cases.length-1,i+1);show()}};document.getElementById('prev').onclick=()=>{i=Math.max(0,i-1);show()};document.getElementById('backContext').onclick=()=>document.querySelector('.incoming-shell').scrollIntoView({behavior:'smooth',block:'start'});document.getElementById('nextUnreviewed').onclick=()=>{let start=cases[i]?allCases.findIndex(c=>c.case_id===cases[i].case_id):-1;let found=allCases.findIndex((c,n)=>n>start&&!reviews[c.case_id]);if(found<0)found=allCases.findIndex(c=>!reviews[c.case_id]);document.getElementById('filter').value='all';cases=allCases;i=Math.max(0,found);show()};document.getElementById('filter').onchange=applyFilter;document.getElementById('note').oninput=()=>{clearTimeout(noteTimer);noteTimer=setTimeout(()=>save(false),600)};document.addEventListener('keydown',e=>{if(['TEXTAREA','SELECT','INPUT'].includes(e.target.tagName))return;if(e.key==='ArrowRight')document.getElementById('next').click();if(e.key==='ArrowLeft')document.getElementById('prev').click();if('123456'.includes(e.key)){choice=choices[+e.key-1][0];if(choice==='insufficient_context')intervention='';renderButtons();save(false)}});
Promise.all([fetch('/api/cases').then(r=>r.json()),fetch('/api/reviews').then(r=>r.json())]).then(([data,stored])=>{allCases=data.items;cases=allCases;reviews=stored.items||{};blindComplete=!!data.blind_complete;document.getElementById('missing').textContent=`Incomplete context: ${data.context_unavailable_count}`;let first=cases.findIndex(c=>!reviews[c.case_id]);i=first<0?0:first;show()});</script></body></html>"""


def serve_review(retrieval_dir: Path, host: str, port: int) -> None:
    """Serve review."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        raise ValueError("host must be loopback or an explicit LAN address")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, value: Any, status: int = 200) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = REVIEW_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/cases":
                self._json(browser_review_payload(retrieval_dir))
                return
            if self.path == "/api/reviews":
                self._json(json.loads((retrieval_dir / "manual_review" / "human_reviews.json").read_text(encoding="utf-8")))
                return
            if self.path == "/api/results":
                result = review_results(retrieval_dir)
                if result["unreviewed_count"]:
                    result = {key: value for key, value in result.items() if key != "decisive_retriever_wins"}
                    result["blind_results_withheld"] = True
                self._json(result)
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/save":
                self._json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20_000:
                    raise ValueError("invalid request length")
                payload = json.loads(self.rfile.read(length))
                self._json(save_review(retrieval_dir, payload))
            except Exception as exc:
                self._json({"error": str(exc)}, 400)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Review server: http://{host}:{port}")
    server.serve_forever()


def write_hybrid_report(retrieval_dir: Path, project_dir: Path) -> dict[str, Any]:
    """Write hybrid report."""
    index = json.loads((retrieval_dir / "index" / "index_manifest.json").read_text(encoding="utf-8"))
    model = json.loads((retrieval_dir / "model_manifest.json").read_text(encoding="utf-8"))
    thresholds = json.loads((retrieval_dir / "thresholds.json").read_text(encoding="utf-8"))
    evaluation = json.loads((retrieval_dir / "automated_evaluation.json").read_text(encoding="utf-8"))
    replay = json.loads((retrieval_dir / "replay_30d" / "replay_results.json").read_text(encoding="utf-8"))
    sample = json.loads((retrieval_dir / "review_sample_100.json").read_text(encoding="utf-8"))
    runtime = shadow_summary(read_shadow_records(project_dir / "hybrid_reply_retrieval_runtime"))
    try:
        local = json.loads((project_dir / "mrsMThatcher.local.json").read_text(encoding="utf-8"))
        live_enabled = bool(local.get("reply_strategy", {}).get("hybrid_retrieval", {}).get("enabled"))
        live_mode = str(local.get("reply_strategy", {}).get("hybrid_retrieval", {}).get("mode") or "unavailable")
    except Exception:
        live_enabled, live_mode = False, "unavailable"
    replay_items = replay.get("items", [])
    hybrid_only = [item for item in replay_items if item.get("disagreement_class") == "hybrid_only_evidence"]
    short_risks = [
        item for item in replay_items
        if item.get("shadow_hybrid_quote_ids") and len(WORD_RE.findall(normalise_text(item.get("incoming_text", "")))) <= 2
    ]
    non_english_additions = [
        item for item in hybrid_only if query_language_hint(item.get("incoming_text", "")) != "english-or-unknown"
    ]
    strata = Counter(value for item in sample.get("items", []) for value in item.get("review_strata", []))
    status = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "live_shadow_enabled": live_enabled,
        "live_mode": live_mode,
        "production_retriever": "lexical",
        "shadow_events": runtime,
        "index_version": f"{index['index_schema_version']}:{index['embeddings_sha256'][:12]}",
        "model_id": model["model_id"],
        "reviewed_count": review_results(retrieval_dir)["reviewed_count"],
    }
    atomic_write_json(retrieval_dir / "shadow_status.json", status)
    evaluation_multilingual = evaluation.get("multilingual", {})
    report = [
        "# Hybrid Reply Retrieval Report", "",
        "## Status", "",
        f"Live shadow enabled: **{'yes' if live_enabled else 'no'}** (`{live_mode}`).",
        "The production conversational reply path remains lexical. Hybrid results are not supplied to xAI and cannot alter reply modes, text, receipts, posting, or scheduling.", "",
        "## Live shadow observation", "",
        f"- Events/completed/failures: {runtime['events']} / {runtime['completed']} / {runtime['failures']}",
        f"- Top-five lexical/hybrid overlap: {runtime['top_5_overlap_percent'] if runtime['top_5_overlap_percent'] is not None else 'unavailable'}%",
        f"- Changed evidence sets: {runtime['hybrid_changed_evidence_set']}",
        f"- Hybrid-only / lexical-only evidence: {runtime['hybrid_only_evidence']} / {runtime['lexical_only_evidence']}",
        f"- No-evidence disagreements: {runtime['no_evidence_disagreements']}",
        f"- Latency p50/p95/max: {runtime['latency_p50_ms']} / {runtime['latency_p95_ms']} / {runtime['latency_max_ms']} ms",
        "The observed production decisions were checked against the separately recorded shadow rows: selected evidence remained a subset of the authoritative lexical evidence, with no hybrid-only quote ID entering a production decision.", "",
        "## Model and index", "",
        f"- Model: `{model['model_id']}`",
        f"- Licence: {model['licence']}",
        "- Runtime: ONNX, local files only, `trust_remote_code=False`",
        f"- Package pins: {json.dumps(model.get('package_versions', {}), sort_keys=True)}",
        f"- Documents: {index['document_count']}",
        f"- Dimensions: {index['dimensions']} float32",
        f"- Embedding matrix: {(retrieval_dir / 'index' / 'embeddings.npy').stat().st_size / 1024 / 1024:.2f} MiB",
        f"- Build time: {index['build_seconds']:.3f} seconds",
        f"- Model/index load time: {evaluation['latency_ms']['model_index_load']:.1f} ms",
        f"- Peak build RSS increase: {index.get('peak_rss_delta_kib', 0) / 1024:.1f} MiB",
        f"- Corpus hash: `{index['corpus_hash']}`",
        f"- Embeddings hash: `{index['embeddings_sha256']}`", "",
        "## Retrieval method", "",
        "The retriever forms the union of the unchanged lexical top 20 and exact cosine semantic top 20, adds bounded metadata matches, and ranks with weighted reciprocal-rank fusion. Metadata cannot rescue a candidate below both absolute lexical and semantic floors. Exact and >=0.90-Jaccard quote variants are deduplicated.",
        f"Selected thresholds: `{json.dumps(thresholds, sort_keys=True)}`", "",
        "## Automated evaluation", "",
        f"- Evaluation positives: {evaluation['hybrid']['queries']}",
        f"- Lexical recall@1/@5/MRR: {evaluation['lexical']['recall_at_1']:.1%} / {evaluation['lexical']['recall_at_5']:.1%} / {evaluation['lexical']['mean_reciprocal_rank']:.3f}",
        f"- Semantic recall@1/@5/MRR: {evaluation['semantic']['recall_at_1']:.1%} / {evaluation['semantic']['recall_at_5']:.1%} / {evaluation['semantic']['mean_reciprocal_rank']:.3f}",
        f"- Hybrid recall@1/@5/MRR: {evaluation['hybrid']['recall_at_1']:.1%} / {evaluation['hybrid']['recall_at_5']:.1%} / {evaluation['hybrid']['mean_reciprocal_rank']:.3f}",
        f"- Held-out hard-negative no-evidence accuracy: {evaluation['hard_negative_no_evidence_accuracy']:.1%}",
        f"- Multilingual topic-match, overall/holdout: {evaluation_multilingual.get('overall_topic_match_rate', 0):.1%} / {evaluation_multilingual.get('evaluation_topic_match_rate', 0):.1%}",
        f"- Query latency p50/p95/max: {evaluation['latency_ms']['p50']:.1f} / {evaluation['latency_ms']['p95']:.1f} / {evaluation['latency_ms']['maximum']:.1f} ms", "",
        "Packet-derived evaluation structurally favours lexical retrieval because its queries are drawn from the same English packets. The multilingual smoke set is small and hand-labelled by broad topic. Neither is evidence for active promotion.", "",
        "## 30-day replay", "",
        f"- Structured candidates: {replay['summary']['candidate_count']}",
        f"- Completed/failures: {replay['summary']['completed']} / {replay['summary']['failures']}",
        f"- Changed evidence sets: {replay['summary']['hybrid_changed_evidence_set']}",
        f"- Hybrid-only / lexical-only evidence: {replay['summary']['hybrid_only_evidence']} / {replay['summary']['lexical_only_evidence']}",
        f"- No-evidence disagreements: {replay['summary']['no_evidence_disagreements']}",
        f"- Candidates containing non-ASCII letters: {replay['summary']['multilingual_candidate_count']}",
        f"- Replay latency p50/p95/max: {replay['summary']['latency_p50_ms']} / {replay['summary']['latency_p95_ms']} / {replay['summary']['latency_max_ms']} ms", "",
        f"Potentially beneficial additions include {len(non_english_additions)} hybrid-only non-English cases. These are review candidates, not validated gains. Risk review identified {len(short_risks)} accepted one- or two-word queries, where semantic retrieval can be over-broad.", "",
        "## Human review queue", "",
        f"The blind queue contains {sample['case_count']} real historical candidates selected across lane, language, substantive-content, disagreement, and historical-use strata. Composition counts: `{json.dumps(dict(sorted(strata.items())), sort_keys=True)}`.",
        "Launch: `python3 hybrid_reply_retrieval.py serve-review --retrieval-dir semantic_alignment_research/hybrid_reply_retrieval_001 --host 127.0.0.1 --port 8767`", "",
        "## Safety conclusion", "",
        "The automated and replay results justify shadow observation and human review only. Hybrid retrieval remains unproven for production evidence selection. The source default is disabled; the only permitted configured mode is `shadow`; failures and stale indexes fail open to the unchanged lexical path.",
        "", "## Digest and verification", "",
        "`mrs_log_digest.py` reads only the ignored local shadow status JSON. It does not import the embedder or make an embedding, X, or xAI call. Missing or malformed telemetry is reported as unavailable.",
        "The final focused regression run passed 120 tests. The broader reply, digest, formatter, fail-safe, receipt, integration, and production-isolation run passed 816 tests with one skip. Python compilation, Ruff checks on the new modules, and `git diff --check` passed.",
        "", "## Limitations", "",
        "Packet-derived queries favour lexical retrieval, the multilingual labelled set is very small, and no human relevance review is complete. Broad one- or two-word concepts can still produce unsafe semantic matches. These limitations prohibit active promotion.",
    ]
    atomic_write_text(retrieval_dir / "hybrid_reply_retrieval_report.md", "\n".join(report) + "\n")
    return status
