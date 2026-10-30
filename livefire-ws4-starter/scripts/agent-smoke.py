#!/usr/bin/env python3
"""Runs each pinned agent once, alone, through its real MCP connection. Point WS3's server at BACKEND=fixtures first.

  python scripts/agent-smoke.py drill-agent-smoke              Planner: options, then a brief
  python scripts/agent-smoke.py drill-agent-smoke --engineer   also the Engineer (writes a candidate branch)
Needs FOUNDRY_PROJECT_ENDPOINT, PLANNER_AGENT and ENGINEER_AGENT, and Azure AI User on the project.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "drill-runner"))
from livefire.agents import FoundryAgents  # noqa: E402
from livefire.policy import check_brief, check_options, check_secrets  # noqa: E402
from livefire.contracts import AutonomyPolicy  # noqa: E402

FAILED = {"label": "after-restore", "verdict": "FAIL", "last_step": "place-order",
          "error": "We couldn't place your order (SecretUnavailable). Please try again.",
          "orders_persisted": 0, "payment_auths_persisted": 0}


async def main(drill: str, engineer: bool) -> int:
    agents = FoundryAgents(os.environ["FOUNDRY_PROJECT_ENDPOINT"], os.environ["PLANNER_AGENT"],
                           os.environ["ENGINEER_AGENT"])
    policy = AutonomyPolicy(expires_at="2099-01-01T00:00:00Z")
    out, usage = await agents.planner({"task": "investigate", "drill_id": drill, "output_stage": "options",
                                       "goal": "Customers cannot place orders. Find the cause from evidence and "
                                               "propose recovery options.",
                                       "observed": {"baseline": "PASS", "during_fault": "FAIL at place-order"}})
    print(json.dumps(out.model_dump(), indent=2))
    print(usage, "| checks:", check_options(out.recovery_options, policy, drill) or "ok",
          check_secrets(out.model_dump()) or "ok")
    out, usage = await agents.planner({"task": "remediation_brief", "drill_id": drill, "output_stage": "brief",
                                       "goal": "Infrastructure is green but the customer journey still fails.",
                                       "failed_journey": FAILED, "earlier_hypothesis": out.recovery_options.hypothesis})
    print(json.dumps(out.model_dump(), indent=2))
    print(usage, "| checks:", check_brief(out.remediation_brief, policy, drill) or "ok")
    if engineer:
        report, usage = await agents.engineer({"task": "candidate", "drill_id": drill, "attempt": 1,
                                               "base_ref": "main", "brief": out.remediation_brief.model_dump()})
        print(json.dumps(report.model_dump(), indent=2))
        print(usage)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "drill-agent-smoke", "--engineer" in sys.argv)))
