# RCPia Web

Next.js (App Router) frontend for RCPia v1. Node dependencies are managed with
[pnpm](https://pnpm.io/).

```bash
pnpm install     # install deps
pnpm dev         # dev server on :3000
pnpm test        # vitest smoke tests
pnpm typecheck   # tsc --noEmit
pnpm lint        # next lint
pnpm build       # production build (standalone output)
```

After any `pnpm install` that adds or updates dependencies behind a mirrored registry,
run `pnpm lock:normalize` and commit the lockfile. It strips absolute tarball URLs that
would otherwise pin `pnpm-lock.yaml` to one registry mirror and break
`--frozen-lockfile` in CI.

`pnpm build` produces a normal build. Set `NEXT_OUTPUT=standalone` for the container
build — Windows hosts need Developer Mode for the symlinks that standalone output
creates, so it is opt-in rather than default.

Environment:

- `NEXT_PUBLIC_API_BASE_URL` — browser-visible API URL (default relative `/api`).
- `API_INTERNAL_BASE_URL` — server-side API URL inside Docker (e.g. `http://api:8000`).

## Workstation

The dashboard and workstation rail share the same case list: grouped by deposit day,
newest first, with the last-updated timestamp displayed explicitly in UTC. Status dots
always have text labels; a terminal panel without usable synthesis is not a missing-field
warning.

The workstation follows `docs/design/poste-mock.html`, with light/dark semantic tokens
and locally bundled IBM Plex fonts (no font CDN). Blocking badges and the launch explanation
use the API's issue severities; the launch permission remains `can_run_analysis`.
The existing missing/uncertain counts still include fields marked "Sans objet".

Detailed progress and connection-retry feedback appear directly below the analysis
launch controls, after the ledger. Polling does not scroll or navigate the page.
The compact header keeps the overall status; a recorded current completion adds
"Voir la fiche RCP" beside the inline follow-up, never from an empty queue alone.
Progress remains indeterminate, showing only reported milestones and elapsed time.
Questions is a disabled, forthcoming tab; no question-answering backend is connected.

### Checking warning calibration

The `checking` warning uses a **provisional six-second observation budget**, not a claim
that citation checking takes six seconds.

On 6 September 2026, 17:52:07-17:53:59 UTC, one explicitly synthetic staging case
(`TASK4-SYNTHETIC-TIMING-20260906T175207285Z`) was reviewed against its fixture and one
panel was launched through the existing API. All three specialists and synthesis succeeded;
no failed runs or retries were observed. The synthetic case is retained for audit.

Sequential activity reads, 100ms after each response, enclosed checking in four monotonic
observation windows: **0.286-0.562s**, median **0.410s**. Each window started before the last
pre-checking observation and ended after recording or live-job disappearance confirmed by
terminal run metadata. These are conservative full-phase bounds, potentially including
reasoning tail, recording and network time, **not exact checking-to-recording durations**.
Only one checking milestone was caught directly; the public endpoint does not expose
attempt identity, so no exact same-attempt timestamp pairs were certified.

Across 516 activity reads, median RTT was 0.081s and maximum RTT 1.150s. The warning budget
is `ceil(0.562 + 2.5 + 1.150 + 1) = 6s`: the measured bound, the workstation's 2.5s
after-response polling interval, observed maximum RTT and a one-second display margin.
The other milestone thresholds are unchanged.

This is a one-panel calibration, not a latency SLA or a representative tail estimate.
Earlier current-revision logs showed an archive-only median of 2.848s and maximum of 3.148s
(four observations); the pooled maximum across revisions was 4.235s (42 observations).
Those partial intervals cannot be substituted for the whole checking phase. Revisit the budget with more full-phase
observations, including cold starts and larger cases, after deployment. Unknown timestamps
remain unknown, and elapsed time is neither capped nor converted into a progress percentage.

## Invitations

`/admin/invitations`, linked from the dashboard, lets an organization administrator
create invitations, copy their one-time activation links and revoke outstanding
invitations. Access is decided by `GET /auth/invitations`, not a frontend role check.
The API lists only the caller's organization's unexpired, pending invitations.

Activation links use the web origin and a URL fragment (`/activate#token=…`).
They are held only in the current page's memory, never in browser storage. Copy a
link before creating another invitation or leaving the page; lost links cannot be
retrieved. No invitation email is sent automatically.
