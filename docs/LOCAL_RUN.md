# 在本地翻译真实杂志的前几页（给 Claude Code 本地会话的说明）

杂志目录：`~/Magazines/2026`。只翻一期的前 6 页，不要整本翻。

1. `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`，然后跑 `python -m pytest -q tests`，确认通过。
2. 列出杂志目录里的 PDF，挑一期 POPEYE。
3. 翻译：
   - **A 方案（环境变量里有 ANTHROPIC_API_KEY）**：`python run.py --dir ~/Magazines/2026 --no-browser` 后台启动，
     用 `GET http://127.0.0.1:8765/api/library` 拿到 id，再依次请求 `GET /api/mag/{id}/page/{n}`（n=0..5），一直轮询到每页都是 status=done。
   - **B 方案（没有 key）**：不调 API，由你自己逐页查看 `maglens.render.render_png(path, n, 2000)` 渲染出来的图片，
     按 `maglens/prompts.py` 里的 SYSTEM 规则和 PAGE_SCHEMA 写出 JSON，用 `Engine._finish` 处理后，
     通过 `Store.save_page` 写进 `~/.maglens/maglens.sqlite3`，然后启动 `run.py` 查看。
4. 逐页检查：对照页面图有没有漏字（同时看 `meta.coverage`）、译文是否自然、术语是否统一，A 方案还要记下每页花了多少钱。
5. 用 Playwright 截 2–3 张阅读页截图（deviceScaleFactor 2），导出 `GET /api/mag/{id}/export.md`，
   连同一份简短的问题清单（漏字 / 误译 / 排版）一起交给用户。
