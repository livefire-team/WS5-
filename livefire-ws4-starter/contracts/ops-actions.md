# Ops Service actions, v1.0 (WS2 implements; WS4's runner calls)

`POST /actions/<action>` on the Ops Service's internal-only ingress, with `Authorization: Bearer <ops-runner-key>`
(control Key Vault secret `ops-runner-key`) and a JSON body that always includes `drill_id`.
Reply: `{"action", "result", "details"}`. Status 401 for a wrong key, 409 while another action runs for the drill,
422 when refused (a target outside the twin, a bad image). Every reply within 210 s: Container Apps ingress ends a
request at 240 s. Every action is safe to repeat. The Ops Service resolves every target itself; callers never pass one.

| Action | Body besides drill_id | Results | Event the Ops Service writes |
| --- | --- | --- | --- |
| inject_fault | none | ok, refused | fault.inject |
| restore_access | none | ok | fault.restore |
| attempt_broad_recovery | none | denied, UNEXPECTED_ALLOW | recover.broad; if allowed, remove the assignment at once |
| wait_for | state (ok or forbidden), timeout_s <= 180, since (ISO time the wait began) | ok, timeout | wait.ok or wait.forbidden on ok only, seconds counted from since |
| run_referee | label | PASS, FAIL; details = the referee result | referee.<label> |
| deploy_revision | image matching ghcr.io/<owner>/order-service:sha-<40 hex> | ok, refused | deploy.revision {image, revision}, once ready |
| reset_twin | none | ok | reset: access restored, base image back, restarted, ready |
| kill | none | ok | reset {by: kill} |
