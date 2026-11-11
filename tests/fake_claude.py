#!/usr/bin/env python3
"""Stand-in for `claude -p --output-format json --json-schema ...` used by the runner tests."""
import json, os, sys
from pathlib import Path

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
assert all(Path(f).exists() for f in ("page.png", "top.png", "bottom.png")), "images missing"
if mode == "limit":
    print(json.dumps({"type": "result", "is_error": True, "api_error_status": 429,
                      "result": "Claude AI usage limit reached"}))
    sys.exit(1)
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0071,
    "usage": {"input_tokens": 4, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 16000,
              "output_tokens": 7000},
    "modelUsage": {"claude-haiku-5-5": {"costUSD": 0.0071}},
    "structured_output": {"page_kind": "article", "page_summary": "测试页",
                          "blocks": [{"type": "body", "bbox": [0.1, 0.1, 0.5, 0.2], "ja": "テスト",
                                      "zh": "价格950日元", "note": ""}],
                          "new_terms": [{"ja": "テスト", "zh": "测试", "kind": "other"}]},
}))
