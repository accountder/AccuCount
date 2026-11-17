"""极简认证：PBKDF2 存密码，HMAC 签名的无状态 token。

不引入 JWT 依赖，够用且不易出错。
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, Header, HTTPException

from .db import DATA_DIR

_SECRET_FILE = DATA_DIR / ".secret"


def _secret() -> bytes:
    env = os.environ.get("ACCUCOUNT_SECRET") or os.environ.get("CNERP_SECRET")
    if env:
        return env.encode()
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    s = secrets.token_bytes(32)
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_FILE.write_bytes(s)
    return s


SECRET = _secret()
TOKEN_TTL = 60 * 60 * 12


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return dk.hex(), salt


def verify_password(password: str, stored: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, stored)


def make_token(user_id: int, token_version: int = 0) -> str:
    payload = f"{user_id}:{int(time.time()) + TOKEN_TTL}:{int(token_version or 0)}"
    raw = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def parse_token(token: str) -> tuple[int, int]:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        raise HTTPException(401, "登录状态无效")
    expect = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expect, sig):
        raise HTTPException(401, "登录状态无效")
    try:
        payload = base64.urlsafe_b64decode(raw.encode()).decode()
        uid, exp, ver = payload.split(":", 2)
        if int(exp) < time.time():
            raise HTTPException(401, "登录已过期，请重新登录")
        return int(uid), int(ver)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "登录状态无效")


def current_user(authorization: str | None = Header(default=None)):
    """返回当前登录用户信息；未登录、停用或令牌失效时抛 401。"""
    from .db import SessionLocal
    from .models import User

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "请先登录")
    uid, ver = parse_token(authorization.split(" ", 1)[1].strip())
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        if not u or not u.enabled or int(u.token_version or 0) != ver:
            raise HTTPException(401, "账号不可用")
        return {
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "role": u.role,
            "token_version": int(u.token_version or 0),
        }
    finally:
        db.close()


def require_role(*roles):
    def _dep(user=Depends(current_user)):
        if roles and user["role"] not in roles:
            raise HTTPException(403, "当前账号无此权限")
        return user

    return _dep
