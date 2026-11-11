"""Accounts for a small private deployment: scrypt password hashes + HMAC-signed session cookies.

Accounts live in <data_dir>/auth.json (never in config.toml, which is checked in):

    {"secret": "<random hex>", "users": [{"name": "siri", "hash": "scrypt$...", "role": "admin"}]}

No auth.json -> auth is off and every request is treated as the local admin (the old behaviour).
Manage accounts with `python -m maglens.users`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

ROLES = ("admin", "reader")
COOKIE = "maglens_session"
SESSION_DAYS = 30
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Auth:
    def __init__(self, path: Path):
        self.path = path
        self.secret = b""
        self.users: dict[str, dict] = {}
        self.reload()

    @property
    def enabled(self) -> bool:
        return bool(self.users)

    def reload(self):
        if not self.path.exists():
            self.secret, self.users = b"", {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.secret = bytes.fromhex(raw["secret"])
        self.users = {u["name"]: u for u in raw.get("users", [])}

    def save(self):
        if not self.secret:
            self.secret = secrets.token_bytes(32)
        data = {"secret": self.secret.hex(), "users": sorted(self.users.values(), key=lambda u: u["name"])}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    # accounts --------------------------------------------------------------
    def set_user(self, name: str, password: str, role: str):
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        self.users[name] = {"name": name, "hash": hash_password(password), "role": role}
        self.save()

    def remove_user(self, name: str) -> bool:
        if self.users.pop(name, None) is None:
            return False
        self.save()
        return True

    def check_login(self, name: str, password: str) -> dict | None:
        u = self.users.get(name)
        if u and verify_password(password, u["hash"]):
            return {"name": u["name"], "role": u["role"]}
        verify_password(password, hash_password("x"))  # same cost for unknown users
        return None

    # sessions --------------------------------------------------------------
    def make_token(self, user: dict, days: int = SESSION_DAYS) -> str:
        body = _b64(json.dumps({"u": user["name"], "exp": int(time.time()) + days * 86400}).encode())
        sig = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def read_token(self, token: str | None) -> dict | None:
        """Valid, unexpired token for a user that still exists -> {name, role} (role read fresh)."""
        if not token or "." not in token or not self.secret:
            return None
        body, sig = token.rsplit(".", 1)
        want = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        try:
            data = json.loads(_unb64(body))
        except ValueError:
            return None
        u = self.users.get(data.get("u"))
        if not u or data.get("exp", 0) < time.time():
            return None
        return {"name": u["name"], "role": u["role"]}
