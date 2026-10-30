#!/usr/bin/env python3
"""Recomputes a drill's verdict from its sealed evidence, without trusting the runner.

  python scripts/verify_package.py evidence/drill-20261118-0930
Checks: the event log up to the seal matches package.json's SHA-256, and the six verdict inputs recomputed
from those events match the sealed ones.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "drill-runner"))
from livefire.policy import compute_verdict  # noqa: E402

folder = Path(sys.argv[1])
package = json.loads((folder / "package.json").read_text())
lines = [line for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
sealed = [json.loads(line) for line in lines[: package["events_count"]]]
digest = hashlib.sha256("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in sealed).encode()).hexdigest()
inputs = compute_verdict(sealed).model_dump()
claimed = package["verdict_inputs"] or {}
checks = {"event log matches the seal": digest == package["events_sha256"],
          **{f"input {k}": inputs[k] == claimed.get(k) for k in inputs if k != "reasons"}}
for name, ok in checks.items():
    print(f"{'ok      ' if ok else 'MISMATCH'} {name}")
print(f"terminal state: {package['terminal_state']} ({package['reason']})")
sys.exit(0 if all(checks.values()) else 1)
