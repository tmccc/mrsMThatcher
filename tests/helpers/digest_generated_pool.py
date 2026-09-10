"""Synthetic generated-image pools, quarantine transactions and posting logs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import copy
import hashlib
import json

import mrs_log_digest as digest


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value), encoding="utf-8")


def pool(tmp_path: Path, count: int = 4) -> tuple[Path, list[str]]:
    base = tmp_path / "project"; generated = base / "generated_review_approved_images"; generated.mkdir(parents=True)
    path_index, items, audit_items, names = {}, {}, {}, []
    policies = list(digest.GENERATED_POLICIES)
    for index in range(count):
        origin = f"{index + 1:064x}"; name = f"tg_{origin}.png"; path = generated / name; path.write_bytes(f"image-{index}".encode()); image_hash = hashlib.sha256(path.read_bytes()).hexdigest(); names.append(name)
        path_index[name] = image_hash; items[image_hash] = {"analysis": {"summary": str(index)}}
        audit_items[name] = {"basename": name, "image_sha256": image_hash, "origin_quote_hash": origin, "analysis": {
            "recommended_cross_quote_policy": policies[index % len(policies)], "identity_dependence": "low",
            "contains_specific_intended_person": False, "recognisability_to_typical_viewer": 5,
            "recognisability_to_politically_interested_viewer": 5, "meaning_retention_without_identity": 5,
            "origin_quote_suitability": 5, "recommended_penalty_strength": 0, "confidence": 1.0,
        }}
    dump(base / "generated_image_analysis.json", {"schema_version": 3, "analysis_kind": "images", "path_index": path_index, "items": items})
    dump(base / "generated_image_identity_dependence_audit.json", {"schema_version": 1, "analysis_kind": "generated_image_identity_dependence_audit", "items": audit_items})
    dump(base / "images_used.json", [names[0], "t01.jpg"])
    return base, names


def quarantine(base: Path, names: list[str], txid: str = "20260710T120000Z_test", status: str = "completed") -> Path:
    analysis = json.load(open(base / "generated_image_analysis.json")); audit = json.load(open(base / "generated_image_identity_dependence_audit.json"))
    directory = base / "generated_image_quarantine" / "transactions" / txid; (directory / "images").mkdir(parents=True)
    entries = []
    for name in names:
        image_hash = analysis["path_index"][name]; source = base / "generated_review_approved_images" / name
        entries.append({"basename": name, "original_relative_path": f"generated_review_approved_images/{name}", "sha256": image_hash,
                        "analysis_record": copy.deepcopy(analysis["items"][image_hash]), "audit_record": copy.deepcopy(audit["items"][name])})
        if status == "completed": source.rename(directory / "images" / name); analysis["path_index"].pop(name); analysis["items"].pop(image_hash); audit["items"].pop(name)
    dump(directory / "manifest.json", {"transaction_id": txid, "kind": "quarantine", "status": status, "created_at": "2026-07-10T12:00:00+00:00", "images": entries})
    if status == "completed": dump(base / "generated_image_analysis.json", analysis); dump(base / "generated_image_identity_dependence_audit.json", audit)
    return directory


def log_line(ts: datetime, message: str) -> str:
    return f"{ts.strftime('%Y-%m-%d %H:%M:%S')} INFO     test:1 - {message}\n"


def post(ts: datetime, post_id: str, basename: str) -> str:
    event = {"event": "main_post_posted", "lane": "quote_image", "post_id": post_id, "image_basename": basename}
    return log_line(ts, "EVENT " + json.dumps(event, separators=(",", ":")))
