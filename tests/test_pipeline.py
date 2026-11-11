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
