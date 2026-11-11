"""Page extraction + translation pipeline.

main pass (image + text layer + glossary) -> coverage check against the PDF text layer
-> supplement pass for missed fragments, or a tiled audit pass for scanned pages.
"""
from __future__ import annotations

import base64
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts, render
from .config import Config

# $ per 1M tokens: (input, output). Cache reads ~0.1x input, writes ~1.25x input.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
}
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
                   "claude-fable-5-2", "claude-fable-5-1"}


class ExtractError(RuntimeError):
    pass


@dataclass
class Usage:
    cost: float = 0.0
    calls: int = 0
    details: list = field(default_factory=list)

    def add(self, msg):
        u = msg.usage
        pin, pout = PRICES.get(msg.model, PRICES.get("claude-opus-5-5"))
        inp = u.input_tokens or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        c = (inp * pin + cw * pin * 1.25 + cr * pin * 0.1 + (u.output_tokens or 0) * pout) / 1e6
        self.cost += c
        self.calls += 1
        self.details.append({"model": msg.model, "in": inp + cw + cr, "out": u.output_tokens,
                             "cost": round(c, 4)})


# ---------------------------------------------------------------------------
# text-layer coverage
_KEEP = re.compile(r"[\w぀-ヿ㐀-鿿ｦ-ﾟ]")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if _KEEP.match(ch)).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def text_layer_usable(spans: list[dict]) -> bool:
    """Reject empty or mojibake text layers (fonts without ToUnicode maps)."""
    text = "".join(s["text"] for s in spans)
    if len(norm(text)) < 8:
        return False
    good = sum(1 for ch in text if _KEEP.match(ch) or ch.isspace() or ch in "、。・「」（）!?！？ー〜…")
    pua = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF or ch == "�")
    return good / len(text) > 0.75 and pua / len(text) < 0.05


def coverage(spans: list[dict], blocks: list[dict]) -> tuple[float, list[dict]]:
    """Fraction of text-layer characters whose bigrams appear in extracted `ja` text."""
    got = _bigrams(norm("".join(b.get("ja", "") for b in blocks)))
    total = covered = 0
    missing = []
    for s in spans:
        n = norm(s["text"])
        if len(n) < 2 or n.isdigit():  # page numbers & stray glyphs
            continue
        bg = _bigrams(n)
        ratio = len(bg & got) / len(bg)
        total += len(n)
        if ratio >= 0.7:
            covered += len(n)
        else:
            missing.append(s)
    return (covered / total if total else 1.0), missing


# ---------------------------------------------------------------------------
_YEN = re.compile(r"([0-9][0-9,]*)\s*日元")


def add_cny(text: str, rate: float) -> str:
    def rep(m):
        rest = m.string[m.end():]
        if rest.startswith(("（约", "(约", "，约")):
            return m.group(0)
        cny = f"约{round(int(m.group(1).replace(',', '')) * rate):,}元人民币"
        if rest.startswith(("）", ")")):            # （950日元） -> （950日元，约45元人民币）
            return f"{m.group(0)}，{cny}"
        tax = re.match(r"[（(]((?:不)?含税)[）)]", rest)
        if tax:                                      # 950日元（含税） -> 950日元（含税，约45元人民币）
            return f"{m.group(0)}（{tax.group(1)}，{cny}）"
        return f"{m.group(0)}（{cny}）"
    out = _YEN.sub(rep, text)
    return re.sub(r"（((?:不)?含税)，(约[\d,]+元人民币)）[（(](?:不)?含税[）)]", r"（\1，\2）", out)


def _clamp_bbox(b) -> list[float]:
    try:
        x0, y0, x1, y1 = (float(v) for v in list(b)[:4])
    except Exception:
        return [0, 0, 1, 1]
    if max(x0, y0, x1, y1) > 1.5:  # model answered in percent
        x0, y0, x1, y1 = (v / 100 for v in (x0, y0, x1, y1))
    x0, x1 = sorted((min(max(x0, 0), 1), min(max(x1, 0), 1)))
    y0, y1 = sorted((min(max(y0, 0), 1), min(max(y1, 0), 1)))
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def _img(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(png).decode()}}


# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        import anthropic
        self.client = anthropic.Anthropic(max_retries=4)

    def _call(self, content: list, schema: dict, usage: Usage, max_tokens: int = 48000) -> dict:
        cfg = self.cfg
        kw = dict(
            model=cfg.model, max_tokens=max_tokens,
            system=[{"type": "text", "text": prompts.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if cfg.fallbacks and cfg.model in FALLBACK_MODELS:
            stream = self.client.beta.messages.stream(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        else:
            stream = self.client.messages.stream(**kw)
        with stream as s:
            msg = s.get_final_message()
        usage.add(msg)
        if msg.stop_reason == "refusal":
            raise ExtractError("模型拒绝处理此页（refusal）")
        if msg.stop_reason == "max_tokens":
            raise ExtractError("输出超长被截断（max_tokens），可改用框选分区识别")
        texts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        if not texts:
            raise ExtractError("模型没有返回文本")
        try:
            return json.loads(texts[-1])
        except json.JSONDecodeError as e:
            raise ExtractError(f"JSON 解析失败: {e}") from e

    # -- full page ---------------------------------------------------------
    def process_page(self, mag: dict, index: int, glossary: list[dict],
                     prev_summary: str | None) -> tuple[dict, Usage]:
        cfg, path = self.cfg, Path(mag["path"])
        usage = Usage()
        png = render.render_png(path, index, cfg.render_long_edge)
        spans = render.text_spans(path, index)
        usable = text_layer_usable(spans)
        layer = "\n".join(s["text"] for s in spans) if usable else None

        content = [_img(png), {"type": "text", "text": prompts.main_user_text(
            text_layer=layer, glossary=glossary, prev_summary=prev_summary,
            title=mag["title"], page_no=index + 1, total=mag["pages"])}]
        data = self._call(content, prompts.PAGE_SCHEMA, usage)
        blocks = data.get("blocks", [])
        terms = list(data.get("new_terms", []))
        passes = ["main"]

        cov = None
        if usable:
            cov, missing = coverage(spans, blocks)
            if cov < cfg.coverage_threshold and missing:
                extra = self._call([_img(png), {"type": "text", "text": prompts.supplement_text(
                    missing=[m["text"] for m in missing[:150]],
                    existing=[b["ja"][:60] for b in blocks], glossary=glossary)}],
                    prompts.SUPPLEMENT_SCHEMA, usage)
                for b in extra.get("blocks", []):
                    b["supplement"] = True
                blocks += extra.get("blocks", [])
                terms += extra.get("new_terms", [])
                passes.append("supplement")
                cov, _ = coverage(spans, blocks)
        elif data.get("page_kind") not in ("photo",):
            extra = self._audit(path, index, blocks, glossary, usage)
            blocks += extra.get("blocks", [])
            terms += extra.get("new_terms", [])
            passes.append("audit")

        return self._finish(data, blocks, terms, passes, cov, usable), usage

    def _audit(self, path: Path, index: int, blocks, glossary, usage) -> dict:
        """Scanned page: re-check with 2x2 overlapping zoomed tiles for small text."""
        tiles = [(0, 0, 0.55, 0.55), (0.45, 0, 1, 0.55), (0, 0.45, 0.55, 1), (0.45, 0.45, 1, 1)]
        content = [_img(render.render_png(path, index, 1400))]
        content += [_img(render.render_png(path, index, 1600, clip=t)) for t in tiles]
        content.append({"type": "text", "text": prompts.audit_text(
            existing=[b["ja"][:60] for b in blocks], glossary=glossary, tiles=tiles)})
        extra = self._call(content, prompts.SUPPLEMENT_SCHEMA, usage)
        for b in extra.get("blocks", []):
            b["supplement"] = True
        return extra

    def _finish(self, data, blocks, terms, passes, cov, usable) -> dict:
        out = []
        for i, b in enumerate(blocks):
            zh = b.get("zh", "")
            if self.cfg.show_cny:
                zh = add_cny(zh, self.cfg.cny_rate)
            out.append({"id": f"b{i}", "type": b.get("type", "other"),
                        "bbox": _clamp_bbox(b.get("bbox", [0, 0, 1, 1])),
                        "ja": b.get("ja", ""), "zh": zh, "note": b.get("note", ""),
                        "supplement": bool(b.get("supplement"))})
        return {"page_kind": data.get("page_kind", "other"),
                "page_summary": data.get("page_summary", ""),
                "blocks": out, "new_terms": terms,
                "meta": {"passes": passes, "coverage": None if cov is None else round(cov, 3),
                         "text_layer": usable}}

    # -- user-selected region ----------------------------------------------
    def process_region(self, mag: dict, index: int, bbox: list[float], glossary,
                       context: str | None) -> tuple[list[dict], list[dict], Usage]:
        usage = Usage()
        x0, y0, x1, y1 = _clamp_bbox(bbox)
        png = render.render_png(Path(mag["path"]), index, 1800, clip=(x0, y0, x1, y1))
        data = self._call([_img(png), {"type": "text", "text": prompts.region_text(
            glossary=glossary, context=context)}], prompts.SUPPLEMENT_SCHEMA, usage)
        blocks = []
        for b in data.get("blocks", []):
            bx0, by0, bx1, by1 = _clamp_bbox(b.get("bbox", [0, 0, 1, 1]))
            w, h = x1 - x0, y1 - y0
            b["bbox"] = [x0 + bx0 * w, y0 + by0 * h, x0 + bx1 * w, y0 + by1 * h]
            b["supplement"] = True
            blocks.append(b)
        return blocks, data.get("new_terms", []), usage


class MockEngine(Engine):
    """No API key: builds blocks straight from the PDF text layer so the UI can be tested."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def process_page(self, mag, index, glossary, prev_summary):
        spans = render.text_spans(Path(mag["path"]), index)
        blocks = [{"type": "body", "bbox": s["bbox"], "ja": s["text"],
                   "zh": "【模拟模式，未调用模型】" + s["text"], "note": ""} for s in spans]
        if not blocks:
            blocks = [{"type": "other", "bbox": [0, 0, 1, 1], "ja": "",
                       "zh": "【模拟模式】这一页没有文字层；配置 ANTHROPIC_API_KEY 后才能识别图片中的文字。",
                       "note": ""}]
        data = {"page_kind": "other", "page_summary": ""}
        return self._finish(data, blocks, [], ["mock"], None, bool(spans)), Usage()

    def process_region(self, mag, index, bbox, glossary, context):
        return [], [], Usage()


def make_engine(cfg: Config) -> Engine:
    import os
    if cfg.mock or not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                        or Path("~/.config/anthropic").expanduser().exists()):
        print("[maglens] 未检测到 Anthropic 凭证，使用模拟模式（只显示 PDF 文字层）")
        return MockEngine(cfg)
    return Engine(cfg)
