"""PDF rendering and text-layer extraction (PyMuPDF)."""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import pymupdf

_lock = threading.Lock()  # MuPDF documents are not thread-safe


@lru_cache(maxsize=16)
def _open(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


def page_count(path: Path) -> int:
    with _lock:
        return _open(str(path)).page_count


def page_size(path: Path, index: int) -> tuple[float, float]:
    with _lock:
        r = _open(str(path))[index].rect
        return r.width, r.height


def render_png(path: Path, index: int, long_edge: int,
               clip: tuple[float, float, float, float] | None = None) -> bytes:
    """Render page `index` (0-based) so its long edge is `long_edge` px.

    `clip` is a normalized (x0, y0, x1, y1) box in 0..1 page coordinates.
    """
    with _lock:
        page = _open(str(path))[index]
        rect = page.rect
        area = rect
        if clip:
            x0, y0, x1, y1 = clip
            area = pymupdf.Rect(rect.x0 + x0 * rect.width, rect.y0 + y0 * rect.height,
                                rect.x0 + x1 * rect.width, rect.y0 + y1 * rect.height)
        zoom = long_edge / max(area.width, area.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=area, alpha=False)
        return pix.tobytes("png")


def cached_png(cache_dir: Path, mag_id: str, path: Path, index: int, long_edge: int) -> Path:
    out = cache_dir / mag_id / f"p{index:04d}_{long_edge}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(render_png(path, index, long_edge))
        tmp.replace(out)
    return out


def text_spans(path: Path, index: int) -> list[dict]:
    """Return text-layer lines as [{'text', 'bbox' (normalized), 'vertical'}]."""
    with _lock:
        page = _open(str(path))[index]
        W, H = page.rect.width, page.rect.height
        d = page.get_text("dict")
    out = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            dx, dy = line.get("dir", (1, 0))
            out.append({
                "text": text,
                "bbox": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)],
                "vertical": abs(dy) > abs(dx),
            })
    return merge_vertical(out)


def merge_vertical(spans: list[dict]) -> list[dict]:
    """Join 1–2 char lines stacked in a column (typical of vertical text in PDFs) into one span."""
    out: list[dict] = []
    for s in spans:
        prev = out[-1] if out else None
        if prev and len(s["text"]) <= 2 and (prev.get("_col") or len(prev["text"]) <= 2):
            px0, py0, px1, py1 = prev["bbox"]
            x0, y0, x1, y1 = s["bbox"]
            w = max(px1 - px0, x1 - x0, 1e-6)
            h = y1 - y0
            same_col = min(px1, x1) - max(px0, x0) > 0.5 * w
            adjacent = -0.5 * h <= y0 - py1 <= 1.2 * h
            if same_col and adjacent:
                prev["text"] += s["text"]
                prev["bbox"] = [min(px0, x0), py0, max(px1, x1), max(py1, y1)]
                prev["vertical"] = prev["_col"] = True
                continue
        out.append(dict(s))
    for s in out:
        s.pop("_col", None)
    return out
