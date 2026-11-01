# WS5: Control Center and story

The Control Center is the one screen the customer watches during a LiveFire drill. It reads a
drill's `events.jsonl` and shows, live: the nine stages, a banner that answers *"can customers
order right now?"*, infrastructure health next to the customer journey, the scorecard, the
decision card and the kill switch.

**How it connects to the other workstreams:** only through events, in the format in
`contracts/event-catalog.md`. It never calls another service. WS2's scripts, WS1's load
generator and WS4's workflow write events; the Control Center reads them and writes back just two
kinds: `approval.decided` (a human decision) and `kill.requested`.

## What's where

```
contracts/event-catalog.md          the contract: send this to WS4, WS2, WS1, WS3
src/shared/livefire_events.py       the event store every team writes through (folder or Blob)
src/control-center/
  app/main.py                       routes, sign-in guard, live stream, approvals, kill switch
  app/events.py                     re-exports src/shared/livefire_events.py
  app/stages.py                     events → stages, banner, feed, scorecard (one pure function)
  app/security.py                   sign-in modes, CSRF, security headers
  app/templates/, app/static/       the pages, one CSS file, one JS file (no CDN, no build step)
  fixtures/make_samples.py          writes the two sample drills
  fixtures/drills/                  drill-sample-g1-manual, drill-sample-full-loop
  tests/                            24 tests
  requirements.txt, Dockerfile
infra/modules/control-center.bicep  Azure Container Apps deployment (called from WS2's control.bicep)
.github/workflows/control-center.yml  tests on every PR, image to GHCR from main
scripts/decide-and-recover.sh       G2 bridge: makes the decision buttons act before WS4 exists
scripts/kill-watcher.sh             G2 bridge: makes the kill switch act before WS4 exists
docs/walkthrough/                   deck corrections, run of show, presenter notes
```

## Run it on your laptop (5 minutes)

You need Python 3.12 and Git. Nothing else.

```bash
# 1. install (once)
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r src/control-center/requirements-dev.txt

# 2. run the tests: all 24 should pass
pytest -q src/control-center/tests

# 3. start it on the sample drills
cd src/control-center/app
AUTH_MODE=local ALLOW_LOCAL_AUTH=1 EVENT_SOURCE=dir:../fixtures/drills uvicorn main:app --port 8080
```

On Windows PowerShell, set the variables first:
`$env:AUTH_MODE="local"; $env:ALLOW_LOCAL_AUTH="1"; $env:EVENT_SOURCE="dir:../fixtures/drills"; uvicorn main:app --port 8080`

Open <http://localhost:8080>:

* **drill-sample-full-loop → Live** shows the end state: VERIFIED, every tile filled.
* **drill-sample-full-loop → Replay ×10** tells the whole story in about four minutes (×20 takes about two).
* **drill-sample-g1-manual** ends on CUSTOMERS ORDERING AGAIN, recovered by restart (correct: no agents yet).

### Try a decision

```bash
# from src/control-center/app, in a second terminal
mkdir -p ../fixtures/drills/drill-try-decision
sed '/"approval.decided"/,$d' ../fixtures/drills/drill-sample-full-loop/events.jsonl \
  | sed 's/drill-sample-full-loop/drill-try-decision/g' > ../fixtures/drills/drill-try-decision/events.jsonl
```

Open `drill-try-decision`, click an option. The last line of that file is now an
`approval.decided` with your name. A second click on the same request returns 409.
(Delete the folder afterwards so it isn't committed.)

### Watch a real drill live (the G1/G2 demo moment)

Point it at WS2's evidence folder while WS2 runs a drill in another terminal:

```bash
EVENT_SOURCE=dir:<repo>/evidence AUTH_MODE=local ALLOW_LOCAL_AUTH=1 uvicorn main:app --port 8080
```

### Try passcode sign-in (what the lab uses)

```bash
PASSCODE="$(openssl rand -base64 18)"; echo "passcode: $PASSCODE"
export PASSCODE_SHA256="$(printf '%s' "$PASSCODE" | sha256sum | cut -d' ' -f1)"
export SESSION_SECRET="$(openssl rand -base64 48)"
AUTH_MODE=passcode EVENT_SOURCE=dir:../fixtures/drills uvicorn main:app --port 8080
```

Use `http://localhost` (not `127.0.0.1` or another machine's IP), or the cookies won't stick.

## G2 (Nov 2) checklist

- [ ] Event catalog committed and sent to WS4, WS2, WS1 (see `docs/handoffs.md`)
- [ ] WS2's Ops Service writes events through `src/shared/livefire_events.py`
- [ ] WS2 has added the control environment, `id-lf-control-center`, control Key Vault and evidence
      container to `control.bicep`, and calls `infra/modules/control-center.bicep`
- [ ] Secrets in the control Key Vault: `cc-session-secret`, `cc-passcode-sha256`
- [ ] Deployed; smoke test: sign-in works, a drill from Blob appears, CSP header present, no console errors
- [ ] Decision and kill switch wired through `scripts/decide-and-recover.sh` and `scripts/kill-watcher.sh`
- [ ] Business tiles filled by WS1's `metrics.orders`

**Fallback if the lab deploy isn't ready:** run the Control Center on the driver's laptop against
WS2's local evidence folder (above). That's a legitimate G2 demo of live stages.

## Where this differs from the build guide, and why

| Guide | This build | Why |
|---|---|---|
| FastAPI | Starlette (what FastAPI is built on) | Same routes and behaviour; FastAPI's extra features (`/docs`, validation) were going to be switched off anyway |
| `Referrer-Policy: no-referrer` | `same-origin` | With `no-referrer`, browsers send `Origin: null` on form posts, so the guide's Origin check refuses the sign-in form. Found in a browser test. Still sends nothing to other sites. |
| `events.py` in the app | Canonical copy in `src/shared/livefire_events.py` | This is the guide's G2 step 1, done now. The Docker build context is `src/` so the image includes it. |
| 12 tests | 24 tests | Same coverage plus Easy Auth header trust, kill switch, replay end, half-written lines, gap capping |

## Not verified yet (needs Azure)

The Docker image build, the Bicep module, Easy Auth and the Blob event store. The code for all four
is written, and the image's file layout and passcode sign-in were tested locally; run them in the
first lab session and fix anything small.
