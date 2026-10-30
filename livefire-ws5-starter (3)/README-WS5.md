# WS5 starter files (LiveFire Fusion Control Center)

Files from the "WS5 Build Guide", as tested:
- src/control-center: 12 tests pass; checked in Chromium against both sample drills
  (False Green, open decision and verified states; replay; no console errors; CSP active).
- infra/modules/control-center.bicep: compiles with no lint warnings.
- scripts/decide-and-recover.sh: tested against stubbed WS2 scripts; needs WS2's lib.sh and recover.sh.
Not yet verified: the Docker build, Easy Auth and the Blob event store (all need Azure).

Run locally:
  cd src/control-center/app
  AUTH_MODE=local ALLOW_LOCAL_AUTH=1 EVENT_SOURCE=dir:../fixtures/drills uvicorn main:app --port 8080
