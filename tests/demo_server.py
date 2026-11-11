"""Run the real app on the sample PDF, with FakeClient replaying fixture responses.

python tests/demo_server.py <magazine dir> <data dir> [port]
"""
import sys
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_client import FakeClient  # noqa: E402

from maglens import extract  # noqa: E402
from maglens.config import load_config  # noqa: E402
from maglens.server import create_app  # noqa: E402



cfg = load_config(library_dir=sys.argv[1], data_dir=sys.argv[2])
engine = extract.Engine.__new__(extract.Engine)
engine.cfg, engine.client = cfg, FakeClient()
orig = engine._call
engine._call = lambda *a, **k: (time.sleep(1.5), orig(*a, **k))[1]
uvicorn.run(create_app(cfg, engine), host="127.0.0.1", port=int(sys.argv[3]) if len(sys.argv) > 3 else 8765,
            log_level="warning")
