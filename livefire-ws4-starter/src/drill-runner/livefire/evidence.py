"""The evidence package: one sealed JSON summary per drill, next to its events.jsonl."""
import hashlib
import json

from .contracts import EvidencePackage
from .events import BlobEventStore, DirEventStore


def _put(store, drill_id: str, name: str, data: bytes) -> None:
    """Writes once. In Blob, version-level immutability keeps it; overwrite=False refuses a second write."""
    if isinstance(store, DirEventStore):
        path = store.root / drill_id / name
        if path.exists():
            raise FileExistsError(path)
        path.write_bytes(data)
    elif isinstance(store, BlobEventStore):
        store.container.get_blob_client(f"{drill_id}/{name}").upload_blob(data, overwrite=False)
    else:
        raise TypeError("unknown event store")


def _first(events, *actions, result=None):
    for e in events:
        if e["action"] in actions and (result is None or str(e.get("result")) == result):
            return e["ts"]
    return ""


def build(run, s) -> tuple[EvidencePackage, list[dict]]:
    events = run.log.read()
    raw = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events).encode()
    report = s.reports[-1] if s.reports else None
    package = EvidencePackage(
        drill_id=run.cfg.drill_id,
        scenario=run.cfg.scenario,
        terminal_state=s.terminal,
        reason=s.reason,
        verdict_inputs=s.verdict_inputs,
        policy=run.cfg.policy,
        agents=run.meta.get("agents", {}),
        prompt_sha256=run.meta.get("prompt_sha256", {}),
        model_deployment=run.meta.get("model_deployment", ""),
        base_ref=run.cfg.base_ref,
        candidate={"branch": report.branch if report else "", "commit": s.commit, "image": s.image,
                   "ci_run": s.ci_url, "attempts": s.attempt,
                   "change_report": report.model_dump() if report else None},
        options=s.options.model_dump() if s.options else None,
        brief=s.brief.model_dump() if s.brief else None,
        counts={
            "events": len(events),
            "tool_calls": sum(e["action"].endswith(".tool_call") for e in events),
            "denied_tool_calls": sum(e["action"].endswith(".tool_call") and e.get("result") == "deny" for e in events),
            "policy_denials": sum(e["action"] == "recover.broad" and e.get("result") == "denied" for e in events),
            "referee_runs": sum(e["action"].startswith("referee.") for e in events),
            "human_decisions": sum(e["action"] == "approval.decided" for e in events),
            "candidate_attempts": s.attempt,
        },
        usage={"tokens": run.tokens, "est_cost_usd": round(run.cost, 4)},
        timeline={"started": _first(events, "drill.start"), "fault": _first(events, "fault.inject"),
                  "false_green": _first(events, "referee.after-restore", result="FAIL"),
                  "verdict": _first(events, "verdict"), "sealed_from_events_through": events[-1]["ts"] if events else ""},
        events_sha256=hashlib.sha256(raw).hexdigest(),
        events_count=len(events),
        residual_risks=s.brief.residual_risks if s.brief else [],
    )
    return package, events


def seal(run, s) -> str:
    package, _ = build(run, s)
    data = json.dumps(package.model_dump(mode="json"), indent=2).encode()
    _put(run.log.store, run.cfg.drill_id, "package.json", data)
    digest = hashlib.sha256(data).hexdigest()
    run.log.write("package.ready", "ok", {"path": f"{run.cfg.drill_id}/package.json", "sha256": digest,
                                          "events_sha256": package.events_sha256})
    return digest


def pull_request_body(run, s) -> str:
    report, brief = s.reports[-1], s.brief
    rows = "\n".join(f"| {m.change} | {m.evidence_ref} | {m.acceptance_criterion} |" for m in report.evidence_to_change)
    inputs = s.verdict_inputs.model_dump() if s.verdict_inputs else {}
    checks = "\n".join(f"- {'PASS' if v else 'FAIL'}: {k}" for k, v in inputs.items() if isinstance(v, bool))
    return (
        f"Candidate from LiveFire drill `{run.cfg.drill_id}` (attempt {s.attempt}), produced by the Engineer agent "
        f"and verified by the deterministic workflow. **Not merged by any agent: a person reviews and decides.**\n\n"
        f"**Verdict:** {s.terminal.value}\n\n{checks}\n\n"
        f"**Required behavior (from the Planner's brief):** {brief.required_behavior}\n\n"
        f"| Change | Evidence | Acceptance criterion |\n| --- | --- | --- |\n{rows}\n\n"
        f"**Residual risks:** {'; '.join(brief.residual_risks) or 'none recorded'}\n\n"
        f"Evidence package: `{run.cfg.drill_id}/package.json` (sha256 `{s.package_sha256[:16]}…`). "
        f"CI run: {s.ci_url}\n"
    )
