# Brief: bring RCPia's workstation to the handover bar

You are implementing a defined slice of work on RCPia. Read this whole document before writing
code. It is written for someone who has never seen this repository.

Companion document: **`docs/clinician-handover-plan.md`** — the plan this brief executes. Read
it second. Where the two disagree, the plan wins and you should say so.

---

## 1. What RCPia is, and why its rules are unusual

RCPia prepares **draft** RCP (multidisciplinary tumour board) fiches for prostate and bladder
cancer cases. A clinician uploads a dossier; the system extracts structured facts from it; the
clinician **corrects** those facts; then three specialist agents and a synthesis agent produce a
provisional fiche which a human board reviews.

It never decides. Every output is provisional. That is not marketing language — it is the
constraint the whole architecture serves, and several features exist only to keep the promise:

- **The correction gate.** The specialists read *the version the clinician approved*, not the
  machine's first reading. A panel that convenes before the human has looked breaks the
  product's central claim. This was found broken in production config on 6 Sep and fixed.
- **Citation attestation.** An agent that cites evidence it was not given **fails closed**.
  Observed on staging: an agent cited `briganti_2012`, the gate rejected it, three attempts,
  the job failed rather than shipping an ungrounded claim. That is correct behaviour. Do not
  "fix" it.
- **Honest progress.** The UI must never claim to know something it does not. Where the backend
  cannot report a state, the interface says so ("non suivi", "état inconnu") instead of
  guessing. A previous reviewer rejected a progress bar for inventing progress, and the
  percentages were removed. See §7.

Treat "the interface must not overstate what the system knows" as a correctness requirement,
not a stylistic preference. It is the thing reviewers reject work over.

## 2. Hard rules — do not violate these

**Stop and report to the repository owner before changing any of:**

- clinical prompts or agent brains (`apps/api/rcpia/agents/`, `docs/implementation/*brain*.md`)
- retrieval or reranking policy
- bundle size or content policy
- **safety or completeness rules** — including *which* fields block an analysis
- the generated fiche structure
- model choice
- parser behaviour beyond selecting or configuring LlamaParse

Presentation changes are yours to make. **Rules are not.** Concretely: you may change how a
blocking field is displayed; you may not change which fields block, nor the API's completeness
arithmetic. The gate promises the specialists read what is on screen, so the screen must not
disagree with the API.

**Patient data.** Real dossiers live outside the repo at
`C:\Users\hzmarrou\OneDrive\python\learning\pi\rcpia\docs\dossiers`. They are anonymised, but
dossier text must never enter the repository, a commit, a test fixture, a screenshot committed
to the repo, or any published page. Agent runtime output can echo the case bundle, so never log
raw agent stdout/stderr into a file that gets committed.

**No hardening right now.** There is a standing decision: this is a feedback-gathering phase,
and anything that makes usage harder loses to feedback volume. Do not add rate limits, stricter
auth, IP restrictions, or extra confirmation steps unless explicitly asked.

**Secrets.** `~/.rcpia-deploy/stg.secrets` holds staging credentials. Read from it; never print
its values into a transcript, a log, a commit, or a PR body.

## 3. Orientation

Monorepo. Two apps.

```
apps/api/rcpia/        FastAPI + SQLAlchemy async (psycopg3), Python 3.13
  core/models.py       ORM models, CaseStatus enum
  core/config.py       Settings (env-driven)
  cases/               case creation, uploads, fact_schema.py, completeness.py
  parsing/             LlamaParse + chunking
  agents/              panel.py, routes.py, schemas.py  <-- clinical, see §2
  worker.py            job loop; claims jobs, writes milestones
  ops/smoke.py         the deployment self-check
apps/web/              Next.js 15 App Router, React 19, TypeScript
  app/                 pages; poste/[caseId]/Workstation.tsx is the main surface
  app/api/[...path]/   same-origin proxy to the API
  lib/                 api.ts, cases.ts, ledger.ts, activity.ts, pps.ts, auth.ts
  tests/               vitest, 83 passing
infra/docker-compose.yml
docs/                  plans, runbooks, brains
```

### Running it

From the **repository root** — this matters, see §9:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --scale rcpia-worker=3
```

Then: web `http://localhost:3000`, API `http://localhost:8000`, Postgres on **5433**.

Local login: `hzmarrou@rcpia.online` / `correct-horse-battery-staple` (role `uploader`).

Checks you must keep green:

```bash
cd apps/web && pnpm exec tsc --noEmit && pnpm exec vitest run && pnpm exec next lint
docker compose --env-file .env -f infra/docker-compose.yml exec -T rcpia-worker \
  python -m rcpia.ops.smoke --api http://api:8000     # expect 11/11
```

## 4. What is already implemented

Verified by driving the running app on 6 Sep 2026, not by reading code.

### API — every route that exists

```
POST   /auth/activate                         POST   /cases
GET    /auth/dashboard                        GET    /cases
POST   /auth/invitations                      GET    /cases/{id}
POST   /auth/invitations/lookup               GET    /cases/{id}/activity
DELETE /auth/invitations/{invitation_id}      POST   /cases/{id}/analysis
POST   /auth/login                            GET    /cases/{id}/analysis
POST   /auth/logout                           POST   /cases/{id}/analysis/panel
GET    /auth/me                               POST   /cases/{id}/documents
GET    /health                                GET    /cases/{id}/documents/{doc_id}/content
                                              GET    /cases/{id}/ledger
                                              POST   /cases/{id}/ledger/corrections
                                              GET    /cases/{id}/pps
                                              GET    /cases/{id}/pps/markdown
                                              GET    /cases/{id}/pps/docx
                                              GET    /cases/{id}/pps/docx/probatoire
```

**Every backend capability the handover needs already exists.** The invitation endpoints have
no UI; `GET /cases` has no dashboard. That is the shape of the remaining work.

### Web — every page that exists

```
/                     landing, with links in
/login  /activate  /request-access
/dashboard            A STUB. ends in a placeholder sentence
/cases/new            upload form: reference (required), localisation, question, file
/cases/[caseId]       older single-case view
/cases/[caseId]/ledger
/cases/[caseId]/pps
/poste/[caseId]       THE MAIN SURFACE — the three-pane workstation
```

### What works, end to end

| Capability | Detail |
| --- | --- |
| Accounts | invite-only; roles `admin` / `uploader` / `viewer`; middleware refuses unauthenticated pages |
| Upload | `.docx` and `.pdf`; `reference` is required and the browser silently refuses submit without it |
| Extraction | LlamaParse -> chunks -> agent -> ledger v1. 109 fields, 10 categories, grounded (14 passages on each of the last 5 runs), plus a second pass over the prose. ~55 s locally |
| Ledger | grouped by category, sex applicability rendered "sans objet", red/amber highlights, corrections recorded as a new version with author and reason |
| Correction gate | `can_run_analysis` false while a blocking gap exists; launch button disabled. Verified: a clean prostate dossier yields ~32 red rows of which exactly **1** blocks (`gleason`) and 1 is required-not-blocking (`isup`) |
| Panel | 3 specialists + `rcp_synthesis`, each grounded, convened only by explicit user action |
| Citation attestation | an agent citing evidence it was not given fails closed. Observed on staging: `briganti_2012`, rejected, 3 attempts, job failed |
| Progress | real milestones `bundle` -> `reasoning` -> `checking` -> `recording`, with elapsed time; honest "unknown" where the backend cannot say |
| Fiche | view page, Word export, separate evidence register (*probatoire*) |
| Deployment | Azure Container Apps, custom domain `stg.rcpia.online`, smoke 11/11 |
| Tests | 83 vitest tests in `apps/web/tests/`, all passing |

### Known gaps, not defects

The case status never advances past `ready_for_review` (Task 1). The dashboard has no case list
(Task 2). Invitations have no UI (Task 3). Three ledger fields are defined twice (Task 4a). The
`checking` overrun threshold is miscalibrated (Task 4b).

### Two defects fixed on 6 Sep — context for why some rules exist

1. The staging client bundle had `http://localhost:8000` compiled in, so **every** browser-side
   call was refused while server-rendered pages looked healthy. `NEXT_PUBLIC_*` is inlined at
   build time.
2. `AUTO_PANEL_ROLES` was set on staging, so the panel convened the instant extraction finished
   and the correction phase was skipped. Now empty everywhere.

### Branching

**PR #8 is open and must merge before you start Task 2.** It splits the browser API base URL
(relative `/api`) from the server one (absolute), and it touches `lib/api.ts`, six page
components and the web tests — the same files Task 2 restyles. Starting Task 2 first guarantees
a conflict.

Then: **one branch and one PR per task**, branched from an up-to-date `main`. Never commit to
`main`. Do not reintroduce an absolute browser-side URL.

Two defects fixed on 6 Sep, for context:

1. The staging client bundle had `http://localhost:8000` compiled in, so **every** browser-side
   call was refused while server-rendered pages looked healthy. `NEXT_PUBLIC_*` is inlined at
   build time.
2. `AUTO_PANEL_ROLES` was set on staging, so the panel convened the instant extraction finished
   and skipped the correction phase. Now empty everywhere. `convene_after_extraction` returns
   `None` on empty roles.

## 5. The work

**Read this before anything else in this section.**

This is a working, tested, deployed application. **You are modifying it in place. You are not
building a new one.** Nothing here asks for a new app, a new frontend, a parallel page, a
rewrite of the workstation, or a fresh component library. If you find yourself creating a
second version of a screen that already exists, stop — you have misread the brief.

In particular, `apps/web/app/poste/[caseId]/Workstation.tsx` is 771 lines that already
implement most of the target design's *structure*. What is missing is mostly its *appearance*,
plus a few small additions. Each task below therefore states **what already exists** before it
states **what changes**. Respect that split: extend the existing components, keep their props
and tests working, and keep the diff proportionate to the change.

Do these in order. Each lands as its own PR.

### Task 1 — Make case status mean something (backend)

`CaseStatus` in `apps/api/rcpia/core/models.py:76` declares `draft`, `parsing`,
`ready_for_review`, `analyzing`, `completed`, `blocked`. Only the first three are ever assigned
(`cases/service.py:60,230`, `parsing/service.py:215`). `analyzing` and `completed` appear
nowhere outside the enum.

Consequences today: the workstation cannot distinguish a finished case from an untouched one
and honestly draws "non suivi"; and any case list would show every case as `ready_for_review`
for ever.

Required:

- set `analyzing` when a panel is convened
- set `completed` when the panel's work is finished, synthesis included
- decide what `blocked` means and document it, or remove it from the enum
- likely files: `agents/panel.py`, `worker.py`, `core/models.py`, plus an alembic migration only
  if you change the enum itself

Done when: a real local panel moves a case `ready_for_review -> analyzing -> completed`, and
`apps/web/lib/pipeline.ts` stops needing its `unknown` state for the post-panel stages. Add API
tests covering both transitions.

**This task gates everything else.** The design in Task 2 is built around status.

### Task 2 — The workstation restyle, including the case list

**The design you are building: `docs/design/poste-mock.html`.** Open it in a browser — it is a
working page, not a picture. Click *Lancer l'analyse* to watch the progress behaviour play out
(time-compressed 12x).

It is committed to this repository on purpose. The original lives as a Claude artifact at
<https://claude.ai/code/artifact/7c9127dc-cf4c-4b55-95af-52d69e8d80a3>, which serves viewers a
*pinned* version that may be older than the one captured here, and which your tooling may not be
able to fetch at all. **The file in the repository is the source of truth.**

All values in it are fictional; it carries its own "Maquette" banner saying so. It has
implementer notes added inline at the two places that are not settled — the progress bar and the
Questions tab — and at the resizing code, which the shipped workstation already implements.

The mock is **not only a restyle**. Its left rail is the case list, and its completeness card
carries a `Bloquantes` count. Both are blockers in the plan; building them here avoids doing
them twice.

#### Design tokens

The governing rule, from the mock's own comment: *red and amber already carry meaning in this
product — missing, and uncertain — so the accent cannot be either.* A deep petrol keeps the
semantic colours the only warm things on the page.

| Role | Light | Dark |
| --- | --- | --- |
| accent | `#0d5b61` | `#4fb3ba` |
| accent ink / soft | `#ffffff` / `#e2eeee` | `#08262a` / `#153338` |
| missing | `#a8291f` on `#fbeceb` | `#f0857a` on `#331c1a` |
| uncertain | `#8a5a00` on `#fdf3e0` | `#e0ad55` on `#2f2617` |
| ok | `#2b6349` on `#e7f1ec` | `#6fc39c` on `#14291f` |
| paper / surface | `#f4f4f1` / `#ffffff` | `#101315` / `#171b1e` |
| ink / muted | `#16191c` / `#6d7379` | `#e8eaea` / `#8b9297` |
| line / line-2 | `#dcdedb` / `#c9ccc8` | `#2a3034` / `#3a4247` |

Typography: **IBM Plex Sans** for UI, **IBM Plex Mono** for values, references and clocks with
`font-variant-numeric: tabular-nums`, **IBM Plex Serif** for the fiche so it reads as a document
rather than as more UI. Define both themes at token level, light on bare `:root`.

#### What already exists — do not rebuild any of this

`apps/web/app/poste/[caseId]/Workstation.tsx` (771 lines) already implements, and it works:

| Piece | Where |
| --- | --- |
| Three-pane shell with two draggable splitters, keyboard operable, widths persisted to `localStorage` | `Workstation.tsx:32-45`, `:688-745` |
| Left case rail listing every case | `.poste-rail`, `:236-260` |
| Pipeline as a stage list | `.poste-pipeline`, `:269`; logic in `lib/pipeline.ts` |
| Ledger table grouped by category, with "sans objet" rows | `.poste-group`, `:368`; logic in `lib/ledgerView.ts` |
| Right pane tabs — Correction and Fiche | `role="tab"`, `:486-492` |
| Agent progress from real milestones | `.poste-agents`, `:633`; logic in `lib/activity.ts` |
| Gate box, launch button, launch/error states | around `:450` |

So the target design's **structure is largely built**. Treat this task as a restyle plus four
small additions — not as building the mock.

#### What actually changes

1. **Appearance.** Replace the palette and typography in `apps/web/app/globals.css` with the
   tokens above. This is the bulk of the task and it is mostly CSS. Keep the existing class
   names where you can; renaming `.poste-*` classes churns the diff for nothing.
2. **Status dots in the rail.** Each row gains a dot driven by Task 1's status — red
   `à vérifier`, amber `analyse en cours`, green `fiche prête`. Small change to an existing
   list; no new component.
3. **Completeness card** (new, small). Above the ledger: *lues / manquantes / incertaines /
   **bloquantes***. The counts come from the API's `issues` array; `lib/ledgerView.ts` already
   has `tally()` — extend it rather than writing a second counter.
4. **Blocking clarity.** The API's `issues` carry `severity` of `blocking` / `required` /
   `uncertain` and the UI ignores it. Surface it: name the blocking field next to the disabled
   launch button, and distinguish the three severities visually.
5. **A third tab, "Questions"** — present but disabled, see §7.2.

> Presentation only. Do not change which fields block, and do not recompute completeness in the
> frontend. See §2.

#### The one genuinely missing surface: the dashboard

The case rail exists, but **only inside `/poste/[caseId]`** — you need a case id to reach the
list of cases. `apps/web/app/dashboard/page.tsx` is a stub ending in the placeholder *"La
génération de PPS provisoires sera disponible ici."* So a clinician who uploads today cannot
find that case tomorrow by any route in the UI.

Give the dashboard the same case list, in the new visual language, each row linking to
`/poste/<id>`. `GET /cases` already returns `id`, `reference`, `cancer_type`, `status`,
`created_at`, `updated_at`, and `lib/cases.ts` already has `fetchCases`. **No API work is
needed.** Reuse the rail's row rendering rather than writing a second one.

#### The blocking-clarity requirement

On a clean prostate dossier the ledger returns ~32 red rows of which exactly **one** blocks
(`gleason`) and one is required-but-not-blocking (`isup`). A clinician currently sees 32 red
rows and a disabled button with no way to tell what to do.

The API's `issues` array already carries `severity` of `blocking` / `required` / `uncertain`;
the UI ignores it. Surface it: name the blocking field next to the disabled launch button, and
distinguish the three severities visually.

> Presentation only. Do not change which fields block, and do not recompute completeness in the
> frontend. See §2.

Also fold in the dashboard: `apps/web/app/dashboard/page.tsx` is currently a stub ending in the
placeholder *"La génération de PPS provisoires sera disponible ici."*

### Task 3 — Invitation UI

`POST /auth/invitations`, `POST /auth/invitations/lookup` and `DELETE
/auth/invitations/{id}` exist and are tested. There is no page for any of them, so an admin
cannot invite anyone from a browser.

Build an admin-only page to create an invitation and show the resulting link, plus a list of
outstanding invitations with revoke. `/activate` already exists and works — this only feeds it.
Let the API decide authorization; do not re-implement the role check in the page (follow the
pattern in `app/dashboard/page.tsx`, which asks the API).

### Task 4 — Two tidy-ups

**4a. Duplicate ledger fields.** `platelets`, `bicarbonate` and `phosphorus` are each defined
**twice** in `apps/api/rcpia/cases/fact_schema.py` — lines 217, 229, 231 and again 436-438. The
ledger returns 106 entries for 103 distinct fields. The later definitions are better (they carry
units); remove the earlier ones after merging any alias worth keeping.

While there, **report but do not silently change**: `FactField("phosphorus", ("ph",))` gives
phosphorus the alias `ph`, which will match blood **pH**. That is an extraction-correctness
question needing a clinical answer, and it falls under §2.

**4b. Overrun threshold.** `TYPICAL_SECONDS.checking = 0.2` in `apps/web/lib/activity.ts` was
measured on a laptop. On staging the step takes ~2 s, so "plus long que d'habitude" fires on
every single run. A warning that always fires trains people to ignore all warnings. Re-measure
and set it from real data; say in a comment where the number came from.

## 6. How this repository expects you to work

- Branch per task; open a PR per task. Never commit to `main`.
- Keep `tsc --noEmit`, `vitest run`, `next lint` and the 11/11 smoke green. Do not weaken a test
  to make it pass — if an assertion encodes a bug, fix it and say so in the message.
- **Write tests that ask what the code does, not what its source says.** A recent bug shipped
  because a test read a constant instead of checking the URL actually requested.
- Comments explain *why*, especially when the reason is a defect someone already hit. Match the
  surrounding style — this codebase writes prose comments, not labels.
- French is the user-facing language. Keep the existing register.
- Commit messages: end with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- `gh pr edit` **fails on this repo** (Projects-classic deprecation error). Use
  `gh api repos/hzmarrou/RCPia/pulls/N -X PATCH -F body=@file`.

## 7. Two decisions that are NOT yours

Ask the repository owner. Do not pick one and proceed.

**7.1 — Is the progress bar allowed to be determinate?**

The mock fills a bar and creeps between milestones (`shown = at + span * 0.82 * within`). The
PR #6 reviewer rejected exactly this and the percentages were removed: a clock-interpolated bar
invents progress the system does not have. The mock argues back in its caption — it never
claims past the last milestone reached, and the creep is capped short of the next one.

Both are honest positions that disagree about whether capped creep is a claim. Options: keep
today's indeterminate indicator inside the new visual language; adopt capped creep; or fill
milestone segments discretely with no creep. **Get a ruling before building the progress
section.**

For context, the measured stage shares are real and are what make an equal-quarters bar
dishonest: `bundle` ~9%, `reasoning` ~89%, `checking` and `recording` well under 1%.

**7.2 — The "Questions" tab is a new clinical surface, not styling.**

The mock's third tab is a scoped assistant: answers from this case, the fiche and cited
guideline passages; cites its sources; and refuses to give a clinical opinion — *"Hors périmètre
— je ne me prononce pas sur la décision : elle appartient à la RCP."*

That is a new answering agent. It needs a backend endpoint, retrieval scoped to one case, a
refusal boundary, and citation attestation like every other agent. It touches clinical prompts
and safety behaviour, so under §2 it needs clinical sign-off and must not be built as a UI-only
feature.

**Default unless told otherwise: build the tab visibly present but disabled**, labelled as
coming, rather than holding the whole restyle behind the largest item in the plan.

## 8. Definition of done

A clinician, unaided, can: receive an invitation and activate an account; log in; upload a
dossier; **come back tomorrow and find it**; read what was extracted; see plainly *which* field
is blocking and correct it; launch the panel; watch honest progress; read the fiche and export
the Word file; and **tell at a glance which of their cases are finished**.

Verify by doing it, not by reading code. Every defect found on 6 Sep was found by driving the
real UI; none by reading. Drive it locally first — the standing instruction is that the app must
be right locally before anything is tested in the cloud.

## 9. Gotchas that will cost you an hour each

- **Compose must run from the repository root** with `--env-file .env`. Run from inside
  `infra/` it silently misses `POSTGRES_PORT=5433` and collides with another Postgres on 5432.
- **`az acr build` fails here.** `RUN --mount=type=cache` needs BuildKit, which classic ACR
  Tasks does not enable. Build locally with `DOCKER_BUILDKIT=1` and `docker push`.
- **npm and PyPI are blocked machine-wide** by a Defender policy, inside containers too. Pass
  `--build-arg NPM_CONFIG_REGISTRY=https://packagefeedproxy.microsoft.io/npm/`.
- **`NEXT_PUBLIC_*` is inlined at build time**, not read at runtime. This caused the 6 Sep
  outage. Browser code uses the relative `/api`; only server code gets an absolute origin.
- **Windows console is cp1252.** Any script printing French or `✓` needs
  `PYTHONIOENCODING=utf-8` or it dies on a `UnicodeEncodeError`.
- **vitest needs `esbuild: { jsx: "automatic" }`** (already in `vitest.config.ts`); without it
  every component test fails with "React is not defined".
- The staging database is **stopped nightly after 23:59** by policy. A stopped resource is
  routine, not an incident: `az postgres flexible-server start -g rg-rcpia -n
  psql-rcpia-stg-h7qbil`.

## 10. First thing to do

1. Confirm **PR #8 has merged**. If it has not, stop and say so — Task 2 conflicts with it.
2. Bring the stack up locally, log in, upload a dossier from the dossiers folder, and walk the
   whole loop yourself, so you have seen the thing you are changing.
3. **Create a branch before you write any code.** One branch per task, cut from an up-to-date
   `main`, one PR each. Never commit to `main`. Suggested names:
   - Task 1 -> `feat-case-status-lifecycle`
   - Task 2 -> `feat-workstation-design`
   - Task 3 -> `feat-invitation-ui`
   - Task 4 -> `fix-ledger-duplicates-and-threshold`
4. Start with Task 1. It gates the rest.
5. Report what you find that this brief got wrong. It was written from a running system on
   6 Sep 2026 and the code will have moved.
