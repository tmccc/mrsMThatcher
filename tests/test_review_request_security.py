"""Exercise the shared LAN request boundary and legacy application adapter offline."""
from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tools.generated_image_review_app.security import ReviewSecurity, validate_binding
from tools.generated_image_review.app import AppConfig, create_app
from tools.generated_image_review.tests.test_app_api import make_fixture


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
    assert client.post("/api/export").status_code == 401
    client.auth = ("owner", "secret")
    first = client.get("/")
    token = first.headers["x-csrf-token"]
    assert client.get("/api/export").status_code == 405
    assert not (tmp_path / "export.json").exists()
    assert client.post("/api/export").status_code == 403
    assert client.post("/api/export", headers={"X-CSRF-Token": token}).status_code == 200
    assert (tmp_path / "export.json").exists()
