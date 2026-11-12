"""Accounts, reader/admin separation and the publish (no-PDF) deployment path."""
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402
from make_sample import build_pdf, page1, page2, page3  # noqa: E402

from maglens import extract, guard, publish  # noqa: E402
from maglens.auth import Auth, hash_password, verify_password  # noqa: E402
from maglens.config import load_config  # noqa: E402
from maglens.server import create_app  # noqa: E402


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


def test_login_page_assets_and_security_headers(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader")])
    anon, reader, admin = TestClient(app), TestClient(app), TestClient(app)
    login(reader, "amy"), login(admin, "boss")
    for c in (anon, reader, admin):                            # what the login page needs loads for everyone
        for path in ("/login.html", "/login.js", "/style.css"):
            assert c.get(path, follow_redirects=False).status_code == 200, path
    assert anon.get("/app.js", follow_redirects=False).status_code == 303    # the reader itself needs a login
    r = anon.get("/login.html")
    assert "script-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and "strict-transport-security" not in r.headers  # plain http
    assert "strict-transport-security" in TestClient(app, base_url="https://testserver").get("/login.html").headers


def test_no_inline_scripts_under_csp():
    """The CSP forbids inline scripts and inline event handlers, so none may exist in web/."""
    web = Path(__file__).resolve().parent.parent / "web"
    inline_script = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)
    handler_attr = re.compile(r"""<[^>]+\son[a-z]+\s*=\s*["']""", re.I)  # e.g. <button onclick="...">
    for f in [*web.glob("*.html"), *web.glob("*.js")]:
        text = f.read_text(encoding="utf-8")
        assert not inline_script.search(text), f"inline <script> in {f.name}"
        assert not handler_attr.search(text), f"inline on...= handler in {f.name}"


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

    # the account CLI on the server (no PDFs) can still limit a reader to a published issue
    from maglens import users
    Auth(out / "auth.json").set_user("trial", "pw-trial", "reader")
    users.main(["--data-dir", str(out), "scope", "trial", done])
    assert Auth(out / "auth.json").users["trial"]["mags"] == [done]


def test_inserted_promo_pages_are_detected(libdir, tmp_path):
    from maglens.server import inserted_pages
    _, store = make_app(libdir, tmp_path)
    a, _ = ids(store)

    def put(n, summary, ja, zh="", kind="ad"):
        store.save_page(a, n, {"page_kind": kind, "page_summary": summary, "blocks": [
            {"id": "b0", "type": "ad_copy", "bbox": [0, 0, 1, 1], "ja": ja, "zh": zh, "note": ""}],
            "meta": {}}, 0, "m", "v1")
    put(0, "这一页不是 POPEYE 的正文，而是中文推广页（我读网 wodu518.com）", "", "海量高清杂志资源")
    put(1, "Stone Island 品牌广告页（英文原文，并非日文）", "STONE ISLAND", "Stone Island")
    put(2, "店铺介绍：店长推荐的二维码菜单", "店長おすすめのメニューはこちら。QRコードを読み取ってください。" * 3)
    assert inserted_pages(store, a) == [0]


def test_no_accounts_refuses_proxied_or_remote_requests(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path)
    c = TestClient(app)
    assert c.get("/api/library").status_code == 200                                     # local: admin
    assert c.get("/api/library", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert TestClient(app, client=("203.0.113.9", 5000)).get("/").status_code == 403


def test_login_rate_limit(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path, users=[("amy", "reader")])
    c = TestClient(app)
    for _ in range(10):
        assert c.post("/api/login", json={"name": "amy", "password": "nope"}).status_code == 401
    r = c.post("/api/login", json={"name": "amy", "password": "pw-amy"})
    assert r.status_code == 429 and "15 分钟" in r.json()["detail"]   # even the right password waits
    other = TestClient(app, client=("198.51.100.7", 5000))
    assert other.post("/api/login", json={"name": "amy", "password": "pw-amy"}).status_code == 200


def test_password_change_logs_out_old_sessions(tmp_path):
    a = Auth(tmp_path / "auth.json")
    a.set_user("amy", "hunter22", "reader")
    old = a.make_token({"name": "amy"})
    a.set_user("amy", "hunter33", "reader")
    assert a.read_token(old) is None
    assert a.read_token(a.make_token({"name": "amy"})) == {"name": "amy", "role": "reader"}


def test_admin_manages_accounts_from_web(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader")])
    admin, reader, newbie = TestClient(app), TestClient(app), TestClient(app)
    login(admin, "boss"), login(reader, "amy")
    names = lambda r: {u["name"]: u["role"] for u in r.json()["users"]}

    # readers can't see or touch accounts
    assert reader.get("/api/users").status_code == 403
    assert reader.post("/api/users", json={"name": "x", "password": "12345678"}).status_code == 403
    assert reader.delete("/api/users/boss").status_code == 403
    assert reader.post("/api/users/amy/role", json={"role": "admin"}).status_code == 403

    assert names(admin.get("/api/users")) == {"boss": "admin", "amy": "reader"}
    r = admin.post("/api/users", json={"name": "小明", "password": "secret-123"})
    assert r.status_code == 200 and names(r)["小明"] == "reader"
    assert newbie.post("/api/login", json={"name": "小明", "password": "secret-123"}).status_code == 200
    assert newbie.get("/api/library").status_code == 200

    # validation
    assert admin.post("/api/users", json={"name": "小明", "password": "secret-123"}).status_code == 409
    assert admin.post("/api/users", json={"name": "bad name", "password": "secret-123"}).status_code == 400
    assert admin.post("/api/users", json={"name": "bo", "password": "short"}).status_code == 400
    assert admin.post("/api/users", json={"name": "bo", "password": "secret-123", "role": "root"}).status_code == 400

    # reset password: the old session is logged out, the new password works
    assert admin.post("/api/users/小明/password", json={"password": "another-456"}).status_code == 200
    assert newbie.get("/api/library").status_code == 401
    assert newbie.post("/api/login", json={"name": "小明", "password": "another-456"}).status_code == 200

    # role changes; an admin can't demote or delete themselves (so there is always an admin)
    assert names(admin.post("/api/users/amy/role", json={"role": "admin"}))["amy"] == "admin"
    assert admin.post("/api/users/boss/role", json={"role": "reader"}).status_code == 400
    assert admin.delete("/api/users/boss").status_code == 400
    assert admin.delete("/api/users/nobody").status_code == 404

    # changing your own password keeps you logged in
    assert admin.post("/api/users/boss/password", json={"password": "new-boss-pw"}).status_code == 200
    assert admin.get("/api/users").status_code == 200

    r = admin.delete("/api/users/小明")
    assert r.status_code == 200 and "小明" not in names(r)
    assert newbie.get("/api/library").status_code == 401


def test_accounts_added_by_cli_while_running_are_kept(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path, users=[("boss", "admin")])
    admin = TestClient(app)
    login(admin, "boss")
    Auth(tmp_path / "auth.json").set_user("cli-added", "pw-cli-added", "reader")  # another process
    assert admin.post("/api/users", json={"name": "web-added", "password": "secret-123"}).status_code == 200
    assert set(Auth(tmp_path / "auth.json").users) == {"boss", "cli-added", "web-added"}


def test_account_api_off_without_accounts(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path)
    c = TestClient(app)
    assert c.get("/api/users").status_code == 409
    assert c.post("/api/users", json={"name": "x", "password": "12345678"}).status_code == 409
    assert not (tmp_path / "auth.json").exists()


def test_reader_limited_to_some_issues(libdir, tmp_path):
    """Friends see everything; a public trial account (e.g. posted on 小红书) only sees one issue."""
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("friend", "reader"), ("trial", "reader")],
                          translate=("1月", "2月"))
    jan, feb = ids(store)
    Auth(tmp_path / "auth.json").set_mags("trial", [jan])
    admin, friend, trial = TestClient(app), TestClient(app), TestClient(app)
    for c, n in ((admin, "boss"), (friend, "friend"), (trial, "trial")):
        login(c, n)
    shelf = lambda c: {m["id"] for m in c.get("/api/library").json()["magazines"]}

    assert shelf(friend) == {jan, feb} and shelf(admin) == {jan, feb}
    assert shelf(trial) == {jan}
    assert {m["id"] for m in trial.get("/api/progress").json()["magazines"]} == {jan}
    assert trial.get(f"/api/mag/{jan}").status_code == 200
    for path in (f"/api/mag/{feb}", f"/api/mag/{feb}/page/0", f"/api/mag/{feb}/page/0.png"):
        assert trial.get(path).status_code == 404, path            # hidden, not just "forbidden"
        assert friend.get(path).status_code == 200, path

    # admin changes the scope from the web; it survives a password reset
    users = lambda r: {u["name"]: u.get("mags") for u in r.json()["users"]}
    r = admin.get("/api/users")
    assert users(r)["trial"] == [jan] and {m["id"] for m in r.json()["magazines"]} == {jan, feb}
    assert trial.post("/api/users/trial/mags", json={"mags": None}).status_code == 403
    assert admin.post("/api/users/trial/mags", json={"mags": ["nope"]}).status_code == 400
    assert admin.post("/api/users/trial/mags", json={"mags": []}).status_code == 400
    assert admin.post("/api/users/boss/mags", json={"mags": [jan]}).status_code == 400
    assert users(admin.post("/api/users/trial/mags", json={"mags": [feb]}))["trial"] == [feb]
    assert shelf(trial) == {feb}                                       # applies to the live session
    assert users(admin.post("/api/users/trial/password", json={"password": "trial-pw-2"}))["trial"] == [feb]
    assert users(admin.post("/api/users/trial/mags", json={"mags": None}))["trial"] is None

    # logged out: nothing
    anon = TestClient(app)
    assert anon.post("/api/users/trial/mags", json={"mags": [jan]}).status_code == 401
    assert anon.post("/api/users", json={"name": "z", "password": "secret-123", "mags": [jan]}).status_code == 401

    # creating a limited account directly
    r = admin.post("/api/users", json={"name": "xhs", "password": "secret-123", "mags": [jan]})
    assert r.status_code == 200 and users(r)["xhs"] == [jan]
    xhs = TestClient(app)
    assert xhs.post("/api/login", json={"name": "xhs", "password": "secret-123"}).status_code == 200
    assert shelf(xhs) == {jan}


def test_scope_only_finished_issues_and_cli_keeps_it(libdir, tmp_path, monkeypatch):
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("trial", "reader")])  # only 1月 finished
    jan, feb = ids(store)
    admin = TestClient(app)
    login(admin, "boss")
    assert {m["id"] for m in admin.get("/api/users").json()["magazines"]} == {jan}
    assert admin.post("/api/users/trial/mags", json={"mags": [feb]}).status_code == 400   # unfinished
    assert admin.post("/api/users/trial/mags", json={"mags": [jan]}).status_code == 200

    from maglens import users
    monkeypatch.setattr(users, "_ask_password", lambda: "new-trial-pw")
    users.main(["--data-dir", str(tmp_path), "add", "trial"])                            # re-add = new password
    assert Auth(tmp_path / "auth.json").users["trial"]["mags"] == [jan]
    with pytest.raises(SystemExit):
        users.main(["--data-dir", str(tmp_path), "scope", "trial", "typo"])
    with pytest.raises(SystemExit):
        users.main(["--data-dir", str(tmp_path), "scope", "boss", jan])


def test_broken_or_missing_auth_file_fails_closed(libdir, tmp_path):
    app, _ = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader")])
    c = TestClient(app)
    login(c, "amy")
    f = tmp_path / "auth.json"
    good = f.read_text()
    f.write_text(good[:-5])                                    # hand-edit gone wrong
    assert c.get("/api/library").status_code == 200            # still works, old accounts kept
    assert TestClient(app).get("/api/library").status_code == 401
    boss = TestClient(app)
    login(boss, "boss")
    assert boss.get("/api/users").status_code == 503           # account edits refuse to overwrite it
    f.write_text(good.replace('"role": "reader"', '"role": "root"'))   # parses, but bad entry
    assert c.get("/api/library").status_code == 200
    f.unlink()                                                 # file gone: login stays on
    assert TestClient(app).get("/api/library").status_code == 401
    assert boss.post("/api/users", json={"name": "x", "password": "secret-123"}).status_code == 503
    assert not f.exists()                                      # nothing written over the gap
    f.write_text(good)
    assert boss.get("/api/users").status_code == 200

    bak = tmp_path / "auth.bak"                                # moved away and back (same inode/mtime)
    f.rename(bak)
    assert boss.get("/api/users").status_code == 503
    bak.rename(f)
    assert boss.get("/api/users").status_code == 200

    f.write_text(good.replace('"role": "reader"', '"role": "reader", "mags": "387f"'))  # mags not a list
    assert c.get("/api/library").status_code == 200 and boss.get("/api/users").status_code == 503
    with pytest.raises(ValueError, match="格式有误"):          # a fresh start says what is wrong
        Auth(f)


def test_feedback(libdir, tmp_path):
    """Any logged-in user can leave feedback on a page they can see; only admins can read it."""
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader"), ("trial", "reader")])
    jan, feb = ids(store)                                      # only 1月 is finished
    Auth(tmp_path / "auth.json").set_mags("trial", [jan])
    admin, amy, trial, anon = TestClient(app), TestClient(app), TestClient(app), TestClient(app)
    for c, n in ((admin, "boss"), (amy, "amy"), (trial, "trial")):
        login(c, n)
    post = lambda c, **kw: c.post("/api/feedback", json={"mag_id": jan, "page": 1, "text": "译错了", **kw})

    # logged out: nothing
    assert post(anon).status_code == 401
    assert anon.get("/api/feedback").status_code == 401

    assert post(amy, text="  第二段漏译了  ").status_code == 200
    assert post(admin, page=0).status_code == 200
    assert post(trial, page=2).status_code == 200

    # readers can't list anyone's feedback, including their own
    assert amy.get("/api/feedback").status_code == 403
    assert trial.get("/api/feedback").status_code == 403

    # hidden or unfinished issues look like they don't exist; the admin can post about any issue
    assert post(trial, mag_id=feb).status_code == 404          # outside the trial account's scope
    assert post(amy, mag_id=feb).status_code == 404            # not finished yet
    assert post(amy, mag_id="nope").status_code == 404
    assert post(admin, mag_id=feb).status_code == 200

    # validation
    for kw in ({"text": ""}, {"text": "   \n "}, {"text": "字" * 1001}, {"text": 5},
               {"page": -1}, {"page": 3}, {"page": "1"}, {"page": 1.5}, {"page": True}, {"page": None},
               {"mag_id": None}, {"mag_id": ""}):
        assert post(amy, **kw).status_code == 400, kw
    assert post(amy, text="字" * 1000).status_code == 200

    # admin listing: newest first, with who/where/what
    r = admin.get("/api/feedback")
    assert r.status_code == 200
    fb = r.json()["feedback"]
    assert [(f["user"], f["mag_id"], f["page"]) for f in fb] == [
        ("amy", jan, 1), ("boss", feb, 1), ("trial", jan, 2), ("boss", jan, 0), ("amy", jan, 1)]
    assert fb[-1]["text"] == "第二段漏译了" and fb[-1]["title"] == "SAMPLE 2026年1月号"
    assert fb[-1]["time"].endswith("+00:00")
    assert [f["user"] for f in admin.get("/api/feedback?limit=2").json()["feedback"]] == ["amy", "boss"]
    assert len(admin.get("/api/feedback?limit=0").json()["feedback"]) == 1
    assert len((tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()) == 5

    # a broken line (e.g. hand-edited) is skipped, not fatal
    with (tmp_path / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"user": "x", "te\n{"user": "cut short')       # second one has no newline
    assert len(admin.get("/api/feedback").json()["feedback"]) == 5
    assert post(amy, text="接着写").status_code == 200           # not glued onto the cut-short line
    assert admin.get("/api/feedback").json()["feedback"][0]["text"] == "接着写"

    # unusual line separators inside a text don't split the stored entry
    assert post(amy, text="一 二\u0085三\r四").status_code == 200
    assert admin.get("/api/feedback").json()["feedback"][0]["text"] == "一 二\u0085三\r四"


def test_feedback_rate_limit(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path, users=[("amy", "reader"), ("bob", "reader")])
    jan, _ = ids(store)
    amy, bob = TestClient(app), TestClient(app)
    login(amy, "amy"), login(bob, "bob")
    body = {"mag_id": jan, "page": 0, "text": "好"}
    assert amy.post("/api/feedback", json={**body, "text": ""}).status_code == 400  # rejected posts don't count
    for _ in range(20):
        assert amy.post("/api/feedback", json=body).status_code == 200
    r = amy.post("/api/feedback", json=body)
    assert r.status_code == 429 and "频繁" in r.json()["detail"]
    assert bob.post("/api/feedback", json=body).status_code == 200            # per user, not global
    # the shared trial account: another visitor (other IP) on the same account isn't blocked
    amy2 = TestClient(app, client=("198.51.100.7", 5000))
    login(amy2, "amy")
    assert amy2.post("/api/feedback", json=body).status_code == 200
    assert len((tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()) == 22


def test_feedback_without_accounts_is_local_admin(libdir, tmp_path):
    app, store = make_app(libdir, tmp_path)
    jan, _ = ids(store)
    c = TestClient(app)
    assert c.get("/api/feedback").json() == {"feedback": []}                  # no file yet
    assert c.post("/api/feedback", json={"mag_id": jan, "page": 0, "text": "ok"}).status_code == 200
    assert c.get("/api/feedback").json()["feedback"][0]["user"] == "本机"


# ---------------------------------------------------------------------- rate limits + access log
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def guarded_app(libdir, tmp_path):
    """Small limits and a hand-driven clock, so the tests never sleep."""
    app, store = make_app(libdir, tmp_path, users=[("boss", "admin"), ("amy", "reader"), ("trial", "reader")])
    clock = Clock()
    app.state.limiter = guard.RateLimiter({
        "img": [(3, 60), (5, 3600)], "thumb": [(4, 600)], "data": [(3, 60), (5, 3600)],
        "img_account": [(7, 3600)]}, clock=clock)
    return app, store, clock


def access_lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "access.jsonl").read_text(encoding="utf-8").splitlines()]


def test_default_limits_fit_normal_reading():
    """The shipped numbers leave room for fast desktop reading: 30 spreads in one minute."""
    clock = Clock()
    lim = guard.RateLimiter(clock=clock)
    for _ in range(30):                                   # 2 new images + 2 page data per turn
        for _ in range(2):
            assert lim.hit([("img", ("amy", "1.2.3.4")), ("img_account", ("amy",))]) is None
            assert lim.hit([("data", ("amy", "1.2.3.4"))]) is None
        clock.t += 2
    for _ in range(180):                                  # a whole issue's thumbnail strip
        assert lim.hit([("thumb", ("amy", "1.2.3.4"))]) is None


@pytest.mark.parametrize("path,bucket", [("/api/mag/{m}/page/{n}.png?w=1600", "img"),
                                         ("/api/mag/{m}/page/{n}.png", "img"),
                                         ("/api/mag/{m}/page/{n}.png?w=360", "thumb"),
                                         ("/api/mag/{m}/page/{n}", "data")])
def test_reader_hits_each_limit(libdir, tmp_path, path, bucket):
    app, store, clock = guarded_app(libdir, tmp_path)
    jan, _ = ids(store)
    amy, boss = TestClient(app), TestClient(app)
    login(amy, "amy"), login(boss, "boss")
    limit, window = app.state.limiter.rules[bucket][0]
    url = lambda n: path.format(m=jan, n=n % 3)
    for i in range(limit):
        assert amy.get(url(i)).status_code == 200, i
        clock.t += 1
    r = amy.get(url(0))
    assert r.status_code == 429 and r.json() == {"detail": "看得太快了，休息一下再继续"}
    assert r.headers["Retry-After"] == str(window - limit)  # until the first hit leaves the window
    assert amy.get(url(0)).status_code == 429             # rejected requests don't extend the wait
    clock.t += int(r.headers["Retry-After"])
    assert amy.get(url(0)).status_code == 200
    for _ in range(20):                                   # the admin is never limited
        assert boss.get(url(0)).status_code == 200
    ev = [e for e in access_lines(tmp_path) if e["event"].startswith("limited")]
    assert len(ev) == 2 and ev[0]["event"] == "limited:" + bucket and ev[0]["user"] == "amy"
    assert ev[0]["ip"] == "testclient" and ev[0]["mag_id"] == jan and ev[0]["page"] == 0


def test_hourly_limit_and_probing_counts(libdir, tmp_path):
    app, store, clock = guarded_app(libdir, tmp_path)
    jan, feb = ids(store)
    Auth(tmp_path / "auth.json").set_mags("trial", [jan])
    trial = TestClient(app)
    login(trial, "trial")
    assert trial.get(f"/api/mag/{feb}/page/0").status_code == 404     # hidden issue: still counted
    assert trial.get("/api/mag/nope/page/0").status_code == 404
    for _ in range(3):                                    # spaced out: never hits the per-minute rule
        clock.t += 61
        assert trial.get(f"/api/mag/{jan}/page/0").status_code == 200
    r = trial.get(f"/api/mag/{jan}/page/1")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 3600 - 3 * 61
    # only real views are logged as views; the probes are not
    views = [e for e in access_lines(tmp_path) if e["event"] == "view"]
    assert [(e["mag_id"], e["page"]) for e in views] == [(jan, 0)] * 3


def test_per_account_ceiling_across_ips(libdir, tmp_path):
    app, store, clock = guarded_app(libdir, tmp_path)
    jan, _ = ids(store)
    a, b, c = (TestClient(app, client=(ip, 5000)) for ip in ("198.51.100.1", "198.51.100.2", "198.51.100.3"))
    for x in (a, b, c):
        login(x, "trial")
    got = []
    for i in range(9):
        clock.t += 61                                     # nobody trips a per-minute rule
        got.append((a, b, c)[i % 3].get(f"/api/mag/{jan}/page/{i % 3}.png").status_code)
    assert got == [200] * 7 + [429] * 2                   # each IP is at 3/h at most; the account isn't
    amy = TestClient(app, client=("198.51.100.1", 5000))  # other accounts on the same IP are fine
    login(amy, "amy")
    assert amy.get(f"/api/mag/{jan}/page/0.png").status_code == 200
    assert a.get(f"/api/mag/{jan}/page/0.png?w=360").status_code == 200   # thumbnails aren't capped
    assert {e["event"] for e in access_lines(tmp_path)} == {"limited:img_account"}


def test_access_log_and_rotation(libdir, tmp_path):
    app, store, clock = guarded_app(libdir, tmp_path)
    jan, _ = ids(store)
    amy, boss = TestClient(app), TestClient(app)
    login(amy, "amy"), login(boss, "boss")
    assert amy.get(f"/api/mag/{jan}/page/1").status_code == 200
    assert amy.get(f"/api/mag/{jan}/page/1.png").status_code == 200      # images aren't logged
    assert boss.get(f"/api/mag/{jan}/page/2").status_code == 200         # admins aren't logged
    [e] = access_lines(tmp_path)
    assert set(e) == {"time", "user", "ip", "mag_id", "page", "event"}
    assert (e["user"], e["ip"], e["mag_id"], e["page"], e["event"]) == ("amy", "testclient", jan, 1, "view")
    assert e["time"].endswith("+00:00")

    app.state.access_log.max_bytes = 300
    for i in range(8):
        clock.t += 3600
        assert amy.get(f"/api/mag/{jan}/page/{i % 3}").status_code == 200
    cur, old = tmp_path / "access.jsonl", tmp_path / "access.jsonl.1"
    assert old.exists() and not (tmp_path / "access.jsonl.2").exists()
    assert old.stat().st_size > 300 and cur.stat().st_size <= 300 + 200
    kept = len(cur.read_text().splitlines()) + len(old.read_text().splitlines())
    assert 1 < kept < 9                                   # one old file is kept, older lines dropped
    assert boss.get("/api/access/summary").json()["users"]["amy"]["views"] == kept  # reads both files


def test_access_summary(libdir, tmp_path):
    app, store, clock = guarded_app(libdir, tmp_path)
    jan, _ = ids(store)
    admin, amy, anon = TestClient(app), TestClient(app), TestClient(app)
    t1, t2 = TestClient(app, client=("198.51.100.1", 5000)), TestClient(app, client=("198.51.100.2", 5000))
    for c, n in ((admin, "boss"), (amy, "amy"), (t1, "trial"), (t2, "trial")):
        login(c, n)
    old = {"time": "2020-01-01T00:00:00+00:00", "user": "amy", "ip": "x", "mag_id": jan, "page": 0, "event": "view"}
    (tmp_path / "access.jsonl").write_text(json.dumps(old) + "\n" + '{"broken\n', encoding="utf-8")
    for n in (0, 0, 1):
        assert t1.get(f"/api/mag/{jan}/page/{n}").status_code == 200
    for _ in range(3):
        assert t2.get(f"/api/mag/{jan}/page/2").status_code == 200
    assert t2.get(f"/api/mag/{jan}/page/2").status_code == 429
    assert amy.get(f"/api/mag/{jan}/page/2").status_code == 200

    assert anon.get("/api/access/summary").status_code == 401
    assert amy.get("/api/access/summary").status_code == 403
    r = admin.get("/api/access/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["hours"] == 24 and set(s["users"]) == {"trial", "amy"}   # the 2020 line is outside 24h
    t = s["users"]["trial"]
    assert {k: t[k] for k in ("views", "distinct_pages", "ips", "limited")} == {
        "views": 6, "distinct_pages": 3, "ips": 2, "limited": 1}
    assert t["last_seen"].endswith("+00:00")
    assert s["users"]["amy"]["views"] == 1 and s["users"]["amy"]["limited"] == 0
    [lim] = s["recent_limited"]
    assert (lim["user"], lim["ip"], lim["event"], lim["page"]) == ("trial", "198.51.100.2", "limited:data", 2)
    assert admin.get("/api/access/summary?hours=0").json()["hours"] == 1
    assert admin.get("/api/access/summary?hours=999999").json()["hours"] == 168


def test_summary_recent_limited_is_newest_20():
    from datetime import datetime, timezone
    es = [{"time": f"2026-11-11T00:{i:02d}:00+00:00", "user": "u", "ip": "i", "mag_id": "m", "page": i,
           "event": "limited:img"} for i in range(30)]
    s = guard.summarize(es, 24, now=datetime(2026, 11, 11, 12, tzinfo=timezone.utc))
    assert [e["page"] for e in s["recent_limited"]] == list(range(29, 9, -1))
    assert s["users"]["u"]["limited"] == 30 and s["users"]["u"]["last_seen"] == "2026-11-11T00:29:00+00:00"


def test_limiter_prunes_idle_keys():
    clock = Clock()
    lim = guard.RateLimiter({"img": [(2, 60)], "data": [(2, 3600)]}, clock=clock)
    for i in range(100):
        assert lim.hit([("img", ("u", f"10.0.0.{i}"))]) is None
    assert lim.hit([("data", ("u", "x"))]) is None
    assert len(lim) == 101
    clock.t += 61
    assert lim.hit([("img", ("u", "fresh"))]) is None     # the sweep runs on the next request
    assert len(lim) == 2                                  # idle img keys gone; the hourly one stays
    clock.t += 3600
    lim.hit([("img", ("u", "fresh"))])
    assert len(lim) == 1
    for _ in range(500):                                  # a busy key holds one window's worth at most
        lim.hit([("img", ("u", "busy"))])
        clock.t += 31
    assert len(lim._hits[("img", ("u", "busy"))]) <= 2
