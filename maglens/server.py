"""FastAPI app: JSON API + static frontend.

Two roles: `admin` sees everything (translation controls, glossary, export, costs, progress);
`reader` can only browse finished translations. Without <data_dir>/auth.json there is no login
and every request is the local admin.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import guard, library, render
from .auth import COOKIE, ROLES, SESSION_DAYS, Auth
from .config import ROOT, Config
from .extract import make_engine
from .jobs import Jobs
from .prompts import PROMPT_VERSION
from .store import Store, available

LOCAL_ADMIN = {"name": "本机", "role": "admin"}
# Sent on every response by the app itself (not only by Caddy), so local runs and tests see exactly what
# readers get. Scripts only from this site: no inline <script> or on...= handlers anywhere in web/.
SECURITY_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
                               "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS = "max-age=31536000"  # HTTPS only for a year; only sent over HTTPS (Caddy passes the scheme through)
PUBLIC = {"/login.html", "/login.js", "/style.css", "/robots.txt", "/api/login", "/api/me", "/favicon.ico", "/favicon.svg"}
ADMIN_ONLY = [  # (method, path regex)
    ("POST", r"/api/library/rescan"),
    ("POST", r"/api/mag/[^/]+/page/\d+/retranslate"),
    ("POST", r"/api/mag/[^/]+/page/\d+/region"),
    ("GET", r"/api/glossary/.*"),
    ("PUT", r"/api/glossary/.*"),
    ("GET", r"/api/mag/[^/]+/export\.md"),
    ("GET", r"/api/users"),
    ("POST", r"/api/users(/.*)?"),
    ("DELETE", r"/api/users/.*"),
    ("GET", r"/api/feedback"),
    ("GET", r"/api/access/summary"),
]
USERNAME = re.compile(r"[\w.\-]{1,32}")  # \w includes CJK, so Chinese names are fine
MIN_PASSWORD = 8
WIDTHS = (360, 1600, 2400)
LOGIN_MAX_FAILURES, LOGIN_WINDOW = 10, 15 * 60  # per client IP
FEEDBACK_MAX_CHARS = 1000
FEEDBACK_MAX_POSTS, FEEDBACK_WINDOW = 20, 60 * 60  # per user
FEEDBACK_LIST_DEFAULT, FEEDBACK_LIST_MAX = 200, 1000


# Pages a PDF distributor slipped in (Chinese promo / QR-code pages): skipped when reading, and left
# out when pairing spreads so left/right pages line up again.
_KANA = re.compile(r"[\u3040-\u30ff]")
_PROMO = re.compile(r"我读网|wodu|阿静杂志|资源站|资源库|站长|推广页|非杂志内容|不是 ?POPEYE|并非 ?POPEYE|"
                    r"二维码名片|扫一扫|代找|微信.{0,12}二维码")


def inserted_pages(store: Store, mag_id: str) -> list[int]:
    out = []
    for row in store.all_pages(mag_id):
        d = row["data"] or {}
        blocks = d.get("blocks", [])
        text = (d.get("page_summary") or "") + " ".join(b.get("zh", "") for b in blocks)
        kana = len(_KANA.findall("".join(b.get("ja", "") for b in blocks)))
        if kana <= 20 and _PROMO.search(text):
            out.append(row["page"])
    return out


def published_image(cfg: Config, mag_id: str, n: int, w: int) -> Path:
    return cfg.data_dir / "pages" / mag_id / f"{n:04d}_{w}.webp"


def create_app(cfg: Config, engine=None) -> FastAPI:
    store = Store(cfg.db_path, pages_dir=cfg.data_dir / "pages")
    engine = engine or make_engine(cfg)
    jobs = Jobs(cfg, store, engine)
    auth = Auth(cfg.data_dir / "auth.json")
    library.scan_all(cfg, store)
    app = FastAPI(title="MagLens")
    app.state.store, app.state.jobs, app.state.auth = store, jobs, auth
    # Reader rate limits + access log (maglens/guard.py). Both on app.state so tests can swap the
    # clock and shrink the rules.
    app.state.limiter = guard.RateLimiter()  # read per request, so a test can replace it
    access_log = app.state.access_log = guard.JsonlFile(cfg.data_dir / "access.jsonl", guard.ACCESS_MAX_BYTES)

    # ------------------------------------------------------------------ auth
    @app.middleware("http")
    async def session(request: Request, call_next):
        path = request.url.path
        auth.refresh()  # accounts edited from the CLI apply at once, including the very first one
        if not auth.enabled:
            # No accounts yet: only direct local access is trusted. Anything coming through a reverse
            # proxy (Caddy adds X-Forwarded-For) or from another machine is refused, so a server
            # started before `maglens.users add` never hands out admin access.
            client = request.client.host if request.client else ""
            if request.headers.get("x-forwarded-for") or client not in ("127.0.0.1", "::1", "localhost", "testclient"):
                return PlainTextResponse("MagLens：还没有创建账号。请先在服务器上运行 "
                                         "python -m maglens.users add <名字> --role admin", status_code=403)
            request.state.user = LOCAL_ADMIN
        else:
            request.state.user = auth.read_token(request.cookies.get(COOKIE))
            if request.state.user is None and path not in PUBLIC and not path.startswith("/fonts/"):
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "login required"}, status_code=401)
                return RedirectResponse("/login.html", status_code=303)
        user = request.state.user
        if user and user["role"] != "admin" and any(
                request.method == m and re.fullmatch(p, path) for m, p in ADMIN_ONLY):
            return JSONResponse({"detail": "admin only"}, status_code=403)
        resp = await call_next(request)
        resp.headers.update(SECURITY_HEADERS)
        if request.url.scheme == "https":
            resp.headers["Strict-Transport-Security"] = HSTS
        return resp

    def is_admin(request: Request) -> bool:
        return (request.state.user or {}).get("role") == "admin"

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def log_access(request: Request, mag_id: str, n: int, event: str):
        try:  # best effort: a full disk must not stop people reading
            access_log.append({"time": guard.utc_now(), "user": request.state.user["name"],
                               "ip": client_ip(request), "mag_id": mag_id, "page": n, "event": event})
        except OSError as e:
            print(f"access log write failed: {e}", file=sys.stderr)

    def throttle(request: Request, mag_id: str, n: int, bucket: str):
        """Count a reader request against its bucket (per account + IP; large images and page data also per
        account). Runs before the issue lookup, so probing hidden issues counts too."""
        if is_admin(request):
            return
        name = request.state.user["name"]
        checks = [(bucket, (name, guard.client_key(client_ip(request))))]
        if bucket in ("img", "data"):
            checks.append((bucket + "_account", (name,)))
        over = app.state.limiter.hit(checks)
        if over:
            if over[2]:
                log_access(request, mag_id, n, "limited:" + over[0])
            raise HTTPException(429, guard.TOO_FAST, headers={"Retry-After": str(over[1])})

    def can_see(request: Request, mag_id: str) -> bool:
        """Readers can be limited to some issues (auth.json `mags`); admins see everything."""
        user = request.state.user or {}
        return user.get("role") == "admin" or user.get("mags") is None or mag_id in user["mags"]

    @app.get("/api/me")
    def me(request: Request):
        # model: can this server translate? (--manual / published deployments can't)
        return {"user": request.state.user, "auth": auth.enabled,
                "model": not getattr(engine, "manual", False)}

    failures: dict[str, list[float]] = {}  # client IP -> recent failed login times

    @app.post("/api/login")
    def login(request: Request, name: str = Body(...), password: str = Body(...)):
        ip, now = (request.client.host if request.client else "?"), time.time()
        recent = [t for t in failures.get(ip, []) if now - t < LOGIN_WINDOW]
        if len(recent) >= LOGIN_MAX_FAILURES:
            raise HTTPException(429, "尝试次数太多，请 15 分钟后再试")
        user = auth.check_login(name.strip(), password)
        if not user:
            failures[ip] = recent + [now]
            raise HTTPException(401, "用户名或密码不对")
        failures.pop(ip, None)
        resp = JSONResponse({"user": user})
        resp.set_cookie(COOKIE, auth.make_token(user), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ------------------------------------------------------------------ accounts (admin only)
    # Every handler re-reads auth.json first, so accounts added with `python -m maglens.users` while the
    # server is running are neither invisible here nor overwritten by the next save.
    def accounts(request: Request) -> str:
        if not auth.refresh():  # never save over an auth.json we couldn't read
            raise HTTPException(503, "账号文件 auth.json 读取失败，请先在服务器上检查")
        if not auth.enabled:
            raise HTTPException(409, "本机没有启用账号，账号管理只在服务器上使用")
        return request.state.user["name"]

    def account_list():
        return {"users": [Auth.public(u) for u in auth.users.values()],
                "magazines": [{"id": m["id"], "title": m["title"], "series": m["series"]}
                              for m in store.magazines() if available(m)]}

    def check_mags(mags: list[str] | None, role: str) -> list[str] | None:
        if mags is None or role == "admin":
            return None
        known = {m["id"] for m in store.magazines() if available(m)}  # readers can't open the rest
        if not mags or not all(isinstance(x, str) and x in known for x in mags):
            raise HTTPException(400, "请选择要开放的杂志")
        return sorted(set(mags))

    def check_password(pw: str):
        if len(pw) < MIN_PASSWORD:
            raise HTTPException(400, f"密码至少 {MIN_PASSWORD} 位")

    def existing(name: str) -> dict:
        if name not in auth.users:
            raise HTTPException(404, f"没有这个账号：{name}")
        return auth.users[name]

    @app.get("/api/users")
    def users_list(request: Request):
        accounts(request)
        return account_list()

    @app.post("/api/users")
    def users_add(request: Request, name: str = Body(...), password: str = Body(...), role: str = Body("reader"),
                  mags: list[str] | None = Body(None)):
        accounts(request)
        name = name.strip()
        if not USERNAME.fullmatch(name):
            raise HTTPException(400, "用户名 1–32 个字符，只能用中英文、数字和 . _ -")
        if name in auth.users:
            raise HTTPException(409, f"账号已存在：{name}")
        if role not in ROLES:
            raise HTTPException(400, "角色只能是 reader 或 admin")
        check_password(password)
        auth.set_user(name, password, role, check_mags(mags, role))
        return account_list()

    @app.post("/api/users/{name}/password")
    def users_password(name: str, request: Request, password: str = Body(..., embed=True)):
        me_name = accounts(request)
        u = existing(name)
        check_password(password)
        auth.set_user(name, password, u["role"], u.get("mags"))  # also logs out that user's existing sessions
        resp = JSONResponse(account_list())
        if name == me_name:  # keep the admin who changed their own password logged in
            resp.set_cookie(COOKIE, auth.make_token(u), max_age=SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https")
        return resp

    @app.post("/api/users/{name}/role")
    def users_role(name: str, request: Request, role: str = Body(..., embed=True)):
        me_name = accounts(request)
        u = existing(name)
        if role not in ROLES:
            raise HTTPException(400, "角色只能是 reader 或 admin")
        if name == me_name and role != "admin":
            raise HTTPException(400, "不能取消自己的管理员身份")
        u["role"] = role
        if role == "admin":
            u.pop("mags", None)
        auth.save()
        return account_list()

    @app.post("/api/users/{name}/mags")
    def users_mags(name: str, request: Request, mags: list[str] | None = Body(..., embed=True)):
        accounts(request)
        u = existing(name)
        if u["role"] == "admin":
            raise HTTPException(400, "管理员可以看所有杂志")
        auth.set_mags(name, check_mags(mags, u["role"]))
        return account_list()

    @app.delete("/api/users/{name}")
    def users_remove(name: str, request: Request):
        me_name = accounts(request)
        existing(name)
        if name == me_name:
            raise HTTPException(400, "不能删除自己正在使用的账号")
        auth.remove_user(name)
        return account_list()

    # ------------------------------------------------------------------ library
    def mag_or_404(mag_id: str, request: Request | None = None) -> dict:
        m = store.magazine(mag_id)
        if not m:
            raise HTTPException(404, "magazine not found")
        if request is not None and not is_admin(request):
            info = next((x for x in store.magazines() if x["id"] == mag_id), None)
            if not info or not available(info) or not can_see(request, mag_id):
                raise HTTPException(404, "magazine not available yet")
        return m

    def reader_mag(m: dict) -> dict:
        return {"id": m["id"], "title": m["title"], "series": m["series"], "pages": m["pages"],
                "aspect": m["aspect"], "done": m["done"], "available": available(m), "cover": m.get("cover", 0)}

    def visible_mags(request: Request) -> list[dict]:
        covers = store.covers()
        mags = [{**m, "cover": covers.get(m["id"], 0)} for m in store.magazines()]
        if is_admin(request):
            return [{**m, "available": available(m)} for m in mags]
        out = [reader_mag(m) for m in mags if can_see(request, m["id"])]
        return out if cfg.reader_show_incomplete else [m for m in out if m["available"]]

    @app.get("/api/library")
    def get_library(request: Request):
        base = {"magazines": visible_mags(request), "user": request.state.user, "auth": auth.enabled}
        if not is_admin(request):
            return base
        return {**base, "dir": str(cfg.library_dir), "mock": type(engine).__name__ == "MockEngine",
                "manual": bool(getattr(engine, "manual", False)), "model": cfg.model,
                "total_cost": round(store.total_cost(), 4)}

    @app.post("/api/library/rescan")
    def rescan():
        return {"magazines": library.scan_all(cfg, store)}

    @app.get("/api/progress")
    def progress(request: Request):
        """Overall progress + the background runner's status. Readers only get availability."""
        mags = visible_mags(request)
        if not is_admin(request):
            return {"magazines": mags}
        runner = None
        f = cfg.data_dir / "runner.json"
        if f.exists():
            try:
                runner = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                runner = None
        return {"done": sum(m["done"] for m in mags), "pages": sum(m["pages"] for m in mags),
                "cost": round(store.total_cost(), 4),
                "magazines": [{"id": m["id"], "title": m["title"], "series": m["series"], "done": m["done"],
                               "pages": m["pages"], "available": m["available"], "cover": m["cover"],
                               "cost": round(m["cost"] or 0, 4)} for m in mags],
                "runner": runner}

    @app.get("/api/mag/{mag_id}")
    def get_mag(mag_id: str, request: Request):
        m = mag_or_404(mag_id, request)
        st, skip = store.page_statuses(mag_id), inserted_pages(store, mag_id)
        if not is_admin(request):
            info = next(x for x in store.magazines() if x["id"] == mag_id)
            return {**reader_mag(info), "statuses": st, "skip": skip}
        return {**m, "statuses": st, "skip": skip}

    @app.get("/api/mag/{mag_id}/page/{n}.png")
    def page_png(mag_id: str, n: int, request: Request, w: int = 1600):
        throttle(request, mag_id, n, "thumb" if w <= 360 else "img")
        m = mag_or_404(mag_id, request)
        w = 360 if w <= 360 else 1600 if w <= 1600 else 2400
        pub = published_image(cfg, mag_id, n, w)
        if pub.exists():
            return FileResponse(pub, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})
        if not m["path"] or not Path(m["path"]).exists():
            raise HTTPException(404, "page image not found")
        p = render.cached_png(cfg.cache_dir, mag_id, Path(m["path"]), n, w)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/mag/{mag_id}/page/{n}")
    def page_data(mag_id: str, n: int, request: Request, ensure: bool = True):
        throttle(request, mag_id, n, "data")
        mag_or_404(mag_id, request)
        admin = is_admin(request)
        if not admin:
            log_access(request, mag_id, n, "view")
        if ensure and admin:  # readers never trigger (paid) model calls
            jobs.ensure(mag_id, n)
            jobs.prefetch(mag_id, n)
        row = store.page(mag_id, n) or {"status": "none"}
        out = {"status": row.get("status"), "data": row.get("data")}
        if admin:
            out.update(error=row.get("error"), cost=row.get("cost"))
        elif out["status"] == "error":
            out["error"] = "这一页暂时没有译文"
        return out

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

    # ------------------------------------------------------------------ feedback
    # One JSON object per line in <data_dir>/feedback.jsonl. It only exists on the server (sync.sh
    # never uploads or overwrites it), like auth.json.
    feedback_file = guard.JsonlFile(cfg.data_dir / "feedback.jsonl")
    feedback_lock = threading.Lock()  # guards feedback_posts; sync handlers run in a thread pool
    # (user name, client IP) -> recent post times (monotonic). Keyed by IP too because the public
    # trial account is shared: one visitor hitting the limit must not silence everyone else on it.
    feedback_posts: dict[tuple[str, str], list[float]] = {}

    @app.post("/api/feedback")
    def feedback_add(request: Request, mag_id: Any = Body(None), page: Any = Body(None),
                     text: Any = Body(None)):
        if not isinstance(mag_id, str) or not mag_id:
            raise HTTPException(400, "缺少杂志")
        m = mag_or_404(mag_id, request)  # a scoped reader gets 404 for issues hidden from them
        if not isinstance(page, int) or isinstance(page, bool) or not 0 <= page < m["pages"]:
            raise HTTPException(400, "页码不对")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            raise HTTPException(400, "请写下你的意见")
        if len(text) > FEEDBACK_MAX_CHARS:
            raise HTTPException(400, f"最多 {FEEDBACK_MAX_CHARS} 字")
        name, now = request.state.user["name"], time.monotonic()
        key = (name, client_ip(request))
        entry = {"time": guard.utc_now(), "user": name,
                 "mag_id": mag_id, "title": m["title"], "page": page, "text": text}
        with feedback_lock:
            recent = [t for t in feedback_posts.get(key, []) if now - t < FEEDBACK_WINDOW]
            if len(recent) >= FEEDBACK_MAX_POSTS:
                feedback_posts[key] = recent
                raise HTTPException(429, "反馈太频繁了，请过一会儿再发")
            feedback_file.append(entry)
            feedback_posts[key] = recent + [now]
        return {"ok": True}

    @app.get("/api/feedback")
    def feedback_list(limit: int = FEEDBACK_LIST_DEFAULT):
        limit = max(1, min(limit, FEEDBACK_LIST_MAX))
        return {"feedback": feedback_file.entries(newest=limit)}

    # ------------------------------------------------------------------ access log (admin only)
    # <data_dir>/access.jsonl: one line per reader page view and per 429. Server-only, like feedback.
    @app.get("/api/access/summary")
    def access_summary(hours: int = guard.SUMMARY_DEFAULT_HOURS):
        hours = max(1, min(hours, guard.SUMMARY_MAX_HOURS))
        return guard.summarize(access_log.entries(include_old=True), hours)

    web = ROOT / "web"
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
