from fastapi.testclient import TestClient
import pytest

from desk import http_boundary
from desk.api import app
from desk.settings import secret


def test_body_limit_handles_fixed_and_streamed_requests(monkeypatch):
    client = TestClient(app, base_url="http://localhost")
    body = b'{"persona":"merchant"}'
    headers = {"content-type": "application/json"}
    path = "/api/demo/session"
    monkeypatch.setattr(http_boundary, "METADATA_REQUEST_LIMIT", len(body))
    assert client.post(path, content=body, headers=headers).status_code == 200
    monkeypatch.setattr(http_boundary, "METADATA_REQUEST_LIMIT", len(body) - 1)
    for content in (body, iter([body[:8], body[8:]])):
        reply = client.post(path, content=content, headers=headers)
        assert reply.status_code == 413
        assert reply.headers["cache-control"] == "no-store"


def test_session_origin_host_and_cache_policy():
    client = TestClient(app, base_url="http://localhost")
    body = {"persona": "operator"}
    assert (
        client.post(
            "/api/demo/session", json=body, headers={"origin": "http://other.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/session", headers={"host": "elsewhere.example"}).status_code == 400
    assert client.get("/api/session").json()["role"] == "operator"
    client.cookies.clear()
    client.cookies.set("desk_session", "expired-demo-cookie")
    reply = client.get("/api/session")
    assert reply.json()["role"] == "merchant"
    assert reply.headers["cache-control"] == "no-store"
    assert reply.headers["cross-origin-resource-policy"] == "same-origin"
    assert "httponly" in reply.headers["set-cookie"].lower()
    assert "samesite=strict" in reply.headers["set-cookie"].lower()


def test_stored_signing_key_requires_complete_material(tmp_path, monkeypatch):
    monkeypatch.setenv("KEY_DIRECTORY", str(tmp_path))
    (tmp_path / "demo.key").write_bytes(b"partial")
    with pytest.raises(RuntimeError, match="incomplete"):
        secret("session")
