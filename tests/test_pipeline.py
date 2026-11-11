import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_client import FIXTURES, FakeClient  # noqa: E402
from make_sample import build_pdf, page1, page2, page3  # noqa: E402

from maglens import extract, render  # noqa: E402
from maglens.config import load_config  # noqa: E402


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    p = tmp_path_factory.mktemp("mags") / "SAMPLE 2026年1月号.pdf"
    build_pdf([page1(), page2(), page3()], p, [True, True, False])
    return p


@pytest.fixture
def engine(tmp_path):
    cfg = load_config(data_dir=tmp_path)
    e = extract.Engine.__new__(extract.Engine)
    e.cfg, e.client = cfg, FakeClient()
    return e


def mag(pdf):
    return {"id": "x", "path": str(pdf), "title": "SAMPLE", "pages": 3, "series": "sample"}


def test_vertical_spans_are_merged(pdf):
    spans = render.text_spans(pdf, 1)
    assert any(s["vertical"] and s["text"].startswith("創業四十年") for s in spans)


def test_text_layer_detection(pdf):
    assert extract.text_layer_usable(render.text_spans(pdf, 0))
    assert not extract.text_layer_usable(render.text_spans(pdf, 2))
    assert not extract.text_layer_usable([{"text": ""}])


def test_full_coverage_needs_no_supplement(pdf, engine):
    data, _ = engine.process_page(mag(pdf), 1, [], None)
    assert data["meta"]["passes"] == ["main"]
    assert data["meta"]["coverage"] >= 0.95


def test_missing_sidebar_triggers_supplement(pdf, engine):
    main = copy.deepcopy(FIXTURES["main_2"])
    sidebar = [b for b in main["blocks"] if b["type"] == "sidebar"]
    main["blocks"] = [b for b in main["blocks"] if b["type"] != "sidebar"]
    engine.client.overrides = {"main_2": main, "supplement_2": {"blocks": sidebar, "new_terms": []}}
    data, _ = engine.process_page(mag(pdf), 1, [], None)
    assert data["meta"]["passes"] == ["main", "supplement"]
    assert any(b["supplement"] and b["type"] == "sidebar" for b in data["blocks"])
    prompt = engine.client.calls[1]["messages"][0]["content"][-1]["text"]
    assert "定休日：水曜" in prompt  # the uncovered text-layer line was sent back to the model
    assert data["meta"]["coverage"] >= 0.95


def test_scanned_page_gets_tiled_audit(pdf, engine):
    data, _ = engine.process_page(mag(pdf), 2, [], None)
    assert data["meta"]["passes"] == ["main", "audit"]
    assert sum(c["type"] == "image" for c in engine.client.calls[1]["messages"][0]["content"]) == 5


def test_request_shape(pdf, engine):
    engine.process_page(mag(pdf), 0, [{"ja": "高円寺", "zh": "高圆寺"}], "prev")
    kw = engine.client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "高円寺 → 高圆寺" in kw["messages"][0]["content"][-1]["text"]


def test_cny_and_bbox_postprocess():
    assert extract.add_cny("950日元（含税）", 0.047) == "950日元（含税，约45元人民币）"
    assert extract.add_cny("招牌意面（950日元）。", 0.047) == "招牌意面（950日元，约45元人民币）。"
    assert extract.add_cny("600日元（不含税）／950日元", 0.047) == "600日元（不含税，约28元人民币）／950日元（约45元人民币）"
    assert extract.add_cny("28,000日元", 0.047).endswith("（约1,316元人民币）")
    assert extract._clamp_bbox([10, 20, 5, 120]) == [0.05, 0.2, 0.1, 1.0]


def test_region_bbox_mapped_to_page(pdf, engine):
    engine.client.overrides = {"main_0": {"blocks": [{"type": "caption", "bbox": [0, 0, 0.5, 0.5],
                                                     "ja": "x", "zh": "y", "note": ""}], "new_terms": []}}
    # region prompt has no "第 N /" marker -> FakeClient answers with main_0
    blocks, _, _ = engine.process_region(mag(pdf), 0, [0.2, 0.4, 0.6, 0.8], [], None)
    assert [round(v, 3) for v in blocks[0]["bbox"]] == [0.2, 0.4, 0.4, 0.6]


# -- job scheduling: mock / manual modes ---------------------------------------
def _jobs(tmp_path, pdf, engine_cls, **cfg_kw):
    from maglens.jobs import Jobs
    from maglens.store import Store
    cfg = load_config(data_dir=tmp_path, **cfg_kw)
    store = Store(cfg.db_path)
    store.upsert_magazine({**mag(pdf), "aspect": 0.7, "mtime": 0})
    return Jobs(cfg, store, engine_cls(cfg)), store


def test_mock_results_are_redone_by_real_engine(tmp_path, pdf):
    jobs, store = _jobs(tmp_path, pdf, extract.MockEngine)
    jobs.ensure("x", 0)
    jobs.pool.shutdown(wait=True)
    assert store.page("x", 0)["model"] == "mock"
    assert jobs.ensure("x", 0) == "done"          # mock engine keeps its own result

    real = extract.Engine.__new__(extract.Engine)
    real.cfg, real.client = jobs.cfg, FakeClient()
    from maglens.jobs import Jobs
    jobs2 = Jobs(jobs.cfg, store, real)
    assert jobs2.ensure("x", 0) == "queued"      # placeholder is not treated as done
    jobs2.pool.shutdown(wait=True)


def test_manual_mode_waits_and_never_processes(tmp_path, pdf):
    jobs, store = _jobs(tmp_path, pdf, extract.ManualEngine, manual=True)
    assert jobs.ensure("x", 1) == "waiting"
    jobs.prefetch("x", 1)
    jobs.pool.shutdown(wait=True)
    assert store.page_statuses("x") == {1: "waiting"}
    store.save_page("x", 1, {"blocks": []}, 0.0, "manual", "v1")
    assert jobs.ensure("x", 1) == "done"


# -- claude -p background runner -------------------------------------------------
def test_issue_order():
    from maglens.cli_runner import issue_key
    titles = ["POPEYE Special ", "popeye2026.01", "2026年 06月号", "POPEYE 2026.08 ", "popeye2026.11"]
    assert sorted(titles, key=issue_key, reverse=True) == [
        "popeye2026.11", "POPEYE 2026.08 ", "2026年 06月号", "popeye2026.01", "POPEYE Special "]


def _runner(tmp_path, pdf):
    from maglens.cli_runner import Runner
    from maglens.store import Store
    cfg = load_config(data_dir=tmp_path)
    store = Store(cfg.db_path)
    store.upsert_magazine({**mag(pdf), "aspect": 0.7, "mtime": 0})
    fake = str(Path(__file__).resolve().parent / "fake_claude.py")
    return Runner(cfg, store, "haiku", 2, fake), store


def test_runner_translates_all_pages(tmp_path, pdf, capsys):
    r, store = _runner(tmp_path, pdf)
    store.save_page("x", 0, {"blocks": []}, 0.0, "manual", "v1")        # already done: skipped
    r.run([store.magazine("x")])
    assert store.page_statuses("x") == {0: "done", 1: "done", 2: "done"}
    p = store.page("x", 2)
    assert p["data"]["meta"]["passes"] == ["cli"] and p["model"] == "claude-haiku-5-5"
    assert "约45元人民币" in p["data"]["blocks"][0]["zh"]
    assert store.page("x", 1)["data"]["meta"]["coverage"] is not None   # page with text layer
    assert {"ja": "テスト", "zh": "测试", "kind": "other", "locked": 0} in store.glossary("sample")
    out = capsys.readouterr().out
    assert "MAG_DONE" in out and "done=3/3" in out and "ALL_DONE" in out
    status = json.loads((tmp_path / "runner.json").read_text())
    assert status["state"] == "done" and status["run_done"] == 2


def test_runner_pauses_on_limit(tmp_path, pdf, monkeypatch):
    from maglens import cli_runner
    r, store = _runner(tmp_path, pdf)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(cli_runner.LimitError):
        r.do_page(store.magazine("x"), 0)
