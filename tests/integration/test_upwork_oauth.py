"""Upwork OAuth 2.1 flow against a mock authorization server (no real Upwork account needed)."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine

pytestmark = pytest.mark.anyio


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    from apps.api import db
    from apps.api.auth import upwork_oauth
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{tmp_path / 'o.sqlite3'}"))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UPWORK_MCP_URL", "https://mcp.example.test/mcp")
    monkeypatch.delenv("UPWORK_OAUTH_CLIENT_ID", raising=False)
    seen = {"challenge": None, "tokens": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json={"resource": "https://mcp.example.test/mcp",
                                             "authorization_servers": ["https://auth.example.test"]})
        if u.endswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json={"authorization_endpoint": "https://auth.example.test/authorize",
                                             "token_endpoint": "https://auth.example.test/token",
                                             "registration_endpoint": "https://auth.example.test/register"})
        if u.endswith("/register"):
            return httpx.Response(201, json={"client_id": "dyn-client-1"})
        if u.endswith("/token"):
            form = parse_qs(req.content.decode())
            seen["tokens"] += 1
            if form["grant_type"] == ["authorization_code"]:
                v = form["code_verifier"][0]
                calc = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
                assert calc == seen["challenge"], "PKCE verifier must match the challenge"
                return httpx.Response(200, json={"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "AT-2", "expires_in": 3600})
        return httpx.Response(404)

    monkeypatch.setattr(upwork_oauth, "_transport", httpx.MockTransport(handler))
    return upwork_oauth, seen, db


async def test_full_pkce_flow_encrypted_storage_and_refresh(oauth):
    upwork_oauth, seen, db = oauth
    from apps.api.auth import token_store
    url = await upwork_oauth.start("u1")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["dyn-client-1"] and q["code_challenge_method"] == ["S256"]
    seen["challenge"] = q["code_challenge"][0]
    assert await upwork_oauth.callback("the-code", q["state"][0]) == "u1"
    row = db.one("SELECT access_ct FROM oauth_tokens WHERE tenant_id='u1'")
    assert "AT-1" not in row["access_ct"]                         # encrypted at rest
    assert await token_store.get_upwork_access_token("u1") == "AT-1"
    with pytest.raises(PermissionError):                          # state is single use
        await upwork_oauth.callback("the-code", q["state"][0])
    db.q("UPDATE oauth_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE tenant_id='u1'")
    assert await token_store.get_upwork_access_token("u1") == "AT-2"   # refreshed transparently
    assert token_store.status("u2", "upwork")["connected"] is False    # tenant scoped
