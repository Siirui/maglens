"""Scan a directory for magazine PDFs."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

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
    """'POPEYE 2026年1月号' -> 'popeye', '2nd 2026.11' -> '2nd'. Glossaries are shared per series."""
    m = re.match(r"\s*(\d+(?:st|nd|rd|th))(?![A-Za-z])", title, re.I)  # names that start with a number
    if m:
        return m.group(1).lower()
    m = re.match(r"\s*([A-Za-z][A-Za-z&'. -]*?)(?:[\s_\-]*(?:\d|No|vol|Vol|第|$))", title)
    if m and m.group(1).strip():
        return m.group(1).strip().lower()
    return re.split(r"[\s_\-\d（(]", title.strip(), maxsplit=1)[0].lower() or title.lower()


IMAGE_EXT = {".jpg", ".jpeg", ".png"}


def convert_image_folders(directory: Path, out_dir: Path) -> list[Path]:
    """Turn folders of page images (e.g. a scanned issue as IMG_0001.JPG...) into PDFs in out_dir.

    The source folder is left untouched; images are embedded without re-encoding.
    """
    made = []
    if not directory.exists():
        return made
    for d in sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".")):
        imgs = sorted(f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXT and not f.name.startswith("."))
        if len(imgs) < 4:
            continue
        out = out_dir / f"{d.name}.pdf"
        if out.exists() and out.stat().st_mtime >= max(f.stat().st_mtime for f in imgs):
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        for f in imgs:
            with pymupdf.open(f) as im:
                rect = im[0].rect
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, filename=str(f))
        tmp = out.with_suffix(".tmp.pdf")
        doc.save(tmp, garbage=3, deflate=True)
        tmp.replace(out)
        made.append(out)
    return made


def scan_all(cfg, store: Store) -> list[dict]:
    """Scan the library dir plus PDFs converted from image folders."""
    converted = cfg.data_dir / "converted"
    try:
        convert_image_folders(cfg.library_dir, converted)
    except Exception as e:
        print(f"[library] image folder conversion failed: {e}")
    scan(converted, store)
    return scan(cfg.library_dir, store)


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
