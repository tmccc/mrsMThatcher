"""Provide transactional generated-image quarantine and metadata services."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

GENERATED_RE = re.compile(r"tg_([0-9a-f]{64})\.png\Z")
PROTECTED_NAMES = {
    "bot_state.json", "images_used.json", "lines_used.json", "mrsMThatcher.log",
    "mrsMThatcher.local.json", "regular_post_receipt.json", "meme_post_receipt.json",
    "confirmed_reply_receipt.json", "mrsMThatcher.lock",
}


class ReviewError(RuntimeError):
    """Raised when a quarantine review operation is invalid or unsafe."""
    pass


def sha256(path: Path) -> str:
    """Return the SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    """Read JSON."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: Any) -> None:
    """Perform the atomic JSON operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True)
class Paths:
    """Represent paths data."""
    project: Path
    generated: Path
    quarantine: Path
    data: Path
    analysis: Path
    audit: Path
    used: Path

    @classmethod
    def build(cls, project: Path, generated: Path | None = None, quarantine: Path | None = None, data: Path | None = None) -> "Paths":
        """Build and validate all application filesystem paths."""
        project = project.resolve()
        return cls(project, (generated or project / "generated_review_approved_images").resolve(),
                   (quarantine or project / "generated_image_quarantine").resolve(),
                   (data or project / ".generated_image_review_app").resolve(),
                   (project / "generated_image_analysis.json").resolve(),
                   (project / "generated_image_identity_dependence_audit.json").resolve(),
                   (project / "images_used.json").resolve())


class ReviewService:
    """Represent review service data."""
    def __init__(self, paths: Paths, allow_changes: bool = False):
        """Initialise the review service."""
        self.paths = paths
        self.allow_changes = allow_changes
        self._lock = threading.Lock()
        self._validate_paths()

    def _validate_paths(self) -> None:
        if self.paths.generated == self.paths.quarantine or self.paths.project == self.paths.quarantine:
            raise ReviewError("generated and quarantine directories must be distinct")
        for path in (self.paths.analysis, self.paths.audit):
            if path.name in PROTECTED_NAMES:
                raise ReviewError(f"refusing protected metadata path: {path}")

    def _assert_mutable(self, path: Path) -> None:
        path = path.resolve()
        allowed = (self.paths.generated, self.paths.quarantine, self.paths.data, self.paths.analysis, self.paths.audit)
        if not any(path == root or root in path.parents for root in allowed):
            raise ReviewError(f"write outside review whitelist refused: {path}")
        if path.name in PROTECTED_NAMES:
            raise ReviewError(f"protected production file write refused: {path.name}")

    def _active_documents(self) -> tuple[dict, dict]:
        analysis, audit = read_json(self.paths.analysis), read_json(self.paths.audit)
        if analysis.get("schema_version") != 3 or analysis.get("analysis_kind") != "images":
            raise ReviewError("unsupported generated analysis schema")
        if audit.get("schema_version") != 1 or audit.get("analysis_kind") != "generated_image_identity_dependence_audit":
            raise ReviewError("unsupported identity audit schema")
        return analysis, audit

    def pool_token(self) -> str:
        """Return the pool token."""
        digest = hashlib.sha256()
        for path in (self.paths.analysis, self.paths.audit):
            digest.update(path.name.encode()); digest.update(sha256(path).encode())
        for path in sorted(self.paths.generated.glob("tg_*.png")):
            digest.update(path.name.encode()); digest.update(str(path.stat().st_size).encode()); digest.update(sha256(path).encode())
        return digest.hexdigest()

    def index(self) -> list[dict]:
        """Return the index."""
        analysis, audit = self._active_documents()
        used = set(read_json(self.paths.used)) if self.paths.used.is_file() else set()
        rows = []
        for path in sorted(self.paths.generated.glob("tg_*.png")):
            match = GENERATED_RE.fullmatch(path.name)
            if not match:
                continue
            actual = sha256(path); expected = (analysis.get("path_index") or {}).get(path.name)
            audit_item = (audit.get("items") or {}).get(path.name) or {}
            identity = audit_item.get("analysis") or {}
            item = (analysis.get("items") or {}).get(str(expected), {}) if expected else {}
            generated_analysis = item.get("analysis") or {}
            with Image.open(path) as image:
                dimensions = [image.width, image.height]
            rows.append({
                "basename": path.name, "status": "active", "transaction_id": None, "image_url": f"/image/{path.name}", "sha256": actual, "hash_status": "ok" if actual == expected == audit_item.get("image_sha256") else "stale",
                "size": path.stat().st_size, "dimensions": dimensions, "origin_quote_hash": match.group(1),
                "origin_quote": audit_item.get("origin_quote", ""), "posted": path.name in used,
                "policy": identity.get("recommended_cross_quote_policy", "missing"), "identity_dependence": identity.get("identity_dependence", "missing"),
                "recognisability": identity.get("recognisability_to_typical_viewer"), "meaning_retention": identity.get("meaning_retention_without_identity"),
                "quality": generated_analysis.get("overall_editorial_utility", generated_analysis.get("quality_score")),
                "summary": generated_analysis.get("image_summary") or generated_analysis.get("summary") or identity.get("meaning_without_identity", ""),
                "metadata_status": "ok" if expected and audit_item else "missing",
            })
        active = {row["basename"] for row in rows}
        for transaction in self.transactions():
            if transaction.get("kind") != "quarantine": continue
            txid = str(transaction.get("transaction_id") or "")
            for entry in transaction.get("images") or []:
                name = str(entry.get("basename") or "")
                path = self.paths.quarantine / "transactions" / txid / "images" / name
                if name in active or not path.is_file(): continue
                audit_item = entry.get("audit_record") or {}; identity = audit_item.get("analysis") or {}; generated_analysis = (entry.get("analysis_record") or {}).get("analysis") or {}
                with Image.open(path) as image: dimensions = [image.width, image.height]
                rows.append({"basename": name, "status": "quarantined", "transaction_id": txid, "image_url": f"/quarantine-image/{txid}/{name}", "sha256": entry.get("sha256"),
                    "hash_status": "ok" if sha256(path) == entry.get("sha256") else "stale", "size": path.stat().st_size, "dimensions": dimensions,
                    "origin_quote_hash": entry.get("origin_quote_hash"), "origin_quote": entry.get("origin_quote", ""), "posted": bool(entry.get("used_history_present")),
                    "policy": identity.get("recommended_cross_quote_policy", "missing"), "identity_dependence": identity.get("identity_dependence", "missing"),
                    "recognisability": identity.get("recognisability_to_typical_viewer"), "meaning_retention": identity.get("meaning_retention_without_identity"),
                    "quality": generated_analysis.get("overall_editorial_utility", generated_analysis.get("quality_score")),
                    "summary": generated_analysis.get("image_summary") or generated_analysis.get("summary") or identity.get("meaning_without_identity", ""), "metadata_status": "preserved"})
        return rows

    def filter_sort(self, rows: list[dict], query: str = "", policy: str = "", posted: str = "", sort: str = "basename", status: str = "active") -> list[dict]:
        """Filter sort."""
        query = query.casefold().strip()
        result = [row for row in rows if (not query or query in (row["basename"] + " " + row["origin_quote"] + " " + row["summary"]).casefold())]
        if policy:
            result = [row for row in result if row["policy"] == policy]
        if posted in {"yes", "no"}:
            result = [row for row in result if row["posted"] is (posted == "yes")]
        if status in {"active", "quarantined"}:
            result = [row for row in result if row["status"] == status]
        keys = {
            "basename": lambda row: row["basename"], "quality": lambda row: (row["quality"] is None, -(row["quality"] or 0)),
            "identity": lambda row: ({"essential": 0, "high": 1, "medium": 2, "low": 3, "none": 4}.get(row["identity_dependence"], 9), row["basename"]),
            "recognisability": lambda row: (row["recognisability"] is None, row["recognisability"] or 0),
            "retention": lambda row: (row["meaning_retention"] is None, row["meaning_retention"] or 0),
            "size": lambda row: (-row["size"], row["basename"]), "quote": lambda row: (row["origin_quote"], row["basename"]),
            "posted": lambda row: (not row["posted"], row["basename"]),
        }
        return sorted(result, key=keys.get(sort, keys["basename"]))

    def _selection(self, basenames: list[str], token: str) -> tuple[list[str], dict, dict]:
        if token != self.pool_token():
            raise ReviewError("review is stale; refresh and review the current pool")
        names = list(dict.fromkeys(basenames))
        if len(names) != len(basenames) or not names:
            raise ReviewError("selection must contain unique images")
        analysis, audit = self._active_documents()
        for name in names:
            if not GENERATED_RE.fullmatch(name):
                raise ReviewError(f"invalid generated basename: {name}")
            path = self.paths.generated / name
            if not path.is_file():
                raise ReviewError(f"selected image is absent: {name}")
            actual = sha256(path); expected = (analysis.get("path_index") or {}).get(name)
            if actual != expected or actual != ((audit.get("items") or {}).get(name) or {}).get("image_sha256"):
                raise ReviewError(f"metadata hash mismatch: {name}")
        return names, analysis, audit

    def preview(self, basenames: list[str], token: str) -> dict:
        """Return the preview."""
        names, analysis, audit = self._selection(basenames, token)
        return {"basenames": names, "token": token, "metadata_files": [self.paths.analysis.name, self.paths.audit.name],
                "used_history_matches": [name for name in names if name in set(read_json(self.paths.used))],
                "confirmation": f"QUARANTINE {len(names)} IMAGES",
                "records": [{"basename": name, "analysis_hash": analysis["path_index"][name], "policy": audit["items"][name]["analysis"]["recommended_cross_quote_policy"]} for name in names]}

    def quarantine(self, basenames: list[str], token: str, confirmation: str, reason: str, note: str = "", fail_after_moves: int | None = None) -> dict:
        """Return the quarantine."""
        if not self.allow_changes:
            raise ReviewError("application is read-only; restart with --allow-changes")
        with self._lock:
            names, analysis, audit = self._selection(basenames, token)
            expected_phrase = f"QUARANTINE {len(names)} IMAGES"
            if confirmation != expected_phrase:
                raise ReviewError(f"confirmation phrase must be: {expected_phrase}")
            txid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
            tx = self.paths.quarantine / "transactions" / txid
            images_dir, metadata_dir = tx / "images", tx / "metadata"
            for path in (tx, images_dir, metadata_dir): self._assert_mutable(path); path.mkdir(parents=True, exist_ok=False if path == tx else True)
            manifest = {"schema_version": 1, "transaction_id": txid, "kind": "quarantine", "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(),
                        "reason": reason, "note": note, "images": [], "metadata_files": [self.paths.analysis.name, self.paths.audit.name], "restart_required": True}
            for name in names:
                image_hash = analysis["path_index"][name]
                manifest["images"].append({"basename": name, "original_relative_path": str((self.paths.generated / name).relative_to(self.paths.project)), "sha256": image_hash,
                    "size": (self.paths.generated / name).stat().st_size, "origin_quote_hash": GENERATED_RE.fullmatch(name).group(1),
                    "origin_quote": audit["items"][name].get("origin_quote", ""), "analysis_record": copy.deepcopy(analysis["items"][image_hash]),
                    "analysis_file_metadata": copy.deepcopy((analysis.get("file_metadata") or {}).get(name)), "audit_record": copy.deepcopy(audit["items"][name]),
                    "used_history_present": name in set(read_json(self.paths.used))})
            atomic_json(tx / "manifest.json", manifest)
            shutil.copy2(self.paths.analysis, metadata_dir / (self.paths.analysis.name + ".before"))
            shutil.copy2(self.paths.audit, metadata_dir / (self.paths.audit.name + ".before"))
            new_analysis, new_audit = copy.deepcopy(analysis), copy.deepcopy(audit)
            moved = []
            try:
                for count, entry in enumerate(manifest["images"], 1):
                    source, destination = self.paths.generated / entry["basename"], images_dir / entry["basename"]
                    if destination.exists(): raise ReviewError(f"quarantine destination conflict: {entry['basename']}")
                    os.replace(source, destination); moved.append((source, destination))
                    if fail_after_moves is not None and count >= fail_after_moves: raise ReviewError("injected transaction failure")
                    image_hash = entry["sha256"]
                    new_analysis["path_index"].pop(entry["basename"], None); new_analysis.get("file_metadata", {}).pop(entry["basename"], None)
                    new_analysis["items"].pop(image_hash, None)
                    if isinstance(new_analysis.get("current_hashes"), list): new_analysis["current_hashes"] = [value for value in new_analysis["current_hashes"] if value != image_hash]
                    new_audit["items"].pop(entry["basename"], None)
                new_audit["input_count"] = len(new_audit["items"])
                for target, value in ((self.paths.analysis, new_analysis), (self.paths.audit, new_audit)):
                    self._assert_mutable(target); atomic_json(target, value)
                manifest["status"] = "completed"; manifest["completed_at"] = datetime.now(timezone.utc).isoformat(); atomic_json(tx / "manifest.json", manifest)
                return manifest
            except Exception as exc:
                for source, destination in reversed(moved):
                    if destination.exists() and not source.exists(): os.replace(destination, source)
                shutil.copy2(metadata_dir / (self.paths.analysis.name + ".before"), self.paths.analysis)
                shutil.copy2(metadata_dir / (self.paths.audit.name + ".before"), self.paths.audit)
                manifest["status"] = "failed_rolled_back"; manifest["error"] = str(exc); atomic_json(tx / "manifest.json", manifest)
                raise ReviewError(f"quarantine failed and was rolled back: {exc}") from exc

    def transactions(self) -> list[dict]:
        """Return the transactions."""
        result = []
        for path in sorted((self.paths.quarantine / "transactions").glob("*/manifest.json"), reverse=True):
            try: result.append(read_json(path))
            except Exception: continue
        return result

    def restore(self, transaction_id: str, basenames: list[str], confirmation: str) -> dict:
        """Restore selected images from one quarantine transaction."""
        if not self.allow_changes: raise ReviewError("application is read-only; restart with --allow-changes")
        with self._lock:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", transaction_id): raise ReviewError("invalid transaction id")
            source_tx = self.paths.quarantine / "transactions" / transaction_id
            source_manifest = read_json(source_tx / "manifest.json")
            entries = {item["basename"]: item for item in source_manifest.get("images", [])}
            names = list(dict.fromkeys(basenames))
            if not names or any(name not in entries for name in names): raise ReviewError("invalid restore selection")
            expected = f"RESTORE {len(names)} IMAGES"
            if confirmation != expected: raise ReviewError(f"confirmation phrase must be: {expected}")
            for name in names:
                if (self.paths.generated / name).exists(): raise ReviewError(f"active basename conflict: {name}")
                path = source_tx / "images" / name
                if not path.is_file() or sha256(path) != entries[name]["sha256"]: raise ReviewError(f"quarantine hash mismatch: {name}")
            analysis, audit = self._active_documents(); before_a, before_u = copy.deepcopy(analysis), copy.deepcopy(audit); moved = []
            restore_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_restore_" + uuid.uuid4().hex[:8]
            restore_dir = self.paths.quarantine / "transactions" / restore_id; restore_dir.mkdir(parents=True)
            manifest = {"schema_version": 1, "transaction_id": restore_id, "kind": "restore", "source_transaction_id": transaction_id, "status": "pending", "images": names, "created_at": datetime.now(timezone.utc).isoformat(), "restart_required": True}
            atomic_json(restore_dir / "manifest.json", manifest)
            try:
                for name in names:
                    entry = entries[name]; source, destination = source_tx / "images" / name, self.paths.generated / name
                    os.replace(source, destination); moved.append((source, destination)); image_hash = entry["sha256"]
                    analysis["path_index"][name] = image_hash; analysis["items"][image_hash] = entry["analysis_record"]
                    if entry.get("analysis_file_metadata") is not None: analysis.setdefault("file_metadata", {})[name] = entry["analysis_file_metadata"]
                    if isinstance(analysis.get("current_hashes"), list) and image_hash not in analysis["current_hashes"]: analysis["current_hashes"].append(image_hash); analysis["current_hashes"].sort()
                    audit["items"][name] = entry["audit_record"]
                audit["input_count"] = len(audit["items"]); atomic_json(self.paths.analysis, analysis); atomic_json(self.paths.audit, audit)
                manifest["status"] = "completed"; manifest["completed_at"] = datetime.now(timezone.utc).isoformat(); atomic_json(restore_dir / "manifest.json", manifest); return manifest
            except Exception as exc:
                for source, destination in reversed(moved):
                    if destination.exists(): os.replace(destination, source)
                atomic_json(self.paths.analysis, before_a); atomic_json(self.paths.audit, before_u)
                manifest["status"] = "failed_rolled_back"; manifest["error"] = str(exc); atomic_json(restore_dir / "manifest.json", manifest)
                raise ReviewError(f"restore failed and was rolled back: {exc}") from exc

    def image_path(self, status: str, basename: str, transaction_id: str | None = None) -> Path:
        """Return the image path."""
        if not GENERATED_RE.fullmatch(basename): raise ReviewError("invalid image basename")
        if status == "active": path = self.paths.generated / basename
        elif status == "quarantine" and transaction_id and re.fullmatch(r"[A-Za-z0-9_-]+", transaction_id): path = self.paths.quarantine / "transactions" / transaction_id / "images" / basename
        else: raise ReviewError("invalid image location")
        if not path.is_file(): raise ReviewError("image not found")
        return path

    def thumbnail(self, status: str, basename: str, transaction_id: str | None = None) -> Path:
        """Return the thumbnail."""
        source = self.image_path(status, basename, transaction_id)
        digest = sha256(source); directory = self.paths.data / "thumbnails"; target = directory / f"{digest}.jpg"
        self._assert_mutable(target)
        if not target.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            with Image.open(source) as image:
                thumbnail = image.convert("RGB"); thumbnail.thumbnail((480, 360)); thumbnail.save(temporary, "JPEG", quality=85, optimize=True)
            os.replace(temporary, target)
        return target
