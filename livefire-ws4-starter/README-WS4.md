# WS4 starter files (LiveFire Fusion: agents, workflow and contracts)

Files from the "WS4 Build Guide", as tested on Oct 23 2026:
- src/drill-runner: 33 tests pass (contracts, policy, and the Agent Framework workflow on stubs, T-00 to T-10).
- Every scenario reaches its expected terminal state from the command line (--stub --auto-approve).
- A stubbed drill reached VERIFIED in WS5's real Control Center, with all four decisions clicked in Chromium.
- scripts/create_agents.py --dry-run builds both Foundry agent definitions.
- infra/modules/foundry.bicep and drill-runner.bicep compile with no lint warnings, as does the main.bicep wiring
  shown in the guide.
- handoffs/ws5-csrf-first-visit.patch fixes a Control Center first-visit CSRF bug; WS5's 12 tests still pass.
Not yet verified (needs Azure): Foundry calls and strict-schema acceptance, the MCP connection type, the
Container Apps Job, WS2's Ops Service (not built yet), live GitHub calls, the Docker build.

Run locally, all stubs:
  cd src/drill-runner && pip install -r requirements-dev.txt && pytest -q
  EVENT_SOURCE=dir:../../evidence python -m livefire.runner --drill-id drill-g1-demo --stub --stub-delay 1
