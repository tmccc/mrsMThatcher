from __future__ import annotations

import base64
import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tools.generated_image_review_app.app import create_app, validate_binding
from tools.generated_image_review_app.services import Paths, ReviewError, ReviewService, sha256


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path: Path, count: int = 3, allow: bool = False) -> tuple[ReviewService, list[str]]:
    project = tmp_path / "project"; generated = project / "generated_review_approved_images"; generated.mkdir(parents=True)
    names, items, path_index, metadata, hashes, audits = [], {}, {}, {}, [], {}
    for index in range(count):
        origin = f"{index + 1:064x}"; name = f"tg_{origin}.png"; path = generated / name
        Image.new("RGB", (80 + index, 60), (40 * index, 80, 120)).save(path); digest = sha256(path); names.append(name); hashes.append(digest)
        path_index[name] = digest; metadata[name] = {"size_bytes": path.stat().st_size, "suffix": ".png"}
        items[digest] = {"analysis": {"overall_editorial_utility": 7 + index, "image_summary": f"summary {index}"}}
        audits[name] = {"basename": name, "image_sha256": digest, "origin_quote_hash": origin, "origin_quote": f"quote {index}",
                        "analysis": {"recommended_cross_quote_policy": "origin_quote_only" if index == 0 else "unrestricted", "identity_dependence": "high" if index == 0 else "low", "recognisability_to_typical_viewer": 5 + index, "meaning_retention_without_identity": 6 + index}}
    dump(project / "generated_image_analysis.json", {"schema_version": 3, "analysis_kind": "images", "items": items, "path_index": path_index, "file_metadata": metadata, "current_hashes": hashes})
    dump(project / "generated_image_identity_dependence_audit.json", {"schema_version": 1, "analysis_kind": "generated_image_identity_dependence_audit", "input_count": count, "items": audits})
    dump(project / "images_used.json", [names[0]]); dump(project / "bot_state.json", {"sentinel": 1}); dump(project / "lines_used.json", ["line"])
    (project / "mrsMThatcher.log").write_text("production\n"); dump(project / "mrsMThatcher.local.json", {"sentinel": 1}); dump(project / "regular_post_receipt.json", {"sentinel": 1})
    return ReviewService(Paths.build(project), allow), names


def csrf(client: TestClient) -> str:
    response = client.get("/"); assert response.status_code == 200; return client.cookies["review_csrf"]


def test_index_metadata_filters_sorts_search_and_quarantine_index(tmp_path):
    service, names = fixture(tmp_path, allow=True); rows = service.index()
    assert len(rows) == 3 and rows[0]["posted"] and rows[0]["policy"] == "origin_quote_only"
    assert service.filter_sort(rows, query="quote 1")[0]["basename"] == names[1]
    assert len(service.filter_sort(rows, policy="origin_quote_only")) == 1
    assert service.filter_sort(rows, posted="yes")[0]["basename"] == names[0]
    token = service.pool_token(); service.quarantine([names[0]], token, "QUARANTINE 1 IMAGES", "poor likeness")
    quarantined = service.filter_sort(service.index(), status="quarantined")
    assert len(quarantined) == 1 and quarantined[0]["basename"] == names[0]


def test_missing_and_stale_metadata_reported(tmp_path):
    service, names = fixture(tmp_path); analysis = json.load(open(service.paths.analysis)); analysis["path_index"].pop(names[0]); dump(service.paths.analysis, analysis)
    row = next(row for row in service.index() if row["basename"] == names[0]); assert row["metadata_status"] == "missing" and row["hash_status"] == "stale"


def test_path_traversal_blocked(tmp_path):
    service, _ = fixture(tmp_path)
    with pytest.raises(ReviewError): service.image_path("active", "../bot_state.json")
    with pytest.raises(ReviewError): service.image_path("quarantine", "tg_" + "a" * 64 + ".png", "../bad")


def test_thumbnail_is_hash_keyed_cached_and_source_unchanged(tmp_path):
    service, names = fixture(tmp_path); source = service.paths.generated / names[0]; before = source.read_bytes()
    first = service.thumbnail("active", names[0]); second = service.thumbnail("active", names[0])
    assert first == second and first.name == sha256(source) + ".jpg" and first.is_file() and source.read_bytes() == before


def test_read_only_quarantine_and_restore_refused_without_writes(tmp_path):
    service, names = fixture(tmp_path); before = {p: p.read_bytes() for p in service.paths.project.rglob("*") if p.is_file()}
    with pytest.raises(ReviewError, match="read-only"): service.quarantine([names[0]], service.pool_token(), "QUARANTINE 1 IMAGES", "other")
    with pytest.raises(ReviewError, match="read-only"): service.restore("x", [names[0]], "RESTORE 1 IMAGES")
    assert before == {p: p.read_bytes() for p in service.paths.project.rglob("*") if p.is_file()}


def test_lan_requires_auth_and_auth_secrets_not_exposed(tmp_path):
    with pytest.raises(ValueError, match="requires"): validate_binding("0.0.0.0", None, None)
    validate_binding("0.0.0.0", "owner", "secret")
    service, _ = fixture(tmp_path); client = TestClient(create_app(service, username="owner", password="secret", secret_key="key"))
    assert client.get("/").status_code == 401
    header = {"Authorization": "Basic " + base64.b64encode(b"owner:secret").decode()}
    response = client.get("/", headers=header); assert response.status_code == 200 and "secret" not in response.text


def test_csrf_and_readonly_endpoints(tmp_path):
    service, names = fixture(tmp_path); client = TestClient(create_app(service, secret_key="key")); token = csrf(client)
    response = client.post("/review", data={"csrf": "bad", "pool_token": service.pool_token(), "images": names[0]})
    assert response.status_code == 403
    review = client.post("/review", data={"csrf": token, "pool_token": service.pool_token(), "images": names[0]})
    assert review.status_code == 200 and names[0] in review.text and "QUARANTINE 1 IMAGES" in review.text
    result = client.post("/quarantine", data={"csrf": token, "pool_token": service.pool_token(), "images": names[0], "confirmation": "QUARANTINE 1 IMAGES", "reason": "other"})
    assert "read-only" in result.text and (service.paths.generated / names[0]).exists()


def test_quarantine_multi_metadata_manifest_backups_and_protected_files_untouched(tmp_path):
    service, names = fixture(tmp_path, allow=True)
    protected = [service.paths.used, service.paths.project / "bot_state.json", service.paths.project / "lines_used.json", service.paths.project / "mrsMThatcher.log", service.paths.project / "mrsMThatcher.local.json", service.paths.project / "regular_post_receipt.json"]
    before = {path: path.read_bytes() for path in protected}; result = service.quarantine(names[:2], service.pool_token(), "QUARANTINE 2 IMAGES", "repetitive", "note")
    assert result["status"] == "completed" and all(not (service.paths.generated / name).exists() for name in names[:2])
    tx = service.paths.quarantine / "transactions" / result["transaction_id"]
    assert (tx / "manifest.json").is_file() and (tx / "metadata" / "generated_image_analysis.json.before").is_file()
    analysis, audit = json.load(open(service.paths.analysis)), json.load(open(service.paths.audit))
    assert all(name not in analysis["path_index"] and name not in audit["items"] for name in names[:2]) and audit["input_count"] == 1
    assert before == {path: path.read_bytes() for path in protected}


def test_stale_review_and_confirmation_rejected(tmp_path):
    service, names = fixture(tmp_path, allow=True); token = service.pool_token()
    (service.paths.generated / names[1]).touch()
    # Content token hashes content, so change content to make it stale.
    (service.paths.generated / names[1]).write_bytes(b"changed")
    with pytest.raises(ReviewError, match="stale"): service.quarantine([names[0]], token, "QUARANTINE 1 IMAGES", "other")
    service, names = fixture(tmp_path / "second", allow=True)
    with pytest.raises(ReviewError, match="confirmation"): service.quarantine([names[0]], service.pool_token(), "wrong", "other")


def test_quarantine_rollback_on_injected_failure(tmp_path):
    service, names = fixture(tmp_path, allow=True); analysis_before = service.paths.analysis.read_bytes(); audit_before = service.paths.audit.read_bytes()
    with pytest.raises(ReviewError, match="rolled back"): service.quarantine(names[:2], service.pool_token(), "QUARANTINE 2 IMAGES", "other", fail_after_moves=1)
    assert all((service.paths.generated / name).is_file() for name in names) and service.paths.analysis.read_bytes() == analysis_before and service.paths.audit.read_bytes() == audit_before
    assert service.transactions()[0]["status"] == "failed_rolled_back"


def test_restore_exact_metadata_history_preserved_and_conflict_blocked(tmp_path):
    service, names = fixture(tmp_path, allow=True); original_analysis = json.load(open(service.paths.analysis)); original_audit = json.load(open(service.paths.audit)); used_before = service.paths.used.read_bytes()
    tx = service.quarantine([names[0]], service.pool_token(), "QUARANTINE 1 IMAGES", "other")
    restored = service.restore(tx["transaction_id"], [names[0]], "RESTORE 1 IMAGES")
    assert restored["status"] == "completed" and json.load(open(service.paths.analysis)) == original_analysis and json.load(open(service.paths.audit)) == original_audit
    assert service.paths.used.read_bytes() == used_before
    # A newer active basename blocks restore without overwrite.
    tx2 = service.quarantine([names[0]], service.pool_token(), "QUARANTINE 1 IMAGES", "other")
    (service.paths.generated / names[0]).write_bytes(b"newer")
    with pytest.raises(ReviewError, match="conflict"): service.restore(tx2["transaction_id"], [names[0]], "RESTORE 1 IMAGES")


def test_no_network_or_simulator_dependencies(tmp_path, monkeypatch):
    service, _ = fixture(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    assert len(service.index()) == 3


def test_ui_result_and_restore_workflow(tmp_path):
    service, names = fixture(tmp_path, allow=True); client = TestClient(create_app(service, secret_key="key")); token = csrf(client); pool = service.pool_token()
    review = client.post("/review", data={"csrf": token, "pool_token": pool, "images": [names[0], names[1]]})
    assert review.status_code == 200 and "Review 2 selected images" in review.text
    result = client.post("/quarantine", data={"csrf": token, "pool_token": pool, "images": [names[0], names[1]], "confirmation": "QUARANTINE 2 IMAGES", "reason": "other"})
    assert result.status_code == 200 and "Transaction completed" in result.text
    history = client.get("/quarantine"); assert names[0] in history.text and "RESTORE N IMAGES" in history.text
