"""Static files the login page needs must load before logging in (they are served without a session)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from maglens import extract  # noqa: E402
from maglens.auth import Auth  # noqa: E402
from maglens.config import load_config  # noqa: E402
from maglens.server import create_app  # noqa: E402


def test_login_page_assets_are_public(tmp_path):
    Auth(tmp_path / "auth.json").set_user("boss", "pw-boss-1", "admin")
    cfg = load_config(library_dir=tmp_path / "lib", data_dir=tmp_path, manual=True)
    c = TestClient(create_app(cfg, extract.ManualEngine(cfg)))
    for path in ("/login.html", "/login.js", "/style.css"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, path
    assert "<script>" not in c.get("/login.html").text          # no inline script: the CSP forbids it
    assert c.get("/app.js", follow_redirects=False).status_code == 303   # the reader itself still needs a login
