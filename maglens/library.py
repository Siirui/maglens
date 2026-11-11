"""Scan a directory for magazine PDFs."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import render
from .store import Store


def file_id(path: Path) -> str:
    """Stable id from size + head/tail bytes (fast even for 200MB PDFs, survives renames)."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    return h.hexdigest()[:12]


def guess_series(title: str) -> str:
    """'POPEYE 2026年1月号' -> 'popeye'. Glossaries are shared per series."""
    m = re.match(r"\s*([A-Za-z][A-Za-z&'. -]*?)(?:[\s_\-]*(?:\d|No|vol|Vol|第|$))", title)
    if m and m.group(1).strip():
        return m.group(1).strip().lower()
    return re.split(r"[\s_\-\d（(]", title.strip(), maxsplit=1)[0].lower() or title.lower()


def scan(directory: Path, store: Store) -> list[dict]:
    if directory.exists():
        for p in sorted(directory.rglob("*.pdf")):
            if p.name.startswith("."):
                continue
            try:
                mid = file_id(p)
                known = store.magazine(mid)
                if known and known["path"] == str(p):
                    continue
                n = render.page_count(p)
                w, h = render.page_size(p, 0)
                store.upsert_magazine({
                    "id": mid, "path": str(p), "title": p.stem, "series": guess_series(p.stem),
                    "pages": n, "aspect": round(w / h, 4), "mtime": p.stat().st_mtime,
                })
            except Exception as e:  # corrupt/encrypted PDFs shouldn't kill the scan
                print(f"[library] skip {p}: {e}")
    return store.magazines()
