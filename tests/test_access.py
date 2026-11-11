"""Accounts, reader/admin separation and the publish (no-PDF) deployment path."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402
from make_sample import build_pdf, page1, page2, page3  # noqa: E402

from maglens import extract, publish  # noqa: E402
from maglens.auth import Auth, hash_password, verify_password  # noqa: E402
from maglens.config import load_config  # noqa: E402
from maglens.server import create_app  # noqa: E402
from maglens.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def libdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("lib")
    build_pdf([page1(), page2(), page3()], d / "SAMPLE 2026年1月号.pdf", [True, True, False])
    build_pdf([page1(), page2()], d / "SAMPLE 2026年2月号.pdf", [True, True])
    return d


def make_app(libdir, data, users=None, translate=("1月",)):
    cfg = load_config(library_dir=libdir, data_dir=data, manual=True)
    if users:
        a = Auth(data / "auth.json")
        for name, role in users:
            a.set_user(name, "pw-" + name, role)
    app = create_app(cfg, extract.ManualEngine(cfg))
    store = app.state.store
    for m in store.magazines():                       # finish only the issues named in `translate`
        if any(t in m["title"] for t in translate):
            for n in range(m["pages"]):
                store.save_page(m["id"], n, {"page_kind": "article", "page_summary": "s", "blocks": [
                    {"id": "b0", "type": "body", "bbox": [0, 0, 1, 1], "ja": "テ", "zh": "测", "note": ""}],
                    "meta": {"passes": ["cli"], "usage": [{"cost": 0.01}]}, "new_terms": []},
                    0.0123, "claude-haiku-5-5", "v1")
    return app, store


def ids(store):
    ms = {m["title"]: m["id"] for m in store.magazines()}
    return ms["SAMPLE 2026年1月号"], ms["SAMPLE 2026年2月号"]


def login(c, name):
    r = c.post("/api/login", json={"name": name, "password": "pw-" + name})
    assert r.status_code == 200, r.text


def test_password_and_token(tmp_path):
    h = hash_password("hunter22")
    assert verify_password("hunter22", h) and not verify_password("hunter23", h)
    a = Auth(tmp_path / "auth.json")
    a.set_user("amy", "hunter22", "reader")
    assert a.check_login("amy", "hunter22") == {"name": "amy", "role": "reader"}
    assert a.check_login("amy", "nope") is None and a.check_login("bob", "hunter22") is None
    tok = a.make_token({"name": "amy"})
    assert a.read_token(tok) == {"name": "amy", "role": "reader"}
    body, sig = tok.rsplit(".", 1)
    assert a.read_token(body + "." + sig[:-2] + "xx") is None          # tampered signature
    assert a.read_token(a.make_token({"name": "amy"}, days=-1)) is None  # expired
    a.remove_user("amy")
    assert a.read_token(tok) is None                                    # deleted account
    assert json.loads((tmp_path / "auth.json").read_text())["users"] == []


def test_no_accounts_means_local_admin(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path)
    c = TestClient(app)
    lib = c.get("/api/library").json()
    assert lib["user"]["role"] == "admin" and lib["auth"] is False and "total_cost" in lib
    a, _ = ids(store)
    assert c.get(f"/api/mag/{a}/export.md").status_code == 200


def test_login_required(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader")])
    c = TestClient(app)
    assert c.get("/api/library").status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login.html"
    assert c.get("/login.html").status_code == 200
    a, _ = ids(store)
    assert c.get(f"/api/mag/{a}/page/0.png").status_code == 401        # images are private too
    assert c.post("/api/login", json={"name": "amy", "password": "wrong"}).status_code == 401
    assert "noindex" in c.get("/login.html").headers["x-robots-tag"]


def test_reader_vs_admin(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader")])
    done, todo = ids(store)
    c = TestClient(app)
    login(c, "amy")
    lib = c.get("/api/library").json()
    assert lib["user"] == {"name": "amy", "role": "reader"}
    assert not {"dir", "total_cost", "model"} & lib.keys()
    by_id = {m["id"]: m for m in lib["magazines"]}
    assert by_id[done]["available"] and not by_id[todo]["available"] and "cost" not in by_id[done]
    page = c.get(f"/api/mag/{done}/page/1").json()
    assert page["status"] == "done" and "cost" not in page
    assert c.get(f"/api/mag/{todo}/page/0").status_code == 404           # unfinished issue: not openable
    assert c.get(f"/api/mag/{todo}/page/0.png").status_code == 404
    assert c.get(f"/api/mag/{done}").json().get("path") is None
    prog = c.get("/api/progress").json()
    assert "runner" not in prog and "cost" not in prog
    for method, url, kw in [("post", f"/api/mag/{done}/page/0/retranslate", {}),
                            ("post", f"/api/mag/{done}/page/0/region", {"json": {"bbox": [0, 0, 1, 1]}}),
                            ("put", "/api/glossary/sample", {"json": {"terms": []}}),
                            ("get", "/api/glossary/sample", {}),
                            ("get", f"/api/mag/{done}/export.md", {}),
                            ("post", "/api/library/rescan", {})]:
        assert getattr(c, method)(url, **kw).status_code == 403, url
    # reading a page as a reader never queues work
    c.get(f"/api/mag/{done}/page/2")
    assert store.page_statuses(todo) == {}

    c2 = TestClient(app)
    login(c2, "boss")
    assert c2.get(f"/api/mag/{done}/export.md").status_code == 200
    assert c2.post(f"/api/mag/{done}/page/0/retranslate").status_code == 200
    assert c2.get(f"/api/mag/{todo}").status_code == 200
    assert c2.post("/api/logout").status_code == 200


def test_reader_hide_incomplete(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path, users=[("amy", "reader")])
    app.state.jobs.cfg.reader_show_incomplete = False
    c = TestClient(app)
    login(c, "amy")
    assert [m["title"] for m in c.get("/api/library").json()["magazines"]] == ["SAMPLE 2026年1月号"]


def test_publish_and_serve_without_pdfs(libdir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _, store = make_app(libdir, src)
    out = tmp_path / "dist"
    publish.main(["--out", str(out), "--dir", str(libdir), "--data-dir", str(src), "--workers", "2"])
    done, todo = ids(store)
    assert sorted(p.name for p in (out / "pages").iterdir()) == [done]   # unfinished issue skipped
    assert len(list((out / "pages" / done).glob("*.webp"))) == 3 * 3
    db = sqlite3.connect(out / "maglens.sqlite3")
    assert db.execute("SELECT path FROM magazines").fetchall() == [("",)]
    assert db.execute("SELECT SUM(cost), MAX(model) FROM pages").fetchone() == (0, "")
    assert "usage" not in db.execute("SELECT data FROM pages LIMIT 1").fetchone()[0]

    # serve it: no library dir, no PDFs
    cfg = load_config(library_dir=tmp_path / "nowhere", data_dir=out, manual=True)
    c = TestClient(create_app(cfg, extract.ManualEngine(cfg)))
    mags = c.get("/api/library").json()["magazines"]
    assert [m["id"] for m in mags] == [done] and mags[0]["available"]
    r = c.get(f"/api/mag/{done}/page/1.png?w=1600")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert c.get(f"/api/mag/{done}/page/1").json()["data"]["blocks"][0]["zh"] == "测"
