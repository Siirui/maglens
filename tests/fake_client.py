"""A stand-in for anthropic.Anthropic that replays fixture JSON (no network)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "sample_responses.json").read_text())


class _Stream:
    def __init__(self, owner, kw):
        self.owner, self.kw = owner, kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        self.owner.calls.append(self.kw)
        content = self.kw["messages"][0]["content"]
        text = next(c["text"] for c in content if c["type"] == "text")
        n_images = sum(1 for c in content if c["type"] == "image")
        if "漏掉了一些文字" in text:
            key = f"supplement_{self.owner.page}"
        elif n_images > 1:
            key = f"audit_{self.owner.page}"
        else:
            m = re.search(r"第 (\d+) /", text)
            self.owner.page = int(m.group(1)) if m else 0
            key = f"main_{self.owner.page}"
        data = self.owner.overrides.get(key) or FIXTURES.get(key) or {"blocks": [], "new_terms": []}
        return SimpleNamespace(
            model=self.kw["model"], stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, overrides: dict | None = None):
        self.calls: list[dict] = []
        self.page = 0
        self.overrides = overrides or {}
        stream = lambda **kw: _Stream(self, kw)  # noqa: E731
        self.messages = SimpleNamespace(stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))
