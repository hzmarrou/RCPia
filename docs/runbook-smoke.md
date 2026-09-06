# Runbook: rehearse the clinician workflow

Run this before a clinician handover and after a change to the deployed workflow.
Deployment and recovery are in [the release runbook](runbook-deploy.md).
Use the actual release images, a separate clinician account and an entirely synthetic dossier.

The automated smoke command makes no model calls. The upload-to-fiche rehearsal does:
uploading invokes the parser and extraction model; one manual panel invokes the three
specialists and synthesis, with possible retries. Obtain explicit approval for that
usage and agree the number of dossiers/panels before starting. Do not keep resubmitting
when a run fails.

## 1. Local preparation

Use localhost for visual review. Confirm whether the preview uses the real backend or
synthetic fixture responses, and whether workers are running. An isolated UI preview
without workers cannot prove parsing, agent progress or export.

For the ordinary Docker stack, use PowerShell from the repository root:

```powershell
docker compose --env-file .env -f infra\docker-compose.yml up -d --build --scale rcpia-worker=3
docker compose --env-file .env -f infra\docker-compose.yml ps
```

This replaces the local stack, so coordinate with anyone using it. `.env` must specify the
approved package feeds, the available `POSTGRES_PORT` and empty `AUTO_PANEL_ROLES`.
Expect three running worker containers. Do not raise concurrency without provider capacity
approval. Inspect errors privately; logs can contain clinical material or service details.

## 2. Automated operational smoke

```powershell
docker compose --env-file .env -f infra\docker-compose.yml exec -T rcpia-worker `
    python -m rcpia.ops.smoke --api http://api:8000
```

On staging, select the newly deployed worker revision and run:

```powershell
az containerapp exec -g rg-rcpia -n ca-rcpia-stg-worker `
    --command 'python -m rcpia.ops.smoke --skip-api'
```

Read the command's own pass/fail output. The staging flag skips only the route-inventory
probe: `/openapi.json` is intentionally disabled outside local development. Do not weaken
that configuration to get a green result.

The checks cover current routes locally, the pinned Pi runtime, brain loading, credential
placement, process-local Pi configuration, nested timeout budgets, calculator access,
unrecoverable jobs, recent grounding and the narrative extraction pass. The count is
determined by the tool; a fixed historical count is not the release gate.

Smoke inspects recent records, which may predate the release. A fresh fixture database can
report no previous runs/extractions; record that as unverified, not a pass. Resolve other
failures first, perform the authorized rehearsal below, then repeat smoke and inspect that
rehearsal's actual runs. Never suppress an assertion to make the handover appear ready.

## 3. Rehearsal fixture and account

Prepare one fictional prostate or bladder dossier with an expected field-value sheet.
Have a clinical reviewer approve the fixture if clinical interpretation is required.
Include a known blocking omission that the operator can later correct from that sheet;
never invent a patient value simply to clear the gate.

Use a unique reference such as `HANDOVER-SYNTHETIC-<UTC timestamp>`. Do not reuse an old
case to claim a new full rehearsal. Keep the source, expected readings and resulting
exports in the approved restricted workspace, not Git.

Create a separate `uploader` invitation through `/admin/invitations` and deliver its link
privately. An administrator must do this manually; the app does not send email. Use an
isolated browser context for the recipient, so an existing admin session cannot make
authorization failures invisible. Never export browser cookies or storage state.

## 4. Walk the complete loop

| Step | Action and acceptance |
| --- | --- |
| 1. Activate | Open the invitation link, activate with a personal password and reach the dashboard. Do not reuse administrator credentials. |
| 2. Sign in | Sign out and sign back in. Refreshing an authenticated page should retain access; a signed-out session should not see case data. |
| 3. Upload | Create the synthetic case and upload the dossier. Confirm upload acceptance, parsing and extraction settle, and the ledger is readable. No specialists may launch automatically. |
| 4. Find it again | Sign out, close the session, return and find the same reference in the dashboard. Repeat on the following day before pilot handover; an immediate relogin is only the preliminary persistence check. |
| 5. Review extraction | Compare values and source references with the expected sheet. Missing fields are red, uncertainty is amber, and each field has one ledger row. Check both themes and usable split panes locally. |
| 6. Correct | Confirm the known blocking omission disables launch and appears in the blocking list. Follow its correction link, enter the fixture value and a reason, then confirm a new ledger version. Launch becomes available only when the API gate clears. |
| 7. Launch once | Start one panel. Observe actual milestones and elapsed time, without percentages or invented progress. Confirm all requested specialist outcomes and subsequent synthesis; do not equate an empty activity list with success. |
| 8. Read and export | Read the provisional fiche and its evidence references. Download and open the Word export, checking the case reference, content and provisional status. A successful download alone is not enough. |
| 9. Recognize completion | Return to the dashboard and confirm the current-ledger case is completed only after a usable, citation-attested synthesis. A terminal panel without usable synthesis must be visibly blocked. |
| 10. Report feedback | Submit one synthetic issue through the chosen private feedback channel and confirm the designated triage person can receive it. An undecided channel means this step is pending. |

For invitations, also confirm an `uploader` cannot administer invitations and that revoking
an unused synthetic invitation prevents activation. Do not revoke a real clinician's link
for a rehearsal. Do not create extra model runs to exercise these account checks.

For a live job, keep the last-known state during a connection interruption and show the
communication problem rather than declaring completion. If the short job window prevents
observing this safely, record the limitation and cover the interaction with the existing
local fixtures; do not secretly submit another panel.

Confirm browser API calls stay on the web origin under `/api`. Neither browser network
traffic nor redirects should target the internal Azure API hostname or `localhost:8000`
from a staging page. Inspect only the synthetic session and do not save an unredacted HAR.

There is no fixed three-minute promise. Parser latency, cold starts, provider load and
retries vary. The checking-stage warning is provisional, not an analysis timeout. On a
citation failure, missing output or incorrect status, retain the reference and sanitized
failure metadata, stop the affected flow and investigate; do not disable citation checks
or relax the completeness rules.

## 5. Record the outcome and clean up deliberately

Record the release SHA, image digests/revisions, synthetic reference, UTC times, terminal
run outcomes, passed/pending/failed steps and a link to the private feedback item.
Leave the next-day return explicitly pending until it has actually happened. Do not
describe a localhost preview or a previous staging panel as this full rehearsal.

Staging rehearsal cases are retained for audit. Do not run local reset utilities on staging
or delete append-only records to hide a failed attempt. For a local fixture, stop its
services and remove only the disposable database/blobs that fixture owns. The ordinary
local stack and other users' files are not disposable fixtures.

If an operator needs the existing local reset utility, inspect its documented scope first.
It refuses outside local deployment and while work is live, but its demo-prefix selection
can cover more cases than this rehearsal. It is not automatic end-of-run cleanup.

This rehearsal establishes that a clinician can use the workflow and report a defect.
It does not certify medical correctness: every fiche remains provisional and requires
human clinical review.
