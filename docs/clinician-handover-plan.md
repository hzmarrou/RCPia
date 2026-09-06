# Handing RCPia to a clinician

Written 6 Sep 2026. The question this answers: **what has to be true before a clinician can
use RCPia without you sitting next to them?**

Scope decided with Hamid on 6 Sep:

- **Ship on the V1 agent brains.** The V2 brains wait. The feedback wanted first is about the
  workflow and whether the fiche is useful, not final clinical quality.
- **Build the invitation UI.** Accounts must not require you to run an API call per person.
- **A handful of clinicians, 2-3 weeks.** Fix the blockers and the friction.
- **Adopt the mock's visual identity** (revised later on 6 Sep — this was originally out of
  scope). Petrol accent, red and amber kept for meaning only, IBM Plex. See Stage D.

---

## 1. What exists today

`main` at `6ce61ac`, plus PR #8 open. Staging on `stg.rcpia.online`. 83 web tests, smoke 11/11
locally and on staging.

### The chain works end to end

| Stage | State |
| --- | --- |
| Account | invite-only; activate, login, logout; roles `admin` / `uploader` / `viewer`; session middleware refuses unauthenticated pages |
| Case creation | reference + localisation (prostate/vessie) + question + `.docx` or `.pdf` |
| Extraction | LlamaParse -> chunks -> extraction agent -> ledger v1. 109 fields in 10 categories, grounded (14 passages on each of the last 5 runs), with a second pass over the prose |
| Ledger | grouped table, sex applicability rendered "sans objet", red/amber highlights, corrections recorded as a new version |
| Gate | `can_run_analysis` is false while a blocking gap exists; the launch button is disabled until it clears |
| Panel | 3 specialists (urology, medical oncology, radiation oncology) + `rcp_synthesis`, each grounded |
| Citation check | an agent that cites evidence it was not given **fails closed**. Observed on staging: `briganti_2012` cited, rejected, 3 attempts, job failed rather than shipping an ungrounded claim |
| Progress | real job milestones (`bundle` / `reasoning` / `checking` / `recording`) with elapsed time, and honest "unknown" states where the backend cannot say |
| Fiche | view page, Word export, and a separate evidence register (*probatoire*) |

### Verified on 6 Sep

- **Local**: uploaded Dossier 15 as a real user through the browser. Case created, extraction
  finished in ~55 s, ledger rendered, the panel did **not** start on its own, launch button
  correctly disabled pending one blocking field.
- **Staging**: convened a real panel from the workstation. Full milestone chain recorded for
  the first time ever there; 4 jobs `completed`, `attempts=1`, zero failures.

### Two defects found and fixed today

1. **The browser was calling `http://localhost:8000`.** `NEXT_PUBLIC_*` is inlined by
   `next build`, and the image was built without it, so the runtime variable did nothing.
   Every browser-side call on staging was refused — login, upload, corrections, convening,
   polling, fiche links — while server-rendered pages looked healthy. Fixed in PR #8 by
   splitting the browser constant (relative `/api`) from the server one (absolute).
2. **`AUTO_PANEL_ROLES` was set on staging.** The specialists convened the instant extraction
   finished, so the correction phase was skipped and the panel read the machine's first draft.
   Cleared on both staging container apps. `convene_after_extraction` returns `None` on empty
   roles, so the gate now holds.

---

## 2. What "good enough to hand over" means

One sentence: **a clinician, with no help from you, gets from an account to a fiche and can
tell you what is wrong with it.**

The loop, in full:

1. Receives an invitation and activates an account
2. Logs in
3. Uploads a dossier
4. **Comes back tomorrow and finds that dossier again**
5. Reads what the machine extracted
6. Sees plainly *which* field is stopping the analysis, and corrects it
7. Launches the panel and watches honest progress
8. Reads the fiche, exports the Word file
9. **Can tell at a glance which of their cases are finished**
10. Tells you what is wrong

Steps 4, 6 and 9 do not work today. Step 1 requires you to run an API call by hand.

This bar deliberately excludes: production hardening, visual identity, V2 brains, and
multi-user collaboration. Per the standing decision, friction that reduces feedback volume is
not worth adding right now.

---

## 3. The path

Three stages. Stage A is the difference between "cannot use it" and "can use it"; do not start
B before A is done.

### Stage A — the blockers

**A1. A case list on the dashboard.** *The single highest-value item.*

Today `/dashboard` is a stub whose last line is a placeholder: *"La génération de PPS
provisoires sera disponible ici."* The case rail exists only **inside** `/poste/[caseId]`, so
you need a case id to see the list of cases. A clinician who uploads today cannot find it
tomorrow by any route in the UI.

`GET /cases` already returns `id`, `reference`, `cancer_type`, `status`, `created_at`,
`updated_at`. **No API work is needed.** This is a frontend task: render the list on
`/dashboard`, newest first, each row linking to `/poste/<id>`, showing reference, localisation,
status and when it last changed.

- Touch: `apps/web/app/dashboard/page.tsx`, `apps/web/lib/cases.ts` (`fetchCases` exists)
- Test: the page lists a case and links to its workstation; an empty account gets a first-run
  message rather than a blank panel

**A2. Make the case status mean something.**

`CaseStatus` declares `draft`, `parsing`, `ready_for_review`, `analyzing`, `completed`,
`blocked`. Only the first three are ever assigned — `analyzing` and `completed` appear nowhere
outside the enum. Consequences: the workstation rail cannot distinguish a finished case from an
untouched one and honestly draws "non suivi", and A1's list would show every case as
`ready_for_review` for ever, which makes the list nearly useless.

- Set `analyzing` when a panel is convened
- Set `completed` when the panel's work is done, including the synthesis
- Decide and write down what `blocked` means, or delete it from the enum
- Touch: `rcpia/agents/panel.py` (convening), the worker's job-completion path in
  `rcpia/worker.py`, `rcpia/core/models.py`
- Test: a case moves `ready_for_review -> analyzing -> completed` across a real panel, and the
  workstation rail stops saying "non suivi" once `completed` is reachable

**A3. Invitation UI.**

`POST /auth/invitations`, `/auth/invitations/lookup` and the `DELETE` all exist and are tested;
there is no page for any of them. An admin cannot invite anyone from a browser.

- An admin-only page to create an invitation and show the resulting link
- A list of outstanding invitations with a way to revoke
- `/activate` already exists and works — this only needs to feed it
- Touch: new `apps/web/app/invitations/page.tsx`, `apps/web/lib/auth.ts` (helpers exist)
- Test: a non-admin gets the same refusal the API gives; the API decides, the page does not
  re-implement authorization

### Stage B — the friction

**B1. Say which field is blocking.**

On a clean prostate dossier the ledger returned **32 red rows**, of which exactly **one**
(`gleason`) blocks the analysis and one (`isup`) is required but not blocking. The other 30 are
"not found", which is information, not a fault. A clinician sees 32 red rows and a disabled
button and cannot tell what to do.

The `issues` array already carries `severity` (`blocking` / `required` / `uncertain`) — the UI
just does not use it. Surface it: name the blocking field next to the disabled button, and
distinguish the three severities visually.

> **Constraint.** This changes presentation only. It must not change which fields block, and
> must not change the API's completeness arithmetic — the gate promises the specialists read
> what is on screen. Any change to the rule itself is a separate, clinically-reviewed decision.

- Touch: `apps/web/app/poste/[caseId]/Workstation.tsx`, `apps/web/lib/ledgerView.ts`,
  `apps/web/app/globals.css`

**B2. Remove the duplicated ledger fields.**

`platelets`, `bicarbonate` and `phosphorus` are each defined **twice** in
`rcpia/cases/fact_schema.py` — lines 217, 229, 231 and again 436-438. The ledger therefore
returns 106 entries for 103 distinct fields. The later definitions are the better ones (they
carry units); the earlier ones should go, after merging any alias worth keeping.

While there: `FactField("phosphorus", ("ph",))` gives phosphorus the alias `ph`, which will
match blood **pH**. That is an extraction-correctness question and needs a clinical answer, not
a tidy-up.

**B3. Recalibrate the `checking` threshold.**

`TYPICAL_SECONDS.checking = 0.2` was measured locally. On staging the step takes ~2 s, so the
honest "plus long que d'habitude" qualifier fires on **every single run**. The label is not
lying, but a warning that always fires trains people to ignore all warnings, including the ones
that matter.

- Re-measure on staging and set the threshold from that
- Touch: `apps/web/lib/activity.ts`

### Stage D — the visual identity

Source mock: `docs/design/poste-mock.html`, committed to this repository. Originally the
artifact <https://claude.ai/code/artifact/7c9127dc-cf4c-4b55-95af-52d69e8d80a3>, which serves
viewers a pinned version. Fictional values throughout; it carries its own "Maquette" banner.

**The mock is not only a restyle.** Its left rail *is* the case list (A1), and its completeness
card carries a `Bloquantes` count (B1). Building it delivers two blockers as a side effect,
which is an argument for doing it earlier rather than last.

#### Tokens to adopt

The governing idea, stated in the mock's own comment: *red and amber already carry meaning in
this product — missing, and uncertain — so the accent cannot be either.* A deep petrol keeps
the semantic colours the only warm things on the page.

| Role | Light | Dark |
| --- | --- | --- |
| accent (petrol) | `#0d5b61` | `#4fb3ba` |
| accent ink / soft | `#ffffff` / `#e2eeee` | `#08262a` / `#153338` |
| missing (red) | `#a8291f` on `#fbeceb` | `#f0857a` on `#331c1a` |
| uncertain (amber) | `#8a5a00` on `#fdf3e0` | `#e0ad55` on `#2f2617` |
| ok (green) | `#2b6349` on `#e7f1ec` | `#6fc39c` on `#14291f` |
| paper / surface | `#f4f4f1` / `#ffffff` | `#101315` / `#171b1e` |
| ink / muted | `#16191c` / `#6d7379` | `#e8eaea` / `#8b9297` |
| line | `#dcdedb` / `#c9ccc8` | `#2a3034` / `#3a4247` |

Typography: **IBM Plex Sans** for UI, **IBM Plex Mono** for values, references and clocks
(with `tabular-nums`), **IBM Plex Serif** for the fiche — so the fiche reads as a document
rather than as more UI. Full light and dark palettes, both defined at token level.

#### Structure

- Left rail: brand, "Déposer un dossier", case list grouped by day, each row a status dot
  (`review` red / `wait` amber / `done` green) + mono reference + localisation + time
- Centre: title with chips, pipeline as joined segments, completeness metrics card
  (lues / manquantes / incertaines / **bloquantes**), ledger table with `miss` and `unc` row
  tints, gate box with the promise text and the launch button
- Right pane: three tabs — **Correction**, **Fiche**, **Questions**
- Two draggable splitters with keyboard support, widths persisted

---

### Two conflicts to settle before building Stage D

**D-conflict 1 — the mock's progress bar is determinate.** It fills a bar and creeps between
milestones (`shown = at + span * 0.82 * within`). The PR #6 reviewer rejected exactly this and
made us remove percentages entirely: a clock-interpolated bar invents progress the system does
not know. The mock argues back in its caption — it never claims past the last milestone
reached, and the creep is capped short of the next one.

Both positions are honest; they disagree about whether capped creep is a claim. **This needs a
ruling.** Options: keep today's indeterminate indicator inside the mock's visual language;
adopt the capped-creep bar and put the reasoning back to the reviewer; or show milestone
segments that fill discretely with no creep at all.

Note the mock's own stage shares are measured and correct (`bundle` ~9%, `reasoning` ~89%),
and they are what make an equal-quarters bar dishonest.

**D-conflict 2 — the "Questions" tab is a new clinical surface, not styling.** A scoped
assistant that answers from this case, the fiche and cited guideline passages; cites its
sources; and refuses to give a clinical opinion ("Hors périmètre — je ne me prononce pas sur la
décision : elle appartient à la RCP").

This is a new answering agent. It needs a backend endpoint, retrieval scoped to one case, a
refusal boundary, and citation attestation like every other agent. Per the standing rule it
touches clinical prompts and safety behaviour, so **it needs the same clinical sign-off as the
agent brains** and must not be built as a UI-only feature. It is comfortably the largest item
in this document and should be costed separately from the restyle.

Recommendation: build Stage D's look, rail, metrics and tabs, with **Questions present but
disabled** and labelled as coming, rather than delaying the whole restyle behind it.

### Stage C — before you actually hand it over

**C1. Write `docs/runbook-deploy.md`.** There is no deployment runbook in the repo. Two facts
learned the hard way today exist nowhere but a chat transcript:

- `az acr build` **fails** on this repo — `RUN --mount=type=cache` needs BuildKit, which classic
  ACR Tasks does not enable. Build locally with `DOCKER_BUILDKIT=1` and `docker push`.
- The build needs `--build-arg NPM_CONFIG_REGISTRY=https://packagefeedproxy.microsoft.io/npm/`;
  the Defender block on `registry.npmjs.org` applies inside containers too.
- Compose must be run from the repo root: `docker compose --env-file .env -f
  infra/docker-compose.yml ...`. Run from inside `infra/` it silently misses `POSTGRES_PORT`.

**C2. Real accounts on staging.** The only two accounts are `rcpia-stg-admin@example.org` and
`rcpia-stg-admin@chu-marrakech.test`. Create genuine per-clinician accounts so feedback is
attributable and one person's corrections are not another's.

**C3. A rehearsal.** Walk the full ten-step loop on staging yourself, as a clinician would, from
invitation email to Word export. Every defect found this session was found by doing exactly
this, and none of them by reading code.

**C4. A way to receive feedback.** Decide where it goes — a shared document, an issue tracker,
a weekly call. Without this the pilot produces impressions that evaporate.

---

## Deliberately not in this round

| Item | Why |
| --- | --- |
| V2 agent brains | decided: ship on V1 and gather feedback first. Eight open questions remain, the mixed-version panel being the largest |
| Crowded evidence budget | geriatric passages take about a third of medical oncology's budget on every case. Measured, needs a retrieval-policy decision |
| G8 contradiction | `docs/implementation/agent-geriatric-frailty-brain-v1.md` lines 17/60/133 contradict each other. Needs its clinical author |
| Hardening | standing decision: friction that reduces feedback volume is not worth adding yet |

## Suggested order

Revised once the mock was adopted. Because the mock's rail *is* the case list and its metrics
card *is* the blocking-clarity fix, the design work absorbs A1 and B1 rather than following
them — building them twice would be waste.

1. **A2** — case status first. Everything else displays it: the rail's status dots and the
   list are meaningless while every case reads `ready_for_review` for ever. Backend only.
2. **D** — the restyle, including the rail/case list (A1) and the completeness card with its
   `Bloquantes` count (B1). Settle **D-conflict 1** before starting the progress section;
   ship **Questions** disabled per D-conflict 2.
3. **A3** — invitation UI, in the new visual language.
4. **B2**, **B3** — duplicate fields, overrun threshold.
5. **C1**-**C4** — runbook, accounts, rehearsal, feedback channel.
6. **Questions tab** — separately costed, needs clinical sign-off.

A2 plus D is the smallest set that makes the product usable unaided *and* looks like the
product you want to show people. If time runs short, ship that and create accounts by hand.
