"""Cheap checks on web/app.js that need no browser (skipped when node is not installed)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "web" / "app.js"

# Evaluate the reader's state object with an empty store and print it.
SCRIPT = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
const m = src.match(/const S = \{[\s\S]*?\n\};/);
if (!m) { console.log("null"); process.exit(0); }
const store = { get: (k, d) => d };
eval(m[0].replace("const S", "var S"));
console.log(JSON.stringify(S));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_reader_state_has_every_field():
    # A comment pasted mid-line once swallowed `data`/`polls`, and every translation failed to load.
    out = subprocess.run(["node", "-e", SCRIPT, str(APP)], capture_output=True, text=True, check=True).stdout
    state = json.loads(out)
    assert state is not None, "const S = {...} not found in app.js"
    for key in ["mag", "page", "mode", "spread", "rtl", "boxes", "selecting", "data", "polls", "fit", "zoom", "tap"]:
        assert key in state, key
    assert state["rtl"] is False and state["data"] == {} and state["polls"] == {}
