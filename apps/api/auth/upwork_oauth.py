"""OAuth 2.1 (authorization code + PKCE) for Upwork's official MCP server (A4).

Discovery follows the MCP authorization spec: the MCP server's protected-resource metadata (RFC 9728) names the
authorization server, whose metadata (RFC 8414) gives the endpoints. If UPWORK_OAUTH_CLIENT_ID is not configured
and the server offers dynamic client registration (RFC 7591), GigBid registers itself once and caches the client.
No browser automation, no session cookies: the user signs in on Upwork and consents.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode, urlparse

import httpx

from apps.api import db
from apps.api.auth import token_store

PROVIDER = "upwork"
SCOPES = os.environ.get("UPWORK_OAUTH_SCOPES", "")
_transport: httpx.AsyncBaseTransport | None = None          # tests inject a MockTransport

STATE_DDL = """CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    verifier TEXT NOT NULL, created_at TEXT NOT NULL)"""
CLIENT_DDL = """CREATE TABLE IF NOT EXISTS oauth_clients (provider TEXT PRIMARY KEY, client_json TEXT NOT NULL)"""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, transport=_transport, follow_redirects=True)


async def discover() -> dict:
    mcp_url = os.environ.get("UPWORK_MCP_URL", "https://mcp.upwork.com/mcp")
    origin = "{0.scheme}://{0.netloc}".format(urlparse(mcp_url))
    async with _client() as c:
        prm = (await c.get(f"{origin}/.well-known/oauth-protected-resource")).json()
        issuer = prm["authorization_servers"][0].rstrip("/")
        meta = (await c.get(f"{issuer}/.well-known/oauth-authorization-server")).json()
    return {"resource": prm.get("resource", mcp_url), **meta}


async def client_credentials(meta: dict, redirect_uri: str) -> dict:
    if os.environ.get("UPWORK_OAUTH_CLIENT_ID"):
        return {"client_id": os.environ["UPWORK_OAUTH_CLIENT_ID"],
                "client_secret": os.environ.get("UPWORK_OAUTH_CLIENT_SECRET")}
    db.q(CLIENT_DDL)
    row = db.one("SELECT client_json FROM oauth_clients WHERE provider=:p", p=PROVIDER)
    if row:
        return json.loads(row["client_json"])
    if not meta.get("registration_endpoint"):
        raise RuntimeError("Upwork OAuth client id not configured and dynamic registration is not offered")
    async with _client() as c:
        r = await c.post(meta["registration_endpoint"], json={
            "client_name": "GigBid", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"})
        r.raise_for_status()
        reg = r.json()
    db.q("INSERT INTO oauth_clients VALUES (:p, :j)", p=PROVIDER, j=json.dumps(reg))
    return reg


def redirect_uri() -> str:
    return os.environ.get("UPWORK_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/upwork/callback")


async def start(tenant_id: str) -> str:
    """Authorization URL for the user to visit (PKCE S256, single-use state)."""
    meta = await discover()
    client = await client_credentials(meta, redirect_uri())
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    db.q(STATE_DDL)
    db.q("INSERT INTO oauth_states VALUES (:s,:t,:v,:c)", s=state, t=tenant_id, v=verifier, c=db.now())
    params = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_uri(),
              "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "resource": meta["resource"]}
    if SCOPES:
        params["scope"] = SCOPES
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


async def callback(code: str, state: str) -> str:
    """Exchange the code; returns the tenant id the tokens were stored for."""
    db.q(STATE_DDL)
    row = db.one("SELECT tenant_id, verifier FROM oauth_states WHERE state=:s", s=state)
    if not row:
        raise PermissionError("unknown or already-used OAuth state")
    db.q("DELETE FROM oauth_states WHERE state=:s", s=state)            # single use (CSRF / replay)
    meta = await discover()
    client = await client_credentials(meta, redirect_uri())
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": client["client_id"], "code_verifier": row["verifier"], "resource": meta["resource"]}
    if client.get("client_secret"):
        data["client_secret"] = client["client_secret"]
    async with _client() as c:
        r = await c.post(meta["token_endpoint"], data=data)
        r.raise_for_status()
        tok = r.json()
    token_store.save_tokens(row["tenant_id"], PROVIDER, tok["access_token"], tok.get("refresh_token"),
                            int(tok.get("expires_in", 3600)))
    return row["tenant_id"]


async def refresh(tenant_id: str) -> str:
    rt = token_store.refresh_token(tenant_id, PROVIDER)
    if not rt:
        raise token_store.TokenMissing("upwork token expired; reconnect in Settings")
    meta = await discover()
    client = await client_credentials(meta, redirect_uri())
    data = {"grant_type": "refresh_token", "refresh_token": rt, "client_id": client["client_id"],
            "resource": meta["resource"]}
    if client.get("client_secret"):
        data["client_secret"] = client["client_secret"]
    async with _client() as c:
        r = await c.post(meta["token_endpoint"], data=data)
    if r.status_code != 200:
        token_store.delete_tokens(tenant_id, PROVIDER)
        raise token_store.TokenMissing("upwork refresh failed; reconnect in Settings")
    tok = r.json()
    token_store.save_tokens(tenant_id, PROVIDER, tok["access_token"], tok.get("refresh_token", rt),
                            int(tok.get("expires_in", 3600)))
    return tok["access_token"]
