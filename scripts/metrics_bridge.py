#!/usr/bin/env python3
"""G2 fallback: the load generator's business numbers, from Application Insights into the drill.

Until WS1's load generator writes `metrics.orders` itself, this reads the numbers it already sends to
the twin's Application Insights and appends a `metrics.orders` event to the drill every 30 seconds:

    orders_per_min  orders the simulated customers placed successfully in the latest export interval
                    (loadgen_orders_sent, result=ok), scaled to one minute
    failed_total    failed order attempts since the bridge started (orders_failed, from the order service)
    backlog         the latest loadgen_backlog_size

The numbers come from the load generator's own traffic, about one to three minutes behind (Application
Insights ingestion), and cover the whole twin, not one drill. The event says so in `source` and `lag_s`.
When the load generator sends nothing, no event is written and the tiles stay at a dash: no invented zeros.

Run it on a machine signed in with `az login` to the lab, next to a drill in progress:

    export EVENT_SOURCE=blob:https://<evidence-account>.blob.core.windows.net/evidence   # or dir:<path>
    python3 scripts/metrics_bridge.py <drill-id> --workspace <twin LOG_ANALYTICS_CUSTOMER_ID>
    python3 scripts/metrics_bridge.py <drill-id> --workspace <id> --dry-run --once   # print, write nothing

Stop it with Ctrl+C. Retire it when WS1's load generator writes metrics.orders.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "shared"))
from livefire_events import make_event, now_ts, parse_ts, store_from_env, validate_drill_id  # noqa: E402

NAMES = ("loadgen_orders_sent", "loadgen_backlog_size", "orders_failed")
LOOKBACK = timedelta(minutes=10)      # enough history to find the latest export interval
SAME_EXPORT_S = 10.0                  # rows this close to the newest one belong to the same export
DEFAULT_INTERVAL_S = 60.0             # the Azure Monitor OpenTelemetry default export interval


def build_query(since: datetime) -> str:
    start = (since - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = ", ".join(f"'{n}'" for n in NAMES)
    return (f"AppMetrics | where TimeGenerated >= datetime({start}) | where Name in ({names}) "
            "| project TimeGenerated, Name, Sum, Max, Result = tostring(Properties['result']) "
            "| order by TimeGenerated asc | take 20000")


def _rows(raw: list[dict]) -> list[dict]:
    """az returns every value as a string; keep the rows we can read."""
    rows = []
    for r in raw:
        try:
            rows.append({"ts": parse_ts(str(r["TimeGenerated"])), "name": str(r["Name"]),
                         "sum": float(r.get("Sum") or 0), "max": float(r.get("Max") or 0),
                         "result": str(r.get("Result") or "")})
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def summarize(raw: list[dict], since: datetime, now: datetime | None = None) -> dict | None:
    """The metrics.orders details from Application Insights rows, or None when the load generator is silent."""
    rows = _rows(raw)
    loadgen = sorted({r["ts"] for r in rows if r["name"].startswith("loadgen_")})
    if not loadgen:
        return None
    latest = loadgen[-1]
    # The export interval: the gap to the previous export, if it looks like one; else the SDK default.
    earlier = [t for t in loadgen if (latest - t).total_seconds() > SAME_EXPORT_S]
    interval = (latest - earlier[-1]).total_seconds() if earlier else DEFAULT_INTERVAL_S
    if not 15 <= interval <= 180:
        interval = DEFAULT_INTERVAL_S

    def in_latest_export(r: dict) -> bool:
        return (latest - r["ts"]).total_seconds() <= SAME_EXPORT_S

    placed = sum(r["sum"] for r in rows if r["name"] == "loadgen_orders_sent" and r["result"] == "ok"
                 and in_latest_export(r))
    backlog_rows = [r for r in rows if r["name"] == "loadgen_backlog_size" and in_latest_export(r)]
    failed = sum(r["sum"] for r in rows if r["name"] == "orders_failed" and r["ts"] >= since)
    now = now or datetime.now(timezone.utc)
    return {
        "orders_per_min": round(placed * 60.0 / interval, 1),
        "failed_total": int(round(failed)),
        "backlog": int(round(max(r["max"] for r in backlog_rows))) if backlog_rows else None,
        "source": "app-insights",
        "lag_s": max(0, round((now - latest).total_seconds())),
    }


def query(workspace: str, kql: str) -> list[dict]:
    out = subprocess.run([shutil.which("az") or "az", "monitor", "log-analytics", "query", "-w", workspace,
                          "--analytics-query", kql, "-o", "json"],
                         check=True, capture_output=True, text=True, timeout=120).stdout
    data = json.loads(out or "[]")
    return data if isinstance(data, list) else []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="metrics.orders from Application Insights (G2 fallback)")
    p.add_argument("drill_id")
    p.add_argument("--workspace", required=True, help="the twin's Log Analytics workspace (customer) ID")
    p.add_argument("--every", type=float, default=30.0, help="seconds between events (default 30)")
    p.add_argument("--since", help="count failed orders from this ISO time (default: now)")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--dry-run", action="store_true", help="print the numbers, write no event")
    a = p.parse_args(argv)
    validate_drill_id(a.drill_id)
    since = parse_ts(a.since) if a.since else datetime.now(timezone.utc)
    store = None if a.dry_run else store_from_env()

    while True:
        try:
            details = summarize(query(a.workspace, build_query(since)), since)
            if details is None:
                print(f"{now_ts()} no load generator metrics yet: is caj-loadgen running?", file=sys.stderr)
            elif a.dry_run:
                print(json.dumps(details))
            else:
                store.append(a.drill_id, make_event(a.drill_id, "metrics.orders", "ok", details))
                print(f"{now_ts()} metrics.orders {json.dumps(details)}", file=sys.stderr)
        except Exception as exc:  # a failed query or upload must not end the bridge mid-drill
            if a.once:
                raise
            print(f"{now_ts()} query failed, retrying: {type(exc).__name__}: {exc}", file=sys.stderr)
        if a.once:
            return 0
        time.sleep(max(a.every, 10.0))


if __name__ == "__main__":
    sys.exit(main())
