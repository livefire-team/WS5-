"""The only ways the workflow reaches the outside: Ops Service, agents, CI and GitHub, and the event log.

Each port has a real adapter here (or in agents.py) and a stub in stubs.py with the same behavior contract.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .contracts import ChangeReport, OpsResult, PlannerOutput


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class DependencyError(RuntimeError):
    """A required dependency failed or returned an invalid contract. Ends the drill as ESCALATED_DEPENDENCY."""


# ---------------------------------------------------------------- event log

class EventLog:
    """Appends this workflow's own events. Ops, MCP and the Control Center append theirs to the same file."""

    def __init__(self, store, drill_id: str):
        self.store, self.drill_id = store, drill_id

    def write(self, action: str, result: str = "ok", details: dict | None = None) -> dict:
        event = {"ts": now_iso(), "drill_id": self.drill_id, "action": action, "result": result,
                 "details": details or {}}
        self.store.append(self.drill_id, event)
        return event

    def read(self) -> list[dict]:
        return self.store.read(self.drill_id)


# ---------------------------------------------------------------- Ops Service (WS2)

class OpsPort(Protocol):
    async def call(self, action: str, **params) -> OpsResult: ...


class HttpOps:
    """WS2's Ops Service over its internal-only ingress. Container Apps ingress ends requests at 240 s,
    so long waits are made of short calls (contracts/ops-actions.md)."""

    CALL_TIMEOUT_S = 210
    WAIT_CHUNK_S = 180

    def __init__(self, base_url: str, key: str, drill_id: str, transport=None):
        self.drill_id = drill_id
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=self.CALL_TIMEOUT_S, transport=transport,
                                      headers={"Authorization": f"Bearer {key}"})

    async def _post(self, action: str, body: dict) -> OpsResult:
        last = None
        for attempt in range(2):                          # one retry for a transient failure, then stop (T-10)
            try:
                r = await self.http.post(f"/actions/{action}", json={"drill_id": self.drill_id, **body})
                if r.status_code in (401, 403):
                    raise DependencyError(f"Ops Service rejected the runner's key ({r.status_code})")
                if r.status_code == 422:
                    return OpsResult(action=action, result="refused", details=r.json())
                r.raise_for_status()
                return OpsResult.model_validate(r.json())
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                await asyncio.sleep(3)
            except ValueError as exc:
                raise DependencyError(f"Ops Service returned an invalid result for {action}: {exc}") from exc
        raise DependencyError(f"Ops Service unavailable for {action}: {last}")

    async def call(self, action: str, **params) -> OpsResult:
        if action != "wait_for":
            return await self._post(action, params)
        budget, since = params["timeout_s"], params.get("since") or now_iso()
        deadline = time.monotonic() + budget
        while True:
            chunk = int(max(1, min(self.WAIT_CHUNK_S, deadline - time.monotonic())))
            result = await self._post("wait_for", {"state": params["state"], "timeout_s": chunk, "since": since})
            if result.result != "timeout" or time.monotonic() >= deadline:
                return result


# ---------------------------------------------------------------- agents (Foundry, in agents.py)

@dataclass
class Usage:
    agent: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    response_id: str = ""

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentPort(Protocol):
    async def planner(self, task: dict) -> tuple[PlannerOutput, Usage]: ...
    async def engineer(self, task: dict) -> tuple[ChangeReport, Usage]: ...


# ---------------------------------------------------------------- CI and GitHub

@dataclass
class CiStatus:
    state: str                     # pending | pass | fail
    commit: str = ""               # full head SHA the run tested
    url: str = ""
    failed: list[dict] = field(default_factory=list)


class RepoPort(Protocol):
    async def head_sha(self, branch: str) -> str: ...
    async def changed_paths(self, base: str, branch: str) -> list[str]: ...
    async def ci_status(self, branch: str, commit: str) -> CiStatus: ...
    async def open_pull_request(self, branch: str, title: str, body: str) -> str: ...


class GitHubRepo:
    """The runner's own GitHub credential: read CI and diffs, open one pull request. It cannot push or merge."""

    API = "https://api.github.com"
    WORKFLOW_FILE = "order-service.yml"

    def __init__(self, repo: str, token_provider, transport=None):
        self.repo, self.token = repo, token_provider
        self.http = httpx.AsyncClient(base_url=self.API, timeout=30, transport=transport)

    async def _call(self, method: str, url: str, **kw) -> dict:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "Authorization": f"Bearer {self.token()}"}
        try:
            r = await self.http.request(method, url, headers=headers, **kw)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyError(f"GitHub call failed: {method} {url}: {exc}") from exc
        return r.json() if r.content else {}

    async def head_sha(self, branch: str) -> str:
        return (await self._call("GET", f"/repos/{self.repo}/git/ref/heads/{branch}"))["object"]["sha"]

    async def changed_paths(self, base: str, branch: str) -> list[str]:
        data = await self._call("GET", f"/repos/{self.repo}/compare/{base}...{branch}")
        return sorted({f["filename"] for f in data.get("files", [])} |
                      {f["previous_filename"] for f in data.get("files", []) if f.get("previous_filename")})

    async def ci_status(self, branch: str, commit: str) -> CiStatus:
        runs = await self._call("GET", f"/repos/{self.repo}/actions/workflows/{self.WORKFLOW_FILE}/runs",
                                params={"branch": branch, "head_sha": commit, "per_page": 5})
        run = next((r for r in runs.get("workflow_runs", []) if r["head_sha"] == commit), None)
        if run is None or run["status"] != "completed":
            return CiStatus("pending", commit, run["html_url"] if run else "")
        if run["conclusion"] == "success":
            return CiStatus("pass", commit, run["html_url"])
        jobs = (await self._call("GET", f"/repos/{self.repo}/actions/runs/{run['id']}/jobs")).get("jobs", [])
        failed = [{"job": j["name"], "steps": [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"]}
                  for j in jobs if j.get("conclusion") == "failure"]
        return CiStatus("fail", commit, run["html_url"], failed)

    async def open_pull_request(self, branch: str, title: str, body: str) -> str:
        pr = await self._call("POST", f"/repos/{self.repo}/pulls",
                              json={"title": title, "head": branch, "base": "main", "body": body, "draft": False})
        return pr["html_url"]
