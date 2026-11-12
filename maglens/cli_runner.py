"""Translate whole magazines in the background through Claude Code's headless mode (`claude -p`).

This path needs no Anthropic API key: each page is one `claude -p` call that runs under the
user's own Claude subscription (and its usage credits once plan limits are hit).

    python -m maglens.cli_runner --series popeye --model haiku      # 杂志目录默认读 config.toml

Magazines are processed newest issue first, one at a time; pages within an issue run
concurrently. Progress is written to the shared SQLite DB (the web UI picks it up) and to
<data_dir>/runner.json. Machine-readable events go to stdout, one per line:

    MAG_START <title> | todo=<n>
    MAG_DONE  <title> | done=<d>/<pages> errors=<e> cost=$<c> time=<mm:ss>
    PAUSED    <reason>
    ALL_DONE  done=<d>/<pages> cost=$<c>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import library, prompts, render
from .config import load_config
from .extract import coverage, finish_page, text_layer_usable
from .store import Store

CLI_PASS = "cli"
CALL_TIMEOUT = 600  # seconds of wall-clock time per `claude -p` call
LIMIT_PAT = re.compile(r"usage limit|rate.?limit|hit your limit|limit reached|spend limit|429|overloaded",
                       re.I)


class LimitError(RuntimeError):
    """Plan / spend / rate limit: pause everything and retry later."""


class PageError(RuntimeError):
    pass


def issue_key(title: str) -> tuple[int, int, int]:
    """'popeye2026.11' / 'POPEYE 2026.08' / '2026年 06月号' -> (2026, 11, 0); 'BRUTUS No.1061' -> (0, 0, 1061).
    Neither (specials) -> (0, 0, 0). Sorting descending puts the newest issue first. Keep in step with
    issueKey/issueNo in web/app.js."""
    m = re.search(r"(20\d\d)\D{0,3}?(\d{1,2})(?!\d)", title)
    if m:
        return int(m.group(1)), int(m.group(2)), 0
    n = re.search(r"\bNo\.?\s*(\d+)", title, re.I)
    return 0, 0, int(n.group(1)) if n else 0


def page_prompt(mag: dict, n: int, glossary: list[dict], prev_summary: str | None,
                text_layer: str | None) -> str:
    base = prompts.main_user_text(text_layer=text_layer, glossary=glossary, prev_summary=prev_summary,
                                  title=mag["title"], page_no=n + 1, total=mag["pages"])
    return (base + "\n\n请先用 Read 工具依次查看当前目录下的三张图：page.png（整页）、"
            "top.png（上半部 y 0–0.55 的放大图）、bottom.png（下半部 y 0.45–1 的放大图）。"
            "放大图只用来看清小字，bbox 一律按整页 0–1 坐标给出。然后按 JSON schema 输出这一页全部文本块。")


class Runner:
    def __init__(self, cfg, store: Store, model: str, concurrency: int, claude_bin: str):
        self.cfg, self.store, self.model = cfg, store, model
        self.concurrency, self.claude = concurrency, claude_bin
        self.status_file = cfg.data_dir / "runner.json"
        self.lock = threading.Lock()
        self.pause_until = 0.0
        self.stop = False
        self.started = time.time()
        self.run_done = 0
        self.run_cost = 0.0
        self.state = {"pid": os.getpid(), "state": "starting", "model": model,
                      "concurrency": concurrency, "started": self.started}
        self.schema = json.dumps(prompts.PAGE_SCHEMA, ensure_ascii=False)

    # ------------------------------------------------------------------ status
    def emit(self, line: str):
        print(line, flush=True)

    def write_status(self, **kw):
        with self.lock:
            self.state.update(kw)
            elapsed = time.time() - self.started
            self.state.update(run_done=self.run_done, run_cost=round(self.run_cost, 4),
                              elapsed=round(elapsed), updated=time.time())
            remaining = self.state.get("remaining_pages")
            if self.run_done >= 3 and remaining is not None:
                self.state["eta_sec"] = round(elapsed / self.run_done * remaining)
            tmp = self.status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.status_file)

    def remaining(self, order: list[dict]) -> int:
        n = 0
        for m in order:
            st = self.store.page_statuses(m["id"])
            n += sum(1 for p in range(m["pages"]) if st.get(p) != "done")
        return n

    # ------------------------------------------------------------------ one page
    def call_claude(self, workdir: Path, prompt: str) -> dict:
        cmd = [self.claude, "-p", "--model", self.model,
               "--tools", "Read", "--allowedTools", "Read",
               "--system-prompt", prompts.SYSTEM, "--json-schema", self.schema,
               "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
               "--max-budget-usd", "0.25", prompt]
        # Wall-clock timeout: subprocess's own timeout uses a monotonic clock that stops while the
        # Mac sleeps, so a connection that died during sleep could hang forever.
        with tempfile.TemporaryFile("w+") as fo, tempfile.TemporaryFile("w+") as fe:
            proc = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                    text=True)
            deadline = time.time() + CALL_TIMEOUT
            while proc.poll() is None:
                if time.time() > deadline or self.stop:
                    proc.kill()
                    proc.wait()
                    raise PageError(f"claude -p timed out after {CALL_TIMEOUT // 60} min"
                                    if not self.stop else "stopped")
                time.sleep(1)
            fo.seek(0); fe.seek(0)
            proc_out, proc_err = fo.read(), fe.read()
        out = proc_out.strip()
        try:
            res = json.loads(out.splitlines()[-1]) if out else {}
        except ValueError:
            res = {}
        if not res:
            msg = (proc_err or out or f"exit {proc.returncode}").strip()[:300]
            if LIMIT_PAT.search(msg):
                raise LimitError(msg)
            raise PageError(msg)
        if res.get("is_error") or not res.get("structured_output"):
            msg = str(res.get("result") or res.get("subtype") or "no structured output")[:300]
            if res.get("api_error_status") in (429, 529) or LIMIT_PAT.search(msg):
                raise LimitError(msg)
            raise PageError(msg)
        return res

    def do_page(self, mag: dict, n: int):
        path = Path(mag["path"])
        spans = render.text_spans(path, n)
        usable = text_layer_usable(spans)
        layer = "\n".join(s["text"] for s in spans) if usable else None
        prev = self.store.page(mag["id"], n - 1) if n > 0 else None
        prev_summary = ((prev or {}).get("data") or {}).get("page_summary") if prev else None
        glossary = self.store.glossary(mag["series"])
        self.store.set_status(mag["id"], n, "running")

        work_root = self.cfg.data_dir / "cli_work"
        work_root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f"p{n:04d}_", dir=work_root))
        try:
            (workdir / "page.png").write_bytes(render.render_png(path, n, 2000))
            (workdir / "top.png").write_bytes(render.render_png(path, n, 2000, clip=(0, 0, 1, 0.55)))
            (workdir / "bottom.png").write_bytes(render.render_png(path, n, 2000, clip=(0, 0.45, 1, 1)))
            t0 = time.time()
            res = self.call_claude(workdir, page_prompt(mag, n, glossary, prev_summary, layer))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        data = res["structured_output"]
        blocks, terms = data.get("blocks", []), data.get("new_terms", [])
        cov = coverage(spans, blocks)[0] if usable else None
        page = finish_page(self.cfg, data, blocks, terms, [CLI_PASS], cov, usable)
        cost = float(res.get("total_cost_usd") or 0)
        model = next(iter(res.get("modelUsage") or {}), self.model)
        u = res.get("usage") or {}
        page["meta"].update(engine="claude-code", seconds=round(time.time() - t0, 1), usage=[{
            "model": model, "in": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0), "out": u.get("output_tokens"), "cost": round(cost, 5)}])
        self.store.add_terms(mag["series"], terms)
        self.store.save_page(mag["id"], n, page, cost, model, prompts.PROMPT_VERSION)
        with self.lock:
            self.run_done += 1
            self.run_cost += cost

    def page_worker(self, mag: dict, n: int, errors: list):
        for attempt in range(3):
            while time.time() < self.pause_until and not self.stop:
                time.sleep(5)
            if self.stop:
                return
            try:
                self.do_page(mag, n)
                return
            except LimitError as e:
                with self.lock:
                    if time.time() >= self.pause_until:
                        self.pause_until = time.time() + 15 * 60
                        self.emit(f"PAUSED    {mag['title']} p{n + 1}: {e} | retry in 15 min")
                self.write_status(state="paused", last_error=str(e), resume_at=self.pause_until)
            except Exception as e:  # noqa: BLE001 - keep the run going, record the page as failed
                if attempt == 2:
                    self.store.set_status(mag["id"], n, "error", f"{type(e).__name__}: {e}"[:500])
                    errors.append(n)
                    self.write_status(last_error=f"{mag['title']} p{n + 1}: {e}"[:300])
                    return
                time.sleep(10)
        # three limit pauses in a row: leave the page for the next run
        errors.append(n)

    # ------------------------------------------------------------------ run
    def run(self, order: list[dict]):
        self.write_status(state="running", order=[{"id": m["id"], "title": m["title"]} for m in order],
                          remaining_pages=self.remaining(order))
        for mag in order:
            if self.stop:
                break
            st = self.store.page_statuses(mag["id"])
            todo = [p for p in range(mag["pages"]) if st.get(p) != "done"]
            if not todo:
                continue
            t0, cost0, errors = time.time(), self.run_cost, []
            self.emit(f"MAG_START {mag['title']} | todo={len(todo)}")
            self.write_status(state="running", current={"id": mag["id"], "title": mag["title"],
                                                        "pages": mag["pages"]})
            with ThreadPoolExecutor(self.concurrency) as pool:
                futs = [pool.submit(self.page_worker, mag, n, errors) for n in todo]
                for f in futs:
                    f.result()
                    self.write_status(state="paused" if time.time() < self.pause_until else "running",
                                      remaining_pages=self.remaining(order))
            done = sum(1 for s in self.store.page_statuses(mag["id"]).values() if s == "done")
            mins, secs = divmod(int(time.time() - t0), 60)
            self.emit(f"MAG_DONE  {mag['title']} | done={done}/{mag['pages']} errors={len(errors)} "
                      f"cost=${self.run_cost - cost0:.2f} time={mins}:{secs:02d}")
        total = sum(1 for m in order for s in self.store.page_statuses(m["id"]).values() if s == "done")
        pages = sum(m["pages"] for m in order)
        self.write_status(state="stopped" if self.stop else "done", current=None,
                          remaining_pages=self.remaining(order))
        self.emit(f"ALL_DONE  done={total}/{pages} cost=${self.run_cost:.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 Claude Code（claude -p）后台批量翻译整本杂志")
    ap.add_argument("--dir", help="杂志目录（默认读 config.toml）")
    ap.add_argument("--data-dir")
    ap.add_argument("--model", default="haiku", help="claude -p 的模型（haiku / sonnet / opus 或完整 ID）")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--series", help="把所选杂志都归入这个系列（共用术语表），如 popeye")
    ap.add_argument("--only", nargs="*", help="只跑标题包含这些字样的杂志")
    ap.add_argument("--claude", default=shutil.which("claude") or "claude")
    a = ap.parse_args(argv)

    cfg = load_config(library_dir=a.dir, data_dir=a.data_dir, model=a.model)
    store = Store(cfg.db_path)
    mags = library.scan_all(cfg, store)
    if a.only:
        mags = [m for m in mags if any(s.lower() in m["title"].lower() for s in a.only)]
    if a.series:
        for m in mags:
            if m["series"] != a.series:
                store.set_series(m["id"], a.series)
                m["series"] = a.series
    order = sorted(mags, key=lambda m: issue_key(m["title"]), reverse=True)

    runner = Runner(cfg, store, a.model, a.concurrency, a.claude)

    def on_signal(*_):
        runner.stop = True
        runner.emit("STOPPING  (finishing pages in flight)")
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    runner.emit("ORDER     " + " > ".join(m["title"].strip() for m in order))
    runner.run(order)


if __name__ == "__main__":
    sys.exit(main())
