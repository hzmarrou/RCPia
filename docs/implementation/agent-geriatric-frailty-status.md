# Onco-gériatrie — deployment status

**Deliberately not deployed.** `agents/geriatric_frailty/` does not exist, and that absence is
the control: the Pi adapter needs an agent folder, so a request naming this role enqueues a job
that fails rather than producing an opinion. The design document below is written and the agent
has been run and measured, but nothing in the running system can reach it.

Two things have to happen before the runtime folder is added.

1. **Clinical review of the brain**, and one contradiction in particular (see below).
2. **An explanation for a 600-second timeout** observed in one of four runs.

## Why the folder is the control, and not a setting

An earlier version of this document called the agent "dormant" on the strength of
`AUTO_PANEL_ROLES` being empty. That was wrong, and a reviewer caught it.

`AUTO_PANEL_ROLES` governs only whether a panel is convened **automatically** after extraction.
It does not restrict what the analysis and panel APIs accept, and `geriatric_frailty` has always
been a member of `AGENT_ROLES`, so a clinician could request this speciality by name. A
successful run is then eligible for synthesis and for PPS generation like any other — meaning an
unreviewed agent's opinion could reach a fiche.

The only thing that was ever stopping it was the missing folder. So the folder stays missing.

The same is true of `radiology`, `pathology` and `safety_reviewer`: each has a directory
containing `.pi/.gitkeep` and nothing else. They are not wired, they cannot run, and the fiche
is right to list them among the absent specialities.

## The contradiction to resolve

Both lines are in `agent-geriatric-frailty-brain-v1.md`:

- *G8 > 14 : patient "fit" — peut recevoir un traitement standard. Pas d'EGA nécessaire sauf si
  un domaine spécifique est préoccupant.*
- *Un score isolé ne suffit pas. Le G8 est un outil de dépistage, pas un diagnostic.*

One declines to conclude from a screening score alone; the other concludes "fit, standard
treatment" from exactly that. It may be defensible — "G8 > 14, no further assessment" is the
SIOG and EAU screening position, and the second rule's specific clause concerns G8 ≤ 14 — but
whether the escape hatch carries that weight is a clinical judgement. It belongs to whoever
authored the document, not to this repository.

## Why the agent is wanted

Measured 4 September 2026 over 319 valid runs. Runs before 31 August went out with no brain and
no guideline passages, so they are not evidence about anything and are excluded.

| role | passages | geriatric | geriatric cited | everything else cited |
|---|---|---|---|---|
| medical_oncology | 14.0 | 4.6 (33%) | **19%** | 56% |
| urology_oncology | 14.0 | 4.3 (31%) | 19% | 57% |
| radiation_oncology | 13.8 | 2.5 (18%) | 28% | 38% |

Generalist agents spend about a third of their fourteen guideline slots on geriatric and
health-status passages, and cite them at a third the rate of everything else — roughly 3.7 slots
per run that never reach the opinion. Handed to an agent whose subject they are, the same class
of passage landed 14 of 14 on-topic with 5 cited. They were not useless; they were going to the
wrong reader.

Two corrections to the note that prompted this work. The effect is **not**
medical-oncology-specific: urology sits at 31% against medical oncology's 33%. And a first pass
of this measurement matched `SIOG` inside
`EAU - EANM - ESTRO - ESUR - ISUP - SIOG Guidelines on Prostate Cancer` and reported the main
prostate guideline as the largest geriatric attractor, 132 times, before the classifier was
corrected to strip society-authorship runs.

**None of this reclaims the generalists' slots.** `query/builder.py` builds each agent's query
from the case and that agent's own role, never from panel composition, so adding a speciality
changes nothing about what medical oncology retrieves. That problem is still open, and the
options on it — a larger passage budget, or a diversity cap in reranking — are retrieval policy.

## What the runs measured, including the conclusion that was wrong

Four runs on REGEN5 cases, with a temporary agent folder that has since been removed:

| run | bundle | reasoning | total | outcome |
|---|---|---|---|---|
| REGEN5-12 | 144s | 83s | 227s | succeeded |
| REGEN5-09 | — | — | >600s | **timed out**, requeued |
| REGEN5-08 | 10s | 82s | 94s | succeeded |
| REGEN5-08, urology, same worker back to back | 10s | 73s | 85s | succeeded |

The first two led to a conclusion that this agent's retrieval was twenty times slower than its
peers. **That was wrong.** Timing `build_bundle` directly on one case gives urology 8.2s,
medical oncology 7.3s and this agent 7.4s; back to back on one worker it finishes 9 seconds —
11% — behind urology.

The timeout was not contention. Its own record says
`silent_for=0s stdout=287028B timeout_layer=process`: the run was emitting output right up to
the moment it was killed, so it was working rather than waiting on the provider. It simply took
more than seven times as long as the same agent's successful runs, on one case, and did not
recur. An earlier version of this document guessed at throttling on the shared Azure resource;
that guess is wrong and `silent_for=0s` is what rules it out.

Why it ran long is unknown. Nothing was rebuilt to find out: it has happened once, and a run
that exceeds `AGENT_TIMEOUT_SECONDS` is requeued and spends its budget again, which matters in a
panel because the synthesis waits for it. Worth revisiting only if it recurs.

## When it is time to deploy

Copy the design document to `agents/geriatric_frailty/brain.md` byte for byte — the document is
the source, the folder is the deployment — and add the two boilerplate files every wired agent
carries: `.pi/APPEND_SYSTEM.md` (one line, "read brain.md first") and `.pi/settings.json`
(provider and model). Verify with `diff` rather than by trusting the copy.
