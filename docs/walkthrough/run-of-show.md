# Run of show: live customer walkthrough (45 minutes + Q&A)

Mode: Supervised. Rehearse against the real screen; write down each segment's actual length.

| Minutes | What the customer sees | On screen | Keyboard | What it proves |
|---|---|---|---|---|
| 0–5 | Healthy orders per minute, zero backlog, the isolated twin, mode set to Supervised | Control Center: **HEALTHY**, Baseline stage | Presenter | Recovery is measured in customer transactions; autonomy is set before the drill starts |
| 5–10 | The Ops Service removes the app's Key Vault access. Orders drop to zero, backlog grows, replicas still show Running | Control Center: Fault stage, **CUSTOMERS CAN'T ORDER** | Driver starts the drill | The fault is bounded, reversible, traced and confined to the twin |
| 10–17 | The Planner's MCP calls live, then broad and narrow recovery options with evidence | Control Center feed, then the Foundry trace of the Planner's tool calls | Driver switches tabs | Agents investigate through governed, read-only tools |
| 17–22 | The customer picks the broad option; Azure Policy denies it. The narrow option is approved; access is restored | Control Center decision card | **The customer clicks** | Policy, not the model, decides what runs |
| 22–27 | Dependency health turns green, but the referee's journey fails and SQL shows no order | Control Center: **FALSE GREEN** split view | Presenter | Green infrastructure is not business recovery |
| 27–35 | The Planner writes the brief; the Engineer creates the branch, patch, test and runbook diff; CI runs | Feed of Engineer calls, then the GitHub branch and CI run | Driver switches tabs | The drill becomes a reviewable engineering fix |
| 35–40 | The candidate revision deploys, the same journey passes, the backlog drains | Control Center: Verify stage, **VERIFIED** | Presenter | Evidence, not the agent, declares recovery |
| 40–45 | Scorecard, evidence package, the unmerged pull request, residual risks | Scorecard tiles, evidence package, unmerged PR | Presenter | One drill produces both proof and a lasting improvement |

## Roles (fix these at the first rehearsal)

| Role | Person | Job |
|---|---|---|
| Presenter | _name_ | Talks, owns the timing |
| Driver | _name_ | Runs the drill, switches tabs |
| Backup | _name_ | Watches the lab clock, keeps the replay ready |
| Customer | (guest) | Makes the decision on the decision card |

## Fallback ladder

Try each step before the next. **A replay is always announced as one.**

1. Live drill as planned.
2. A stage stalls for more than 2 minutes: the presenter covers from the notes while the driver keeps going.
3. The lab dies or a stage fails: switch the same screen to
   `/drills/<drill-rehearsal-id>?mode=replay&speed=5` and say *"This is the run we recorded on <date>."*
4. Last resort: the screen recording of a full live run.

## Before every session

- [ ] Lab launched at least 90 minutes ahead; remaining lab time covers the session
- [ ] Bootstrap, then the T-00 baseline
- [ ] One full T-01 rehearsal, then reset
- [ ] Model quota checked; `candidate/` branch namespace empty
- [ ] Tabs open: Control Center, Foundry traces, App Insights, GitHub
- [ ] Control Center open on the projector at 125–150% zoom; feed readable from the back
- [ ] Replay fixture and screen recording ready, both labelled as fallbacks
- [ ] New passcode generated and shared with the customer (or guests assigned in Entra); IP ranges set if used
- [ ] After the session: rotate the passcode

## Rehearsal log

| Date | Total | Longest segment | Fix before next run |
|---|---|---|---|
| | | | |
| | | | |

## Recorded submission (3 minutes)

Keep the plan's 3-minute script. Film it from a replay at ×10 to ×20 so the pacing is under your
control: `/drills/<best-rehearsal>?mode=replay&speed=15`.
