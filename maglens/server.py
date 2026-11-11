"""FastAPI app: JSON API + static frontend."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import library, render
from .config import ROOT, Config
from .extract import make_engine
from .jobs import Jobs
from .prompts import PROMPT_VERSION
from .store import Store


def create_app(cfg: Config, engine=None) -> FastAPI:
    store = Store(cfg.db_path)
    engine = engine or make_engine(cfg)
    jobs = Jobs(cfg, store, engine)
    library.scan(cfg.library_dir, store)
    app = FastAPI(title="MagLens")
    app.state.store, app.state.jobs = store, jobs

    def mag_or_404(mag_id: str) -> dict:
        m = store.magazine(mag_id)
        if not m:
            raise HTTPException(404, "magazine not found")
        return m

    @app.get("/api/library")
    def get_library():
        return {"dir": str(cfg.library_dir), "magazines": store.magazines(),
                "mock": type(engine).__name__ == "MockEngine", "model": cfg.model,
                "total_cost": round(store.total_cost(), 4)}

    @app.post("/api/library/rescan")
    def rescan():
        return {"magazines": library.scan(cfg.library_dir, store)}

    @app.get("/api/mag/{mag_id}")
    def get_mag(mag_id: str):
        m = mag_or_404(mag_id)
        return {**m, "statuses": store.page_statuses(mag_id)}

    @app.get("/api/mag/{mag_id}/page/{n}.png")
    def page_png(mag_id: str, n: int, w: int = 1600):
        m = mag_or_404(mag_id)
        w = 360 if w <= 360 else 1600 if w <= 1600 else 2400
        p = render.cached_png(cfg.cache_dir, mag_id, Path(m["path"]), n, w)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/mag/{mag_id}/page/{n}")
    def page_data(mag_id: str, n: int, ensure: bool = True):
        mag_or_404(mag_id)
        if ensure:
            jobs.ensure(mag_id, n)
            jobs.prefetch(mag_id, n)
        row = store.page(mag_id, n) or {"status": "none"}
        return {"status": row.get("status"), "error": row.get("error"),
                "data": row.get("data"), "cost": row.get("cost")}

    @app.post("/api/mag/{mag_id}/page/{n}/retranslate")
    def retranslate(mag_id: str, n: int):
        mag_or_404(mag_id)
        return {"status": jobs.ensure(mag_id, n, force=True)}

    @app.post("/api/mag/{mag_id}/page/{n}/region")
    def region(mag_id: str, n: int, bbox: list[float] = Body(..., embed=True)):
        m = mag_or_404(mag_id)
        row = store.page(mag_id, n)
        if not row or row["status"] != "done":
            raise HTTPException(409, "page not processed yet")
        data = row["data"]
        blocks, terms, usage = engine.process_region(
            m, n, bbox, store.glossary(m["series"]), data.get("page_summary"))
        start = len(data["blocks"])
        for i, b in enumerate(blocks):
            data["blocks"].append({"id": f"r{start + i}", "type": b.get("type", "other"),
                                   "bbox": b["bbox"], "ja": b.get("ja", ""), "zh": b.get("zh", ""),
                                   "note": b.get("note", ""), "supplement": True})
        store.add_terms(m["series"], terms)
        store.save_page(mag_id, n, data, usage.cost, cfg.model, PROMPT_VERSION, add_cost=True)
        return {"added": len(blocks), "data": data}

    @app.get("/api/glossary/{series}")
    def get_glossary(series: str):
        return {"series": series, "terms": store.glossary(series)}

    @app.put("/api/glossary/{series}")
    def put_glossary(series: str, terms: list[dict] = Body(..., embed=True)):
        store.replace_glossary(series, terms)
        return {"ok": True}

    @app.get("/api/mag/{mag_id}/export.md", response_class=PlainTextResponse)
    def export_md(mag_id: str):
        m = mag_or_404(mag_id)
        out = [f"# {m['title']}\n"]
        for row in store.all_pages(mag_id):
            out.append(f"\n## 第 {row['page'] + 1} 页\n")
            for b in row["data"]["blocks"]:
                if b["type"] == "meta":
                    continue
                prefix = "### " if b["type"] in ("title", "heading") else ""
                out.append(f"{prefix}{b['zh']}\n")
                if b.get("note"):
                    out.append(f"> 译注：{b['note']}\n")
        return Response("\n".join(out), media_type="text/markdown; charset=utf-8")

    web = ROOT / "web"
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
