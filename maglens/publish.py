"""Build a deployable data directory: pre-rendered WebP pages + a slim DB, no PDFs.

    python -m maglens.publish --out dist            # every magazine whose translation is finished
    python -m maglens.publish --out dist --only 2026.11

Layout of the output (point the server's --data-dir at it):

    dist/maglens.sqlite3                 magazines (path=""), finished pages, glossary — no costs
    dist/pages/<mag_id>/<page>_<w>.webp  w = 360 / 1600 / 2400
    dist/auth.json                       accounts (kept across publishes; manage with maglens.users)

Re-running is incremental: images that already exist are skipped and the DB is rebuilt.
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import library, render
from .config import load_config
from .server import WIDTHS, available
from .store import SCHEMA, Store

WEBP_QUALITY = {360: 70, 1600: 80, 2400: 82}


def render_webp(pdf: Path, n: int, w: int, out: Path):
    if out.exists():
        return False
    png = render.render_png(pdf, n, w)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    tmp = out.with_suffix(".tmp")
    im.save(tmp, "WEBP", quality=WEBP_QUALITY[w], method=4)
    tmp.replace(out)
    return True


def build_db(src: Store, mags: list[dict], dest: Path):
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    for m in mags:
        db.execute("INSERT INTO magazines (id,path,title,series,pages,aspect,mtime) VALUES (?,?,?,?,?,?,?)",
                   (m["id"], "", m["title"].strip(), m["series"], m["pages"], m["aspect"], m["mtime"]))
        for row in src.all_pages(m["id"]):
            data = row["data"] or {}
            meta = data.get("meta") or {}
            meta.pop("usage", None)
            data["meta"] = meta
            data.pop("new_terms", None)
            db.execute("INSERT INTO pages (mag_id,page,status,data,error,cost,model,prompt_ver,updated) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (m["id"], row["page"], "done", json.dumps(data, ensure_ascii=False), None, 0, "",
                        row["prompt_ver"], row["updated"]))
        for p, s in src.page_statuses(m["id"]).items():
            if s == "error":
                db.execute("INSERT INTO pages (mag_id,page,status,error,cost,updated) VALUES (?,?,?,?,0,?)",
                           (m["id"], p, "error", "no translation", time.time()))
    for series in {m["series"] for m in mags}:
        for t in src.glossary(series):
            db.execute("INSERT OR REPLACE INTO glossary VALUES (?,?,?,?,?,?)",
                       (series, t["ja"], t["zh"], t["kind"], t["locked"], time.time()))
    db.commit()
    db.close()
    tmp.replace(dest)


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出可部署的数据目录（预渲染页面 + 精简数据库，不含 PDF）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dir", help="杂志目录（默认读 config.toml）")
    ap.add_argument("--data-dir", help="源数据目录（默认读 config.toml）")
    ap.add_argument("--only", nargs="*", help="只导出标题包含这些字样的杂志")
    ap.add_argument("--include-unfinished", action="store_true", help="没翻完的杂志也导出")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)

    cfg = load_config(library_dir=a.dir, data_dir=a.data_dir)
    src = Store(cfg.db_path)
    mags = library.scan_all(cfg, src)
    if a.only:
        mags = [m for m in mags if any(s.lower() in m["title"].lower() for s in a.only)]
    if not a.include_unfinished:
        skipped = [m["title"].strip() for m in mags if not available(m)]
        mags = [m for m in mags if available(m)]
        if skipped:
            print("跳过未完成：" + "、".join(skipped))

    out = Path(a.out).expanduser()
    (out / "pages").mkdir(parents=True, exist_ok=True)
    for m in mags:
        pdf, d = Path(m["path"]), out / "pages" / m["id"]
        d.mkdir(exist_ok=True)
        t0 = time.time()
        jobs = [(n, w) for n in range(m["pages"]) for w in WIDTHS]
        with ThreadPoolExecutor(a.workers) as pool:  # render.render_png serialises MuPDF itself
            made = sum(pool.map(lambda j: render_webp(pdf, j[0], j[1], d / f"{j[0]:04d}_{j[1]}.webp"), jobs))
        size = sum(f.stat().st_size for f in d.iterdir()) / 1e6
        print(f"{m['title'].strip()}: {m['pages']} 页，新渲染 {made} 张，{size:.0f} MB，{time.time() - t0:.0f}s")
    build_db(src, mags, out / "maglens.sqlite3")
    total = sum(f.stat().st_size for f in (out / "pages").rglob("*.webp")) / 1e9
    print(f"完成：{len(mags)} 本 → {out}（页面图共 {total:.2f} GB）")


if __name__ == "__main__":
    main()
