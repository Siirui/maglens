"""SQLite persistence: magazines, per-page results, glossary, batches."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS magazines (
  id TEXT PRIMARY KEY, path TEXT, title TEXT, series TEXT,
  pages INTEGER, aspect REAL, mtime REAL
);
CREATE TABLE IF NOT EXISTS pages (
  mag_id TEXT, page INTEGER, status TEXT, data TEXT, error TEXT,
  cost REAL DEFAULT 0, model TEXT, prompt_ver TEXT, updated REAL,
  PRIMARY KEY (mag_id, page)
);
CREATE TABLE IF NOT EXISTS glossary (
  series TEXT, ja TEXT, zh TEXT, kind TEXT, locked INTEGER DEFAULT 0, updated REAL,
  PRIMARY KEY (series, ja)
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, mag_id TEXT, status TEXT, created REAL, pages TEXT
);
"""


class Store:
    def __init__(self, path: Path, pages_dir: Path | None = None):
        self.pages_dir = pages_dir  # pre-rendered page images (published deployments have no PDFs)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        # A crashed run may leave pages stuck in "running".
        self._db.execute("UPDATE pages SET status='error', error='interrupted' "
                         "WHERE status IN ('running','queued')")
        self._db.commit()

    def _q(self, sql: str, args=(), one=False, commit=False):
        with self._lock:
            cur = self._db.execute(sql, args)
            if commit:
                self._db.commit()
            rows = cur.fetchall()
        rows = [dict(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    # magazines -----------------------------------------------------------
    def upsert_magazine(self, m: dict):
        self._q("INSERT INTO magazines (id,path,title,series,pages,aspect,mtime) VALUES "
                "(:id,:path,:title,:series,:pages,:aspect,:mtime) ON CONFLICT(id) DO UPDATE SET "
                "path=excluded.path, title=excluded.title, mtime=excluded.mtime", m, commit=True)

    def set_series(self, mag_id: str, series: str):
        self._q("UPDATE magazines SET series=? WHERE id=?", (series, mag_id), commit=True)

    def magazines(self) -> list[dict]:
        rows = self._q("SELECT m.*, (SELECT COUNT(*) FROM pages p WHERE p.mag_id=m.id AND "
                       "p.status='done') AS done, (SELECT COUNT(*) FROM pages p WHERE p.mag_id=m.id "
                       "AND p.status='error') AS errors, (SELECT COALESCE(SUM(cost),0) FROM pages p "
                       "WHERE p.mag_id=m.id) AS cost FROM magazines m ORDER BY title")
        return [r for r in rows if (r["path"] and Path(r["path"]).exists())
                or (self.pages_dir and (self.pages_dir / r["id"]).is_dir())]

    def magazine(self, mag_id: str) -> dict | None:
        return self._q("SELECT * FROM magazines WHERE id=?", (mag_id,), one=True)

    # pages ---------------------------------------------------------------
    def page(self, mag_id: str, page: int) -> dict | None:
        r = self._q("SELECT * FROM pages WHERE mag_id=? AND page=?", (mag_id, page), one=True)
        if r and r["data"]:
            r["data"] = json.loads(r["data"])
        return r

    def page_statuses(self, mag_id: str) -> dict[int, str]:
        rows = self._q("SELECT page, status FROM pages WHERE mag_id=?", (mag_id,))
        return {r["page"]: r["status"] for r in rows}

    def set_status(self, mag_id: str, page: int, status: str, error: str | None = None):
        self._q("INSERT INTO pages (mag_id,page,status,error,updated) VALUES (?,?,?,?,?) "
                "ON CONFLICT(mag_id,page) DO UPDATE SET status=excluded.status, "
                "error=excluded.error, updated=excluded.updated",
                (mag_id, page, status, error, time.time()), commit=True)

    def save_page(self, mag_id: str, page: int, data: dict, cost: float, model: str,
                  prompt_ver: str, add_cost: bool = False):
        prev = self._q("SELECT cost FROM pages WHERE mag_id=? AND page=?", (mag_id, page), one=True)
        if add_cost and prev:
            cost += prev["cost"] or 0
        self._q("INSERT INTO pages (mag_id,page,status,data,error,cost,model,prompt_ver,updated) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(mag_id,page) DO UPDATE SET "
                "status=excluded.status, data=excluded.data, error=NULL, cost=excluded.cost, "
                "model=excluded.model, prompt_ver=excluded.prompt_ver, updated=excluded.updated",
                (mag_id, page, "done", json.dumps(data, ensure_ascii=False), None, cost, model,
                 prompt_ver, time.time()), commit=True)

    def all_pages(self, mag_id: str) -> list[dict]:
        rows = self._q("SELECT * FROM pages WHERE mag_id=? AND status='done' ORDER BY page",
                       (mag_id,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    def total_cost(self) -> float:
        r = self._q("SELECT COALESCE(SUM(cost),0) AS c FROM pages", one=True)
        return r["c"]

    # glossary ------------------------------------------------------------
    def glossary(self, series: str) -> list[dict]:
        return self._q("SELECT ja, zh, kind, locked FROM glossary WHERE series=? ORDER BY kind, ja",
                       (series,))

    def add_terms(self, series: str, terms: list[dict]):
        """Add auto-discovered terms; never overwrite an existing (esp. user-edited) entry."""
        now = time.time()
        with self._lock:
            for t in terms:
                ja, zh = (t.get("ja") or "").strip(), (t.get("zh") or "").strip()
                if not ja or not zh:
                    continue
                self._db.execute("INSERT OR IGNORE INTO glossary (series,ja,zh,kind,locked,updated)"
                                 " VALUES (?,?,?,?,0,?)", (series, ja, zh, t.get("kind", ""), now))
            self._db.commit()

    def replace_glossary(self, series: str, terms: list[dict]):
        now = time.time()
        with self._lock:
            self._db.execute("DELETE FROM glossary WHERE series=?", (series,))
            for t in terms:
                ja, zh = (t.get("ja") or "").strip(), (t.get("zh") or "").strip()
                if ja and zh:
                    self._db.execute("INSERT OR REPLACE INTO glossary VALUES (?,?,?,?,?,?)",
                                     (series, ja, zh, t.get("kind", ""), int(t.get("locked", 1)), now))
            self._db.commit()

    # batches -------------------------------------------------------------
    def add_batch(self, batch_id: str, mag_id: str, pages: list[int]):
        self._q("INSERT INTO batches VALUES (?,?,?,?,?)",
                (batch_id, mag_id, "in_progress", time.time(), json.dumps(pages)), commit=True)

    def open_batches(self) -> list[dict]:
        return self._q("SELECT * FROM batches WHERE status='in_progress'")

    def close_batch(self, batch_id: str, status: str = "ended"):
        self._q("UPDATE batches SET status=? WHERE id=?", (status, batch_id), commit=True)
