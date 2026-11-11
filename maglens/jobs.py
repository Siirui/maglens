"""Background page processing with a small thread pool + prefetch."""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from .config import Config
from .extract import Engine
from .prompts import PROMPT_VERSION
from .store import Store


class Jobs:
    def __init__(self, cfg: Config, store: Store, engine: Engine):
        self.cfg, self.store, self.engine = cfg, store, engine
        self.pool = ThreadPoolExecutor(max_workers=cfg.concurrency)
        self._inflight: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    def ensure(self, mag_id: str, page: int, force: bool = False) -> str:
        """Queue a page if it has no result yet. Returns the page status."""
        mag = self.store.magazine(mag_id)
        if not mag or not (0 <= page < mag["pages"]):
            return "invalid"
        key = (mag_id, page)
        with self._lock:
            if key in self._inflight:
                return "running"
            row = self.store.page(mag_id, page)
            if row and row["status"] == "done" and not force:
                return "done"
            if row and row["status"] == "error" and not force:
                return "error"
            self._inflight.add(key)
        self.store.set_status(mag_id, page, "queued")
        self.pool.submit(self._run, mag, page)
        return "queued"

    def prefetch(self, mag_id: str, page: int):
        for p in range(page + 1, page + 1 + self.cfg.prefetch):
            self.ensure(mag_id, p)

    def _run(self, mag: dict, page: int):
        key = (mag["id"], page)
        try:
            self.store.set_status(mag["id"], page, "running")
            glossary = self.store.glossary(mag["series"])
            prev = self.store.page(mag["id"], page - 1) if page > 0 else None
            prev_summary = (prev or {}).get("data", {}) and prev["data"].get("page_summary")
            data, usage = self.engine.process_page(mag, page, glossary, prev_summary)
            data["meta"]["usage"] = usage.details
            self.store.add_terms(mag["series"], data.get("new_terms", []))
            self.store.save_page(mag["id"], page, data, usage.cost, self.cfg.model, PROMPT_VERSION)
        except Exception as e:
            traceback.print_exc()
            self.store.set_status(mag["id"], page, "error", f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._inflight.discard(key)
