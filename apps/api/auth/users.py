"""Users and sessions: scrypt password hashes, HMAC-signed session tokens (cookie or Bearer)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from fastapi import Depends, HTTPException, Request

from apps.api import db

SESSION_TTL_S = 7 * 24 * 3600
COOKIE = "gigbid_session"


def _secret() -> bytes:
    s = os.environ.get("SESSION_SECRET")
    if not s:
        s = os.environ.setdefault("SESSION_SECRET", secrets.token_hex(32))  # dev only: sessions reset on restart
    return s.encode()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except ValueError:
        return False


def make_token(user_id: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "exp": int(time.time()) + SESSION_TTL_S}).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(token: str) -> str | None:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    data = json.loads(base64.urlsafe_b64decode(body.encode()))
    return data["u"] if data["exp"] > time.time() else None


def create_user(email: str, password: str, display_name: str | None = None, persona: str | None = None) -> str:
    uid = f"u_{uuid.uuid4().hex[:10]}"
    db.q("INSERT INTO users (id, email, password_hash, display_name, persona, settings_json, onboarded, created_at) "
         "VALUES (:id,:e,:p,:d,:per,:s,0,:c)", id=uid, e=email.lower().strip(), p=hash_password(password),
         d=display_name, per=persona, s=json.dumps(db.DEFAULT_SETTINGS), c=db.now())
    return uid


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    uid = read_token(token) if token else None
    user = db.one("SELECT id, email, display_name, persona, settings_json, onboarded FROM users WHERE id=:u", u=uid) if uid else None
    if not user:
        raise HTTPException(401, "not signed in")
    user["settings"] = json.loads(user.pop("settings_json"))
    return user


CurrentUser = Depends(current_user)
