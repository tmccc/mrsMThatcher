from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from tools.generated_image_review.app import AppConfig, create_app

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def csrf_client(app):
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get("/")
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
    return client


def make_fixture(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    for quote_hash, quote in [(HASH_A, "Quote A"), (HASH_B, "Quote B"), (HASH_C, "Quote C")]:
        item_dir = corpus / "items" / quote_hash
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "image_01.png").write_bytes(b"png")
        (item_dir / "quote.txt").write_text(quote, encoding="utf-8")
    assessment = tmp_path / "assessment.csv"
    with assessment.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["quote_hash", "grade", "overall_score", "flags", "assessment"])
        writer.writeheader()
        writer.writerow({"quote_hash": HASH_A, "grade": "X", "overall_score": "9", "flags": "visible_text", "assessment": "Review A"})
        writer.writerow({"quote_hash": HASH_B, "grade": "X", "overall_score": "8", "flags": "", "assessment": "Review B"})
        writer.writerow({"quote_hash": HASH_C, "grade": "A", "overall_score": "1", "flags": "", "assessment": "Not eligible"})
    return assessment, corpus


def make_corpus_fixture(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    entries = []
    for quote_hash, quote in [(HASH_A, "Quote A"), (HASH_B, "Quote B")]:
        item_dir = corpus / "items" / quote_hash
        item_dir.mkdir(parents=True)
        (item_dir / "image_01.png").write_bytes(b"png")
        (item_dir / "quote.txt").write_text(quote, encoding="utf-8")
        entries.append({"quote_hash": quote_hash, "text": quote})
    (corpus / "corpus_index.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return corpus


def client_for(tmp_path: Path) -> TestClient:
    assessment, corpus = make_fixture(tmp_path)
    app = create_app(
        AppConfig(
            assessment_file=assessment,
            corpus_root=corpus,
            database=tmp_path / "review.sqlite3",
            export_file=tmp_path / "overrides.json",
            grade="X",
        )
    )
    return csrf_client(app)


def app_for(tmp_path: Path, *, mode: str = "review"):
    assessment, corpus = make_fixture(tmp_path)
    return create_app(
        AppConfig(
            assessment_file=assessment,
            corpus_root=corpus,
            database=tmp_path / "review.sqlite3",
            export_file=tmp_path / "overrides.json",
            grade="X",
            mode=mode,
        )
    )


def test_next_progress_decision_undo_and_export(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.get("/api/next")
        assert response.status_code == 200
        data = response.json()
        assert data["item"]["quote_hash"] == HASH_A
        assert data["progress"] == {"total": 2, "reviewed": 0, "remaining": 2, "allowed": 0, "rejected": 0}

        response = client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"})
        assert response.status_code == 200
        data = response.json()
        assert data["next"]["item"]["quote_hash"] == HASH_B
        assert data["next"]["progress"]["allowed"] == 1

        response = client.post("/api/undo", json={})
        assert response.status_code == 200
        assert response.json()["next"]["item"]["quote_hash"] == HASH_A

        response = client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "reject"})
        assert response.status_code == 200
        response = client.post("/api/export")
        assert response.status_code == 200
        export_data = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
        assert export_data["items"][HASH_A]["decision"] == "reject"


def test_invalid_stale_and_duplicate_decisions_are_rejected(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post("/api/decision", json={"quote_hash": "not-a-hash", "decision": "allow"}).status_code == 404
        assert client.post("/api/decision", json={"quote_hash": HASH_B, "decision": "allow"}).status_code == 409
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "maybe"}).status_code == 400
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 409


def test_image_route_serves_only_known_eligible_hashes(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get(f"/image/{HASH_A}").status_code == 200
        assert client.get(f"/image/{HASH_C}").status_code == 404
        assert client.get("/image/../secret").status_code in {404, 405}


def test_restart_preserves_progress(tmp_path: Path) -> None:
    assessment, corpus = make_fixture(tmp_path)
    config = AppConfig(
        assessment_file=assessment,
        corpus_root=corpus,
        database=tmp_path / "review.sqlite3",
        export_file=tmp_path / "overrides.json",
        grade="X",
    )
    with csrf_client(create_app(config)) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200

    with csrf_client(create_app(config)) as client:
        data = client.get("/api/next").json()
        assert data["item"]["quote_hash"] == HASH_B
        assert data["progress"]["reviewed"] == 1


def test_app_can_start_without_assessment_file_using_corpus_index(tmp_path: Path) -> None:
    corpus = make_corpus_fixture(tmp_path)
    app = create_app(
        AppConfig(
            assessment_file=None,
            corpus_root=corpus,
            database=tmp_path / "review.sqlite3",
            export_file=tmp_path / "overrides.json",
            grade="X",
        )
    )

    with csrf_client(app) as client:
        data = client.get("/api/next").json()
        assert data["item"]["quote_hash"] == HASH_A
        assert data["item"]["grade"] == "unreviewed"
        assert data["progress"]["total"] == 2


def test_confirm_allowed_mode_reviews_only_initial_allows(tmp_path: Path) -> None:
    with csrf_client(app_for(tmp_path, mode="review")) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        assert client.post("/api/decision", json={"quote_hash": HASH_B, "decision": "reject"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="confirm-allowed")) as client:
        data = client.get("/api/next").json()
        assert data["mode"] == "confirm-allowed"
        assert data["progress"] == {"total": 1, "reviewed": 0, "remaining": 1, "allowed": 0, "rejected": 0}
        assert data["item"]["quote_hash"] == HASH_A

        response = client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "reject"})
        assert response.status_code == 200
        data = response.json()["next"]
        assert data["complete"] is True
        assert data["progress"]["rejected"] == 1

        export_data = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
        assert export_data["items"][HASH_A]["decision"] == "reject"
        assert export_data["items"][HASH_A]["initial_decision"] == "allow"
        assert export_data["items"][HASH_A]["confirmation_decision"] == "reject"
        assert export_data["items"][HASH_B]["decision"] == "reject"
        assert "confirmation_decision" not in export_data["items"][HASH_B]


def test_confirm_allowed_undo_restores_confirmation_item_not_initial_queue(tmp_path: Path) -> None:
    with csrf_client(app_for(tmp_path, mode="review")) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="confirm-allowed")) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        data = client.post("/api/undo", json={}).json()
        assert data["undone"]["quote_hash"] == HASH_A
        assert data["undone"]["stage"] == "confirm_allowed"
        assert data["next"]["item"]["quote_hash"] == HASH_A


def test_reconfirm_allowed_mode_reviews_only_confirmation_allows(tmp_path: Path) -> None:
    with csrf_client(app_for(tmp_path, mode="review")) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        assert client.post("/api/decision", json={"quote_hash": HASH_B, "decision": "allow"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="confirm-allowed")) as client:
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        assert client.post("/api/decision", json={"quote_hash": HASH_B, "decision": "reject"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="reconfirm-allowed")) as client:
        data = client.get("/api/next").json()
        assert data["mode"] == "reconfirm-allowed"
        assert data["progress"] == {"total": 1, "reviewed": 0, "remaining": 1, "allowed": 0, "rejected": 0}
        assert data["item"]["quote_hash"] == HASH_A

        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "reject"}).status_code == 200
        export_data = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
        assert export_data["items"][HASH_A]["decision"] == "reject"
        assert export_data["items"][HASH_A]["reconfirmation_decision"] == "reject"
        assert export_data["items"][HASH_B]["decision"] == "reject"
        assert "reconfirmation_decision" not in export_data["items"][HASH_B]


def test_confirmation_rereview_invalidates_reconfirmation_queue_and_export(tmp_path: Path) -> None:
    for mode in ("review", "confirm-allowed", "reconfirm-allowed"):
        with csrf_client(app_for(tmp_path, mode=mode)) as client:
            assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="confirm-allowed")) as client:
        response = client.post("/api/undo", json={})
        assert response.status_code == 200
        assert response.json()["next"]["item"]["quote_hash"] == HASH_A
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "reject"}).status_code == 200
        exported = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))["items"][HASH_A]
        assert exported["decision"] == "reject"
        assert "reconfirmation_decision" not in exported

    with csrf_client(app_for(tmp_path, mode="reconfirm-allowed")) as client:
        data = client.get("/api/next").json()
        assert data["complete"] is True
        assert data["progress"]["total"] == 0

    with csrf_client(app_for(tmp_path, mode="confirm-allowed")) as client:
        assert client.post("/api/undo", json={}).status_code == 200
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200

    with csrf_client(app_for(tmp_path, mode="reconfirm-allowed")) as client:
        data = client.get("/api/next").json()
        assert data["item"]["quote_hash"] == HASH_A
        assert data["progress"]["remaining"] == 1
