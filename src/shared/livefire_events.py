"""LiveFire event store: the one module that knows where drill events live.

Every component writes events through this file, so there is one format, one validator
and one place that knows about Blob storage (see contracts/event-catalog.md).

    EVENT_SOURCE=dir:<path>                       reads/writes <path>/<drill-id>/events.jsonl
    EVENT_SOURCE=blob:<account-url>/<container>   same file as an append blob, via managed identity

It needs only the standard library, plus azure-identity and azure-storage-blob for Blob mode.
It can also be run from a shell script; see `python livefire_events.py --help`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Iterable

DRILL_ID_RE = re.compile(r"^drill-[A-Za-z0-9-]{1,64}$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
EVENTS_FILE = "events.jsonl"


# ---------------------------------------------------------------- format and validation

def validate_drill_id(drill_id: str) -> str:
    """Every drill ID is checked before any file or blob is touched."""
    if not isinstance(drill_id, str) or not DRILL_ID_RE.fullmatch(drill_id):
        raise ValueError("invalid drill id")
    return drill_id


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_event(drill_id: str, action: str, result: str = "ok", details: dict | None = None,
               ts: str | None = None) -> dict:
    event = {"ts": ts or now_ts(), "drill_id": drill_id, "action": action,
             "result": result, "details": details or {}}
    validate_event(event)
    return event


def validate_event(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    validate_drill_id(event.get("drill_id"))
    if not isinstance(event.get("action"), str) or len(event["action"]) > 64 \
            or not ACTION_RE.fullmatch(event["action"]):
        raise ValueError(f"invalid action: {event.get('action')!r}")
    if not isinstance(event.get("result"), str) or len(event["result"]) > 64:
        raise ValueError("result must be a short string")
    if not isinstance(event.get("details", {}), dict):
        raise ValueError("details must be an object")
    try:
        parse_ts(event["ts"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("ts must be an ISO 8601 timestamp") from None
    return event


def parse_lines(text: str) -> list[dict]:
    """Complete lines only: a half-written last line is skipped until it ends in a newline."""
    lines = text.split("\n")
    lines.pop()  # either "" after the final newline, or an incomplete line
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(validate_event(json.loads(line)))
        except (ValueError, TypeError):
            continue  # one bad line from another team must not blank the screen
    return events


def encode(event: dict) -> bytes:
    return (json.dumps(validate_event(event), separators=(", ", ": ")) + "\n").encode()


# ---------------------------------------------------------------- stores

class DirStore:
    """Events on disk: <root>/<drill-id>/events.jsonl (WS2's format, for G1 and laptops)."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._lock = threading.Lock()

    def _path(self, drill_id: str) -> Path:
        return self.root / validate_drill_id(drill_id) / EVENTS_FILE

    def list_drills(self) -> list[dict]:
        drills = []
        if self.root.is_dir():
            for child in self.root.iterdir():
                f = child / EVENTS_FILE
                if DRILL_ID_RE.fullmatch(child.name) and f.is_file():
                    drills.append({"drill_id": child.name, "updated": f.stat().st_mtime})
        return sorted(drills, key=lambda d: d["updated"], reverse=True)

    def exists(self, drill_id: str) -> bool:
        return self._path(drill_id).is_file()

    def read(self, drill_id: str) -> list[dict]:
        path = self._path(drill_id)
        if not path.is_file():
            return []
        return parse_lines(path.read_text(encoding="utf-8", errors="replace"))

    def append(self, drill_id: str, event: dict) -> None:
        if event.get("drill_id") != drill_id:
            raise ValueError("event drill_id does not match")
        path = self._path(drill_id)
        data = encode(event)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())


class BlobStore:
    """Events in Blob storage: append blob <drill-id>/events.jsonl, read with managed identity."""

    def __init__(self, account_url: str, container: str):
        from azure.identity import DefaultAzureCredential  # imported only in blob mode
        from azure.storage.blob import ContainerClient

        # AZURE_CLIENT_ID (set by the Bicep module) picks the user-assigned identity.
        self._container = ContainerClient(account_url, container, credential=DefaultAzureCredential())

    def _blob(self, drill_id: str):
        return self._container.get_blob_client(f"{validate_drill_id(drill_id)}/{EVENTS_FILE}")

    def list_drills(self) -> list[dict]:
        drills = []
        for blob in self._container.list_blobs():
            drill_id, _, name = blob.name.partition("/")
            if name == EVENTS_FILE and DRILL_ID_RE.fullmatch(drill_id):
                drills.append({"drill_id": drill_id, "updated": blob.last_modified.timestamp()})
        return sorted(drills, key=lambda d: d["updated"], reverse=True)

    def exists(self, drill_id: str) -> bool:
        return self._blob(drill_id).exists()

    def read(self, drill_id: str) -> list[dict]:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            data = self._blob(drill_id).download_blob().readall()
        except ResourceNotFoundError:
            return []
        return parse_lines(data.decode("utf-8", errors="replace"))

    def append(self, drill_id: str, event: dict) -> None:
        from azure.core.exceptions import ResourceExistsError
        if event.get("drill_id") != drill_id:
            raise ValueError("event drill_id does not match")
        blob = self._blob(drill_id)
        try:
            blob.create_append_blob(if_none_match="*")  # never overwrites an existing drill
        except ResourceExistsError:
            pass
        blob.append_block(encode(event))


def store_from_env(source: str | None = None):
    source = source or os.environ.get("EVENT_SOURCE", "dir:./evidence")
    kind, _, target = source.partition(":")
    if kind == "dir" and target:
        return DirStore(target)
    if kind == "blob" and "/" in target:
        account_url, _, container = target.rstrip("/").rpartition("/")
        if account_url.startswith("https://") and container:
            return BlobStore(account_url, container)
    raise RuntimeError("EVENT_SOURCE must be dir:<path> or blob:https://<account>.blob.core.windows.net/<container>")


# ---------------------------------------------------------------- live tail and replay

async def live(store, drill_id: str, poll: float = 0.5) -> AsyncIterator[list[dict]]:
    """Yields the full event list now, then again every time it grows."""
    validate_drill_id(drill_id)
    seen = -1
    while True:
        events = await asyncio.to_thread(store.read, drill_id)
        if len(events) != seen:
            seen = len(events)
            yield events
        await asyncio.sleep(poll)


async def replay(events: list[dict], speed: float = 10.0, max_gap: float = 3.0) -> AsyncIterator[list[dict]]:
    """Plays recorded events back with their real spacing, sped up, long waits capped."""
    shown = []
    previous = None
    for event in events:
        if previous is not None:
            gap = (parse_ts(event["ts"]) - parse_ts(previous["ts"])).total_seconds() / max(speed, 0.1)
            await asyncio.sleep(min(max(gap, 0.0), max_gap))
        shown.append(event)
        previous = event
        yield list(shown)


# ---------------------------------------------------------------- command line for shell scripts

def _wait_for(store, drill_id: str, match, timeout: float, poll: float = 1.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in store.read(drill_id):
            if match(event):
                return event
        time.sleep(poll)
    return None


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write and wait for LiveFire drill events (uses EVENT_SOURCE).")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="append one event")
    a.add_argument("drill_id"); a.add_argument("action"); a.add_argument("result", nargs="?", default="ok")
    a.add_argument("details", nargs="?", default="{}", help="JSON object")

    r = sub.add_parser("request-approval", help="append approval.requested; prints the request id")
    r.add_argument("drill_id"); r.add_argument("options", help='JSON list of {"id","label"[,"broad"]}')

    w = sub.add_parser("wait-decision", help="wait for approval.decided; prints the chosen option id")
    w.add_argument("drill_id"); w.add_argument("request_id"); w.add_argument("--timeout", type=float, default=900)

    k = sub.add_parser("wait-action", help="wait for an action that appears after the current end of the drill")
    k.add_argument("drill_id"); k.add_argument("action"); k.add_argument("--timeout", type=float, default=86400)
    k.add_argument("--after", type=int, default=None, help="only events after this many lines (default: now)")

    args = p.parse_args(list(argv) if argv is not None else None)
    store = store_from_env()

    if args.cmd == "append":
        store.append(args.drill_id, make_event(args.drill_id, args.action, args.result, json.loads(args.details)))
        return 0
    if args.cmd == "request-approval":
        options = json.loads(args.options)
        if not isinstance(options, list) or not all(isinstance(o, dict) and "id" in o and "label" in o for o in options):
            p.error('options must be a JSON list of {"id": ..., "label": ...}')
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        store.append(args.drill_id, make_event(args.drill_id, "approval.requested", "ok",
                                               {"request_id": request_id, "options": options}))
        print(request_id)
        return 0
    if args.cmd == "wait-decision":
        event = _wait_for(store, args.drill_id, lambda e: e["action"] == "approval.decided"
                          and e["details"].get("request_id") == args.request_id, args.timeout)
        if event is None:
            print("timeout", file=sys.stderr)
            return 2
        print(event["details"].get("option_id", ""))
        return 0
    if args.cmd == "wait-action":
        start = len(store.read(args.drill_id)) if args.after is None else args.after
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            for event in store.read(args.drill_id)[start:]:
                if event["action"] == args.action:
                    print(json.dumps(event))
                    return 0
            time.sleep(1.0)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
