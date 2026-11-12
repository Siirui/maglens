"""Accounts for a small private deployment: scrypt password hashes + HMAC-signed session cookies.

Accounts live in <data_dir>/auth.json (never in config.toml, which is checked in):

    {"secret": "<random hex>", "users": [{"name": "siri", "hash": "scrypt$...", "role": "admin"},
                                         {"name": "trial", "hash": "...", "role": "reader", "mags": ["<mag id>"]}]}

`mags` (optional) limits a reader to those issues; missing means every issue. Admins ignore it.

No auth.json -> auth is off and every request is treated as the local admin (the old behaviour).
Manage accounts with `python -m maglens.users`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
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
        self._mtime: tuple | None = None
        self.ok = True  # False while auth.json is missing/unreadable but accounts are still loaded
        self._lock = threading.RLock()
        self.reload()

    @property
    def enabled(self) -> bool:
        return bool(self.users)

    def _stat(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError as e:
            return ("error", str(e))  # differs from any real stat, so a fixed file is picked up
        return st.st_ino, st.st_mtime_ns, st.st_size  # save() replaces the file, so the inode changes too

    def refresh(self) -> bool:
        """Pick up edits made by another process (e.g. `maglens.users` on the server): one stat() call.

        Fails closed: if auth.json is missing (while accounts are loaded), unreadable or malformed, the
        accounts already in memory stay in force instead of turning login off or taking the site down.
        Returns False in that state, so account edits can refuse to overwrite a file they can't read.
        To revoke every account, use `maglens.users remove` or restart the service."""
        with self._lock:
            st = self._stat()
            if st == self._mtime:
                return self.ok
            try:
                if st is None and self.users:
                    raise FileNotFoundError(self.path)
                self._load()
            except Exception:  # noqa: BLE001 — any bad file keeps the loaded accounts
                self.ok = False
            self._mtime = st  # also on failure: re-read only once the file changes again
            return self.ok

    def reload(self):
        """Read auth.json now; raises ValueError with a readable message if it is broken."""
        with self._lock:
            try:
                self._load()
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"{self.path} 格式有误，请修好或从备份恢复：{e}") from e
            self._mtime = self._stat()

    def _load(self):
        """Parse and validate first, then swap everything in at once."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if self.users:  # never let a vanished file switch login off
                raise
            text = None
        if text is None:
            secret, users = b"", {}
        else:
            raw = json.loads(text)
            secret = bytes.fromhex(raw["secret"])
            users = {u["name"]: u for u in raw.get("users", [])}
            for u in users.values():
                mags = u.get("mags")
                if not (isinstance(u["name"], str) and isinstance(u.get("hash"), str) and u.get("role") in ROLES
                        and (mags is None or (isinstance(mags, list) and all(isinstance(x, str) for x in mags)))):
                    raise ValueError(f"账号 {u.get('name')!r} 的字段不对")
        self.secret, self.users, self.ok = secret, users, True

    def save(self):
        if not self.secret:
            self.secret = secrets.token_bytes(32)
        data = {"secret": self.secret.hex(), "users": sorted(self.users.values(), key=lambda u: u["name"])}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
        self._mtime = self._stat()

    # accounts --------------------------------------------------------------
    def set_user(self, name: str, password: str, role: str, mags: list[str] | None = None):
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        self.users[name] = {"name": name, "hash": hash_password(password), "role": role}
        if mags is not None:
            self.users[name]["mags"] = list(mags)
        self.save()

    def set_mags(self, name: str, mags: list[str] | None):
        """None = every issue."""
        u = self.users[name]
        if mags is None:
            u.pop("mags", None)
        else:
            u["mags"] = list(mags)
        self.save()

    @staticmethod
    def public(u: dict) -> dict:
        out = {"name": u["name"], "role": u["role"]}
        if u.get("mags") is not None:
            out["mags"] = list(u["mags"])
        return out

    def remove_user(self, name: str) -> bool:
        if self.users.pop(name, None) is None:
            return False
        self.save()
        return True

    def check_login(self, name: str, password: str) -> dict | None:
        u = self.users.get(name)
        if u and verify_password(password, u["hash"]):
            return self.public(u)
        verify_password(password, hash_password("x"))  # same cost for unknown users
        return None

    # sessions --------------------------------------------------------------
    @staticmethod
    def _ver(u: dict) -> str:
        """Changes whenever the password changes, so resetting a password logs out old sessions."""
        return hashlib.sha256(u["hash"].encode()).hexdigest()[:12]

    def make_token(self, user: dict, days: int = SESSION_DAYS) -> str:
        u = self.users[user["name"]]
        body = _b64(json.dumps({"u": u["name"], "v": self._ver(u), "exp": int(time.time()) + days * 86400}).encode())
        sig = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def read_token(self, token: str | None) -> dict | None:
        """Valid, unexpired token for a user that still exists -> {name, role[, mags]} (read fresh)."""
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
        if not u or data.get("exp", 0) < time.time() or data.get("v") != self._ver(u):
            return None
        return self.public(u)
