#!/usr/bin/env python3
"""Writes contracts/schemas/*.json from the contracts package, so the published schemas can't drift.
Run from the repo root after any contract change: python scripts/export_schemas.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "drill-runner"))
from livefire.schemas import export  # noqa: E402

out = ROOT / "contracts" / "schemas"
out.mkdir(parents=True, exist_ok=True)
for name, schema in export().items():
    (out / name).write_text(json.dumps(schema, indent=2) + "\n")
    print("wrote", out / name)
