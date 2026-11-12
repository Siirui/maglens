"""Anti-scraping guard for readers: sliding-window rate limits + an append-only access log.

Everything is in memory (limits) or in <data_dir>/access.jsonl (log); both are server-only. The
clock and the rules are plain attributes so tests can shrink them instead of sleeping.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

# Reading pace these numbers are built around (web/app.js): a reader turns a page every few
# seconds; desktop spreads show 2 pages and prefetch the next 2 spreads (4 images), phones show 1
# and prefetch 2; images are browser-cached for a day, so only new pages cost a request, while the
# page data (translation JSON) is fetched again every time a page is shown. An issue is ~180 pages.
#
# Large images (w > 360) and page data, per (account, IP):
#   60 / minute  - skimming spreads at one turn every 2 s for a whole minute (2 new pages per turn),
#                  plus a resize that re-requests the visible pages at 2400. Steady reading is far
#                  below this; a script that pulls a page per second is not.
#   600 / hour   - reading a whole issue in an hour, even with zooming to 2400 or flipping back and
#                  forth, stays around 200-400. A scraper needs ~180 pages x 1 width per issue, so
#                  this caps it at about 3 issues' worth per hour per IP - and leaves a trail.
# Thumbnails (w <= 360), per (account, IP):
#   400 / 10 minutes - the desktop strip lazy-loads every page of an issue (~180) plus the shelf
#                  covers; even opening two issues and scrolling both strips end to end fits.
#                  Thumbnails are too small to be worth scraping, so one loose rule is enough.
# Large images per account, all IPs together:
#   3000 / hour  - the public trial account is shared, so the per-IP rules alone could be farmed
#                  from many IPs. 3000/h is ~10 people reading at full speed at the same time;
#                  more than that on one account means it is being farmed, not read.
MINUTE, HOUR = 60, 60 * 60
RULES: dict[str, list[tuple[int, float]]] = {  # bucket -> [(max hits, window seconds)]
    "img": [(60, MINUTE), (600, HOUR)],
    "thumb": [(400, 10 * MINUTE)],
    "data": [(60, MINUTE), (600, HOUR)],
    "img_account": [(3000, HOUR)],
}
PRUNE_EVERY = 60.0  # seconds between sweeps that drop keys with no hit inside their longest window
TOO_FAST = "看得太快了，休息一下再继续"

ACCESS_MAX_BYTES = 20 * 1024 * 1024  # rotate access.jsonl -> access.jsonl.1 past this size
SUMMARY_DEFAULT_HOURS, SUMMARY_MAX_HOURS, SUMMARY_RECENT_LIMITED = 24, 168, 20


class RateLimiter:
    """Thread-safe sliding windows keyed by (bucket, key). Only allowed hits are recorded, so a
    client that keeps hammering after a 429 is let in again as soon as its old hits age out."""

    def __init__(self, rules: dict[str, list[tuple[int, float]]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.rules = rules if rules is not None else {k: list(v) for k, v in RULES.items()}
        self.clock = clock
        self._hits: dict[tuple[str, tuple], deque[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = clock()

    def hit(self, checks: list[tuple[str, tuple]]) -> tuple[str, int] | None:
        """Count one request against every (bucket, key) in `checks`, all or nothing.
        Returns None when allowed, else (first bucket over its limit, Retry-After seconds)."""
        with self._lock:
            now = self.clock()
            if now - self._last_prune >= PRUNE_EVERY:
                self._prune(now)
            over, wait = None, 0.0
            for bucket, key in checks:
                q = self._hits.get((bucket, key))
                if not q:
                    continue
                for limit, window in self.rules[bucket]:
                    n = 0
                    for t in reversed(q):  # newest first; stop at the window edge
                        if now - t >= window:
                            break
                        n += 1
                    if n >= limit:
                        # wait until enough old hits leave the window to get back under the limit
                        oldest = q[len(q) - limit]  # the hit whose expiry brings n below limit
                        over = over or bucket
                        wait = max(wait, oldest + window - now)
            if over:
                return over, max(1, math.ceil(wait))
            for bucket, key in checks:
                q = self._hits.setdefault((bucket, key), deque())
                q.append(now)
                longest = max(w for _, w in self.rules[bucket])
                while q and now - q[0] >= longest:
                    q.popleft()
            return None

    def _prune(self, now: float):
        for k in [k for k, q in self._hits.items()
                  if not q or now - q[-1] >= max(w for _, w in self.rules[k[0]])]:
            del self._hits[k]
        self._last_prune = now

    def __len__(self):
        return len(self._hits)


class JsonlFile:
    """Append-only JSON-lines file shared by request threads (feedback.jsonl, access.jsonl)."""

    def __init__(self, path: Path, max_bytes: int | None = None):
        self.path, self.max_bytes = path, max_bytes
        self.lock = threading.Lock()

    @property
    def old(self) -> Path:
        return self.path.with_name(self.path.name + ".1")

    def append(self, entry: dict):
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.max_bytes is not None:
                try:
                    if self.path.stat().st_size > self.max_bytes:
                        self.path.replace(self.old)  # keep exactly one old file
                except FileNotFoundError:
                    pass
            with self.path.open("ab+") as f:
                if f.tell():  # a write cut short earlier must not swallow this entry too
                    f.seek(-1, 2)
                    if f.read(1) != b"\n":
                        line = "\n" + line
                f.write(line.encode("utf-8"))

    def entries(self, include_old: bool = False) -> list[dict]:
        """All readable entries, oldest first; truncated or hand-edited lines are skipped."""
        texts = []
        with self.lock:
            for p in ([self.old] if include_old else []) + [self.path]:
                try:
                    texts.append(p.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    pass
        out = []
        for text in texts:
            # split on \n only: splitlines() would also break on U+2028 / U+0085 inside a text,
            # which json.dumps(ensure_ascii=False) leaves unescaped
            for line in text.split("\n"):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    out.append(entry)
        return out


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(entries: list[dict], hours: int, now: datetime | None = None) -> dict:
    """Per-user totals over the last `hours` of access-log entries + the most recent 429s."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    users: dict[str, dict] = {}
    pages: dict[str, set] = {}
    ips: dict[str, set] = {}
    limited = []
    for e in entries:
        try:
            t = datetime.fromisoformat(e["time"])
            user, event = str(e["user"]), str(e["event"])
        except (KeyError, TypeError, ValueError):
            continue
        if t.tzinfo is None or t < cutoff:
            continue
        u = users.setdefault(user, {"views": 0, "distinct_pages": 0, "ips": 0, "last_seen": None,
                                    "limited": 0})
        ips.setdefault(user, set()).add(e.get("ip"))
        if u["last_seen"] is None or t > datetime.fromisoformat(u["last_seen"]):
            u["last_seen"] = e["time"]
        if event == "view":
            u["views"] += 1
            pages.setdefault(user, set()).add((e.get("mag_id"), e.get("page")))
        elif event.startswith("limited:"):
            u["limited"] += 1
            limited.append((t, e))
    for user, u in users.items():
        u["distinct_pages"], u["ips"] = len(pages.get(user, ())), len(ips[user])
    limited.sort(key=lambda x: x[0], reverse=True)
    return {"hours": hours, "users": users,
            "recent_limited": [e for _, e in limited[:SUMMARY_RECENT_LIMITED]]}
