"""Per-account reading position ("继续阅读"), so it follows the account across devices.

Kept in <data_dir>/reading.json, which only exists on the server (sync.sh never uploads or
overwrites it, backup.sh copies it):

    {"<account name>": {"last": {"mag_id": "...", "page": 12, "at": "2026-11-11T08:00:00+00:00"},
                        "pages": {"<mag id>": {"page": 12, "at": "..."}, ...}}, ...}

`page` is 0-based like everywhere else in the API; `at` is server time (UTC). "pages" is in write
order (oldest first) and each account keeps its MAX_MAGS most recently written issues. The file is
re-read on every call (it is small) and replaced atomically while holding a thread lock plus an
flock on reading.json.lock, so request threads and the `maglens.users` CLI (removing an account)
neither leave it half-written nor lose each other's updates.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

MAX_MAGS = 50


class ReadingState:
    def __init__(self, path: Path, max_mags: int = MAX_MAGS):
        self.path, self.max_mags = path, max_mags
        self.lock = threading.Lock()

    @contextlib.contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, open(self.path.with_name(self.path.name + ".lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # other processes too; released when the file is closed
            yield

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:
            # Hand-edited or damaged: keep a copy for inspection and start over rather than fail every
            # request. Reading positions are a convenience, not something worth blocking readers for.
            bad = self.path.with_name(self.path.name + ".bad")
            print(f"{self.path} is not valid JSON, moved to {bad}", file=sys.stderr)
            self.path.replace(bad)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict):
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _pages(entry) -> dict[str, dict]:
        """An account's {mag_id: {page, at}}, keeping only well-formed entries (the file may be hand-edited)."""
        pages = entry.get("pages") if isinstance(entry, dict) else None
        if not isinstance(pages, dict):
            return {}
        return {k: {"page": v["page"], "at": v["at"]} for k, v in pages.items()
                if isinstance(v, dict) and isinstance(v.get("page"), int) and not isinstance(v.get("page"), bool)
                and v["page"] >= 0 and isinstance(v.get("at"), str)}

    def get(self, user: str) -> dict[str, dict]:
        """{mag_id: {"page", "at"}} for one account, newest first."""
        with self._locked():
            pages = self._pages(self._load().get(user))
        return dict(reversed(pages.items()))

    def put(self, user: str, mag_id: str, page: int, at: str):
        with self._locked():
            data = self._load()
            pages = self._pages(data.get(user))
            pages.pop(mag_id, None)
            pages[mag_id] = {"page": page, "at": at}  # kept oldest -> newest write, so the cap keeps the tail
            data[user] = {"last": {"mag_id": mag_id, "page": page, "at": at},
                          "pages": dict(list(pages.items())[-self.max_mags:])}
            self._save(data)

    def forget(self, user: str):
        """Drop a removed account's positions, so a new account with the same name starts fresh."""
        with self._locked():
            data = self._load()
            if data.pop(user, None) is not None:
                self._save(data)
