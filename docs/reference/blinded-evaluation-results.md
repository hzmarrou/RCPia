# Blinded expert evaluation — results

Extracted 17 August 2026 from `Evaluation double aveugle RCPia/Dossier N/Expert en *.xlsx`
and unblinded with `blinded-evaluation-key.md`.

**Design.** 15 dossiers. For each, two fiches — one written by the RCP, one produced by an
earlier RCPia build — presented in randomised order as `PPS_1` / `PPS_2`. Three specialists
(urology, medical oncology, radiotherapy) scored both independently on four statements,
1 (*pas du tout d'accord*) to 5 (*tout à fait d'accord*):

1. **Conformité** — conforme aux recommandations AFU 2026 / NCCN
2. **Sécurité** — aucune erreur médicale critique
3. **Personnalisation** — prise en compte des scores cliniques et échelles validées
4. **Multidisciplinarité** — répond à l'impératif de multidisciplinarité

45 scored evaluations, all complete.

## Headline

| Criterion | Human | RCPia | Human better | RCPia better | Tie |
|---|---|---|---|---|---|
| Conformité | 3.40 | 3.33 | 15 | 16 | 14 |
| Sécurité | 3.09 | **3.11** | 13 | 15 | 17 |
| Personnalisation | 4.27 | 4.22 | 7 | 4 | 34 |
| Multidisciplinarité | 3.89 | 3.84 | 11 | 9 | 25 |

By specialty (mean across all four criteria):

| Expert | Human | RCPia |
|---|---|---|
| Urologie | 3.75 | 3.75 |
| Oncologie médicale | 3.65 | 3.60 |
| Oncologie radiothérapie | 3.38 | 3.30 |

**The two arms are indistinguishable.** Every gap is within 0.08 of a point on a 5-point
scale, the win/loss counts are near-even on all four criteria, and no specialty separates
them. On safety the machine arm scores marginally higher.

## What this does not show

**It is not this build.** "RCPia" here is the agent that existed when the evaluation was
prepared. Nothing in this repository has been evaluated by a clinician. Quoting these
numbers as results for the current system would be false.

**Parity is at a mediocre absolute level.** Sécurité averages 3.09 for the *human* fiches —
"neutral" on a scale where 5 means the document contains no critical medical error. Experts
were not enthusiastic about either arm. Matching the human documents is therefore a weak
target, which is the same conclusion the IV-albumin line in Dossier 1 already pointed at:
these are real working documents, not exemplars.

**The raters disagree with each other more than the arms differ.** Dossier 1 is the clearest
case: medical oncology scored the RCPia fiche 4/3/5/5 while radiotherapy and urology both
scored it 1/2/1/2 — the same document, the same criteria, opposite verdicts. Any conclusion
drawn from a single dossier is noise.

**11 of 45 pairs carry identical scores for both documents** (dossiers 4, 12 and 14 almost
entirely). Either those experts genuinely saw no difference, or some rows were filled in
without differentiating. Worth asking before the numbers are used in anything published.

**n is small and the ratings are not independent** — 15 documents per arm, each rated by
the same three people. No significance test is reported here because none would mean much
at this size; the honest statement is that no difference was detected, not that none exists.

## What it is good for

- A **regression baseline**. The current build can be run on all 15 dossiers and its output
  compared against the earlier build's, which has a measured expert score attached.
- A **rubric**. The four criteria are what the clinicians who will use RCPia actually care
  about, and three of the four are things the current design already tries to make
  structural: safety (no invented facts, no uncited claims), personalisation (the ledger and
  its scores), and multidisciplinarity (the agent panel, Phase 9).
- A **reason to be careful about conformity**. Section 6 of the current PPS deliberately
  declines to name a *référentiel*, because no evidence corpus exists yet. The lowest-scoring
  criteria here are conformity and safety, and Phase 6 retrieval is what addresses the first
  of them.

## Reproducing

`scratchpad/scores.py` reads the spreadsheets and applies the key. Note that Excel stores
these values as `2.0`, not `2`: an integer-only parse reads every score as absent and
reports a fully scored study as an empty one.
