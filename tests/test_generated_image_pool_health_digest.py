from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

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


def report_text(snapshot: dict) -> str:
    report = digest.analyse([]); report["generated_image_pool_health"] = snapshot
    return digest.render_markdown(report)


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


def test_healthy_active_only_pool_and_output(tmp_path):
    base, names = pool(tmp_path); snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["active_generated_images"] == 4 and snapshot["quarantined_generated_images"] == 0
    assert snapshot["metadata_coverage"] == "complete" and snapshot["hash_valid"] == 4 and snapshot["health"] == "OK"
    assert snapshot["active_policy_counts"] == dict(zip(digest.GENERATED_POLICIES, [1, 1, 1, 1]))
    assert snapshot["active_previously_used"] == 1 and snapshot["active_never_used"] == 3
    text = report_text(snapshot); assert "## Generated image pool health" in text and "Current filesystem snapshot" in text and "Latest completed quarantine: none" in text


def test_healthy_quarantine_excludes_active_and_preserves_accounting(tmp_path):
    base, names = pool(tmp_path, 8); directory = quarantine(base, names[:4]); snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["active_generated_images"] == 4 and snapshot["quarantined_generated_images"] == 4 and snapshot["total_known_generated_images"] == 8
    assert snapshot["active_analysis_records"] == 4 and snapshot["active_identity_records"] == 4 and snapshot["health"] == "OK"
    assert snapshot["quarantined_previously_used"] == 1 and snapshot["quarantined_never_used"] == 3
    assert snapshot["latest_quarantine"] == {"transaction_id": directory.name, "timestamp": "2026-07-10T12:00:00+00:00", "image_count": 4}
    assert sum(snapshot["active_policy_counts"].values()) == 4 and sum(snapshot["quarantined_policy_counts"].values()) == 4


def test_restored_image_not_currently_quarantined_and_restore_reported(tmp_path):
    base, names = pool(tmp_path); directory = quarantine(base, [names[0]]); entry = json.load(open(directory / "manifest.json"))["images"][0]
    image = directory / "images" / names[0]; image.rename(base / "generated_review_approved_images" / names[0])
    analysis = json.load(open(base / "generated_image_analysis.json")); audit = json.load(open(base / "generated_image_identity_dependence_audit.json")); analysis["path_index"][names[0]] = entry["sha256"]; analysis["items"][entry["sha256"]] = entry["analysis_record"]; audit["items"][names[0]] = entry["audit_record"]; dump(base / "generated_image_analysis.json", analysis); dump(base / "generated_image_identity_dependence_audit.json", audit)
    restore = base / "generated_image_quarantine" / "transactions" / "restore_1"; dump(restore / "manifest.json", {"transaction_id": "restore_1", "kind": "restore", "status": "completed", "created_at": "2026-07-10T13:00:00+00:00", "images": [names[0]]})
    snapshot = digest.generated_pool_health_snapshot(base); assert snapshot["quarantined_generated_images"] == 0 and snapshot["completed_restore_transactions"] == 1 and snapshot["latest_restore"]["transaction_id"] == "restore_1"


def test_failed_rolled_back_transaction_not_counted(tmp_path):
    base, names = pool(tmp_path); quarantine(base, [names[0]], status="failed_rolled_back"); snapshot = digest.generated_pool_health_snapshot(base)
    assert snapshot["quarantined_generated_images"] == 0 and snapshot["completed_quarantine_transactions"] == 0 and snapshot["health"] == "OK"


def test_metadata_problems_continue_and_render_capped_warnings(tmp_path):
    base, names = pool(tmp_path, 4); analysis = json.load(open(base / "generated_image_analysis.json")); audit = json.load(open(base / "generated_image_identity_dependence_audit.json"))
    analysis["path_index"].pop(names[0]); audit["items"].pop(names[1]); audit["items"]["tg_" + "f" * 64 + ".png"] = {"analysis": {"recommended_cross_quote_policy": "bad"}}
    (base / "generated_review_approved_images" / names[2]).write_bytes(b"stale"); audit["items"][names[3]]["analysis"]["recommended_cross_quote_policy"] = "bad"
    dump(base / "generated_image_analysis.json", analysis); dump(base / "generated_image_identity_dependence_audit.json", audit)
    snapshot = digest.generated_pool_health_snapshot(base); kinds = {item["kind"] for item in snapshot["warnings"]}
    assert {"missing_analysis", "missing_audit", "unexpected_audit", "hash_mismatch", "invalid_policy"} <= kinds and snapshot["health"] == "WARNING"
    snapshot["warnings"] = snapshot["warnings"] * 6; text = report_text(snapshot); assert "additional warning(s) omitted" in text


def test_malformed_json_and_missing_quarantine_are_nonfatal(tmp_path):
    base, _ = pool(tmp_path); (base / "generated_image_analysis.json").write_text("{"); (base / "images_used.json").write_text("bad")
    snapshot = digest.generated_pool_health_snapshot(base); kinds = {item["kind"] for item in snapshot["warnings"]}
    assert "analysis_malformed" in kinds and "used_history_malformed" in kinds and snapshot["quarantined_generated_images"] == 0


def test_malformed_quarantine_images_field_is_nonfatal(tmp_path):
    base, _ = pool(tmp_path)
    manifest = base / "generated_image_quarantine" / "transactions" / "bad" / "manifest.json"
    dump(manifest, {
        "transaction_id": "bad",
        "kind": "quarantine",
        "status": "completed",
        "created_at": "2026-07-10T12:00:00+00:00",
        "images": 1,
    })

    snapshot = digest.generated_pool_health_snapshot(base)

    assert snapshot["health"] == "WARNING"
    assert snapshot["completed_quarantine_transactions"] == 0
    assert any(item["kind"] == "transaction_schema_invalid" for item in snapshot["warnings"])


def test_incomplete_quarantine_record_warns(tmp_path):
    base, names = pool(tmp_path); directory = quarantine(base, [names[0]]); manifest = json.load(open(directory / "manifest.json")); manifest["images"][0].pop("analysis_record"); dump(directory / "manifest.json", manifest)
    snapshot = digest.generated_pool_health_snapshot(base); assert any(item["kind"] == "incomplete_quarantine" for item in snapshot["warnings"])
