"""Configuration loading (config.toml + CLI overrides)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    library_dir: Path = Path("~/Downloads/2026").expanduser()
    data_dir: Path = Path("~/.maglens").expanduser()
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    fallbacks: bool = True
    concurrency: int = 3
    prefetch: int = 2
    style: str = "mainland_lifestyle"
    cny_rate: float = 0.047
    show_cny: bool = True
    render_long_edge: int = 2000
    coverage_threshold: float = 0.92
    mock: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "maglens.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_config(path: Path | None = None, **overrides) -> Config:
    path = path or ROOT / "config.toml"
    raw: dict = {}
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    lib, mdl, tr, ex = (raw.get(k, {}) for k in ("library", "model", "translate", "extract"))
    cfg = Config(
        library_dir=Path(lib.get("dir", "~/Downloads/2026")).expanduser(),
        data_dir=Path(lib.get("data_dir", "~/.maglens")).expanduser(),
        model=mdl.get("name", "claude-opus-5-5"),
        effort=mdl.get("effort", "medium"),
        fallbacks=bool(mdl.get("fallbacks", True)),
        concurrency=int(mdl.get("concurrency", 3)),
        prefetch=int(mdl.get("prefetch", 2)),
        style=tr.get("style", "mainland_lifestyle"),
        cny_rate=float(tr.get("cny_rate", 0.047)),
        show_cny=bool(tr.get("show_cny", True)),
        render_long_edge=int(ex.get("render_long_edge", 2000)),
        coverage_threshold=float(ex.get("coverage_threshold", 0.92)),
    )
    for k, v in overrides.items():
        if v is None:
            continue
        if k in ("library_dir", "data_dir"):
            v = Path(v).expanduser()
        setattr(cfg, k, v)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    return cfg
