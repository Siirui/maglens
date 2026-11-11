#!/usr/bin/env python3
"""MagLens —— 本地日文杂志阅读 + 翻译。

用法: python run.py [--dir 杂志目录] [--port 8765] [--model claude-opus-5-5] [--mock]
"""
import argparse
import threading
import webbrowser

import uvicorn

from maglens.config import load_config
from maglens.server import create_app


def main():
    ap = argparse.ArgumentParser(description="MagLens 日文杂志阅读器")
    ap.add_argument("--dir", help="杂志 PDF 目录（默认读 config.toml）")
    ap.add_argument("--data-dir", help="缓存目录（默认 ~/.maglens）")
    ap.add_argument("--model", help="模型，如 claude-opus-5-5 / claude-sonnet-5-5 / claude-haiku-5-5")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mock", action="store_true", help="不调用模型，只显示 PDF 文字层（调试界面用）")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load_config(library_dir=a.dir, data_dir=a.data_dir, model=a.model, effort=a.effort,
                      mock=a.mock or None)
    app = create_app(cfg)
    url = f"http://{a.host}:{a.port}/"
    print(f"[maglens] 杂志目录: {cfg.library_dir}\n[maglens] 打开 {url}")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
