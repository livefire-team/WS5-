"""Shared drill event store (same code as the Control Center's events.py): local folder or Blob."""
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

DRILL_ID = re.compile(r"^drill-[A-Za-z0-9-]{1,64}$")


def valid_drill_id(drill_id: str) -> bool:
    return bool(DRILL_ID.fullmatch(drill_id))


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_lines(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue                      # a half-written last line; picked up on the next poll
        if isinstance(event, dict) and "ts" in event and "action" in event:
            events.append(event)
    return events


class DirEventStore:
    """Reads evidence/<drill-id>/events.jsonl, the format WS2's scripts write."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _file(self, drill_id: str) -> Path:
        if not valid_drill_id(drill_id):
            raise ValueError("invalid drill id")
        return self.root / drill_id / "events.jsonl"

    def list_drills(self) -> list[str]:
        if not self.root.is_dir():
            return []
        drills = [p.name for p in self.root.iterdir() if valid_drill_id(p.name) and (p / "events.jsonl").is_file()]
        return sorted(drills, key=lambda d: (self.root / d / "events.jsonl").stat().st_mtime, reverse=True)

    def read(self, drill_id: str) -> list[dict]:
        path = self._file(drill_id)
        return _parse_lines(path.read_text()) if path.is_file() else []

    def append(self, drill_id: str, event: dict) -> None:
        path = self._file(drill_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")


class BlobEventStore:
    """Reads <container>/<drill-id>/events.jsonl from Blob storage with the app's managed identity."""

    def __init__(self, account_url: str, container: str):
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import ContainerClient
        credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
        self.container = ContainerClient(account_url, container, credential=credential)

    def list_drills(self) -> list[str]:
        names = {b.name.split("/")[0] for b in self.container.list_blobs() if b.name.endswith("/events.jsonl")}
        return sorted((n for n in names if valid_drill_id(n)), reverse=True)

    def read(self, drill_id: str) -> list[dict]:
        if not valid_drill_id(drill_id):
            raise ValueError("invalid drill id")
        blob = self.container.get_blob_client(f"{drill_id}/events.jsonl")
        if not blob.exists():
            return []
        return _parse_lines(blob.download_blob().readall().decode())

    def append(self, drill_id: str, event: dict) -> None:
        if not valid_drill_id(drill_id):
            raise ValueError("invalid drill id")
        blob = self.container.get_blob_client(f"{drill_id}/events.jsonl")
        if not blob.exists():
            blob.create_append_blob()
        blob.append_block((json.dumps(event, separators=(",", ":")) + "\n").encode())


def store_from_env():
    source = os.environ.get("EVENT_SOURCE", "dir:./evidence")
    if source.startswith("dir:"):
        return DirEventStore(source[4:])
    if source.startswith("blob:"):
        account_url, _, container = source[5:].rpartition("/")
        return BlobEventStore(account_url, container)
    raise ValueError("EVENT_SOURCE must be dir:<path> or blob:<account-url>/<container>")


async def live(store, drill_id: str, poll_seconds: float = 0.5, keepalive_seconds: float = 15.0):
    """Yields the growing event list each time new events land, and None as a keepalive when quiet."""
    loop = asyncio.get_running_loop()
    seen, last = -1, loop.time()
    while True:
        events = store.read(drill_id)
        if len(events) != seen:
            seen, last = len(events), loop.time()
            yield events
        elif loop.time() - last >= keepalive_seconds:
            last = loop.time()
            yield None
        await asyncio.sleep(poll_seconds)


async def replay(events: list[dict], speed: float = 10.0, max_gap: float = 3.0):
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
