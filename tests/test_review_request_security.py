"""Exercise the shared LAN request boundary and legacy application adapter offline."""
from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tools.generated_image_review_app.security import ReviewSecurity, validate_binding
from tools.generated_image_review.app import AppConfig, configured_app, create_app, parse_args
from tools.generated_image_review.tests.test_app_api import HASH_A, HASH_B, make_fixture


def app(*, username=None, password=None, **kwargs):
    service = FastAPI()
    service.add_middleware(ReviewSecurity, username=username, password=password, **kwargs)
    @service.get("/")
    def index(): return {"ok": True}
    @service.post("/change")
    def change(): return {"changed": True}
    return service


def test_lan_plaintext_and_partial_credentials_fail_closed():
    with pytest.raises(ValueError): validate_binding("0.0.0.0", "owner", "secret")
    with pytest.raises(ValueError): validate_binding("127.0.0.1", "owner", None)
    validate_binding("127.0.0.1", None, None)
    validate_binding("0.0.0.0", "owner", "secret", tls=True)
    validate_binding("0.0.0.0", "owner", "secret", trusted_proxy_origin="https://review.example")
    with pytest.raises(ValueError):
        validate_binding("0.0.0.0", "owner", "secret", trusted_proxy_origin="http://review.example")


def test_nonloopback_runtime_requires_auth_and_tls_even_when_factory_bypasses_cli():
    client = TestClient(app(allowed_hosts=("review.example",)), base_url="https://review.example")
    assert client.get("/").status_code == 401
    client = TestClient(app(username="owner", password="secret", allowed_hosts=("review.example",)), base_url="http://review.example")
    assert client.get("/", auth=("owner", "secret")).status_code == 403
    client = TestClient(app(username="owner", password="secret", allowed_hosts=("review.example",)), base_url="https://review.example")
    assert client.get("/", auth=("owner", "secret")).status_code == 200


def test_signed_csrf_origin_host_and_security_headers():
    client = TestClient(app(), base_url="http://127.0.0.1")
    response = client.get("/")
    token = response.headers["x-csrf-token"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/change").status_code == 403
    assert client.post("/change", headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.post("/change", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/change", headers={"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/change", headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1"}).status_code == 200
    assert client.post("/change", headers={"Content-Length": "1048577"}).status_code == 413
    assert client.post("/change", content=b"x" * 1048577).status_code == 413


def test_malformed_non_ascii_credentials_return_401():
    client = TestClient(app(username="owner", password="secret"), base_url="http://127.0.0.1")
    encoded = base64.b64encode("é:secret".encode()).decode()
    assert client.get("/", headers={"Authorization": "Basic " + encoded}).status_code == 401
    assert client.get("/", headers={"Authorization": "Basic not-base64"}).status_code == 401


def test_legacy_adapter_protects_images_decisions_and_post_export(tmp_path):
    assessment, corpus = make_fixture(tmp_path)
    service = create_app(AppConfig(assessment, corpus, tmp_path / "decisions.db", tmp_path / "export.json"), username="owner", password="secret")
    client = TestClient(service, base_url="http://127.0.0.1")
    assert client.get("/").status_code == 401
    assert client.get("/image/" + "a" * 64).status_code == 401
    mutations = (
        ("/api/decision", {"quote_hash": HASH_B, "decision": "reject"}),
        ("/api/undo", {}),
        ("/api/export", {}),
    )
    for path, body in mutations:
        assert client.post(path, json=body).status_code == 401
    assert service.state.store.active_decisions() == {}
    assert not (tmp_path / "export.json").exists()
    client.auth = ("owner", "secret")
    first = client.get("/")
    token = first.headers["x-csrf-token"]
    assert client.get("/api/export").status_code == 405
    assert not (tmp_path / "export.json").exists()
    assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}, headers={"X-CSRF-Token": token}).status_code == 200
    decisions = service.state.store.active_decisions()
    exported = (tmp_path / "export.json").read_bytes()
    for path, body in mutations:
        assert client.post(path, json=body).status_code == 403
        assert client.post(path, json=body, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
        assert service.state.store.active_decisions() == decisions
        assert (tmp_path / "export.json").read_bytes() == exported
    assert client.post("/api/undo", headers={"X-CSRF-Token": token}).status_code == 200
    assert service.state.store.active_decisions() == {}
    assert client.post("/api/export", headers={"X-CSRF-Token": token}).status_code == 200
    assert json.loads((tmp_path / "export.json").read_text())["items"] == {}


@pytest.fixture
def swipe_environment(tmp_path, monkeypatch):
    assessment, corpus = make_fixture(tmp_path)
    for name in ("GIR_HOST", "MRS_REVIEW_USERNAME", "MRS_REVIEW_PASSWORD", "MRS_REVIEW_PUBLIC_HOST", "MRS_REVIEW_TRUSTED_PROXY_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "GIR_ASSESSMENT_FILE": assessment, "GIR_CORPUS_ROOT": corpus,
        "GIR_DATABASE": tmp_path / "decisions.db", "GIR_EXPORT_FILE": tmp_path / "export.json",
        "GIR_MODE": "review", "GIR_GRADE": "X",
    }.items():
        monkeypatch.setenv(name, str(value))


def test_swipe_factory_keeps_local_default_and_rejects_forged_local_host_on_lan(swipe_environment, monkeypatch):
    monkeypatch.setattr("sys.argv", ["app.py"])
    assert parse_args().host == "127.0.0.1"
    service = configured_app()
    with TestClient(service, base_url="http://127.0.0.1") as client:
        token = client.get("/").headers["x-csrf-token"]

        async def lan_listener(scope, receive, send):
            scope["server"] = ("192.168.1.20", 8765)
            scope["client"] = ("192.168.1.50", 51000)
            await service(scope, receive, send)

        remote = TestClient(lan_listener, base_url="http://127.0.0.1")
        remote.cookies.update(client.cookies)
        remote.headers["X-CSRF-Token"] = token
        for path in ("/api/decision", "/api/undo", "/api/export"):
            assert remote.post(path, json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 401
        assert service.state.store.active_decisions() == {}
        assert not service.state.config.export_file.exists()


def test_swipe_factory_accepts_authenticated_proxy_host_and_protects_mutations(swipe_environment, monkeypatch):
    monkeypatch.setenv("MRS_REVIEW_USERNAME", "owner")
    monkeypatch.setenv("MRS_REVIEW_PASSWORD", "secret")
    monkeypatch.setenv("MRS_REVIEW_PUBLIC_HOST", "review.example")
    monkeypatch.setenv("MRS_REVIEW_TRUSTED_PROXY_ORIGIN", "https://review.example")
    monkeypatch.setattr("sys.argv", ["app.py"])
    args = parse_args()
    assert args.public_host == "review.example"
    assert args.trusted_proxy_origin == "https://review.example"
    with TestClient(configured_app(), base_url="https://review.example") as client:
        assert client.get("/").status_code == 401
        client.auth = ("owner", "secret")
        response = client.get("/")
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.headers["X-CSRF-Token"]
        assert client.post("/api/decision", json={"quote_hash": HASH_A, "decision": "allow"}).status_code == 200
        assert client.post("/api/undo", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/undo", headers={"Origin": "https://review.example"}).status_code == 200
        assert client.get("/api/export").status_code == 405
        assert client.post("/api/export").status_code == 200
