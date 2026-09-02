# Backlog — after v1

Work that is **not** part of the current version. Everything here is deferred because it needs
something the project does not have yet: a patient population, a class of document, or evidence
that a change is worth making. Each entry says what would trigger it.

What is *not* here: wiring, further agents, defect fixes and data-collection asks. Those are
current-version work and are tracked as such.

Companion to `docs/decisions.md`, which records what was decided and why.
`docs/RCPia-v1-implementation-plan.md` is the original build plan — its checkboxes were never
ticked and most of Phases 0–9 are in fact built, so read it as where the project started, not
as current state.

---

## Nomograms

Two models compute in v1: **Briganti 2012** and **MSKCC pre-radical-prostatectomy** (five
endpoints). The four below are deferred, and the reasons differ enough to matter.

### Briganti 2019

Selected when MRI identified an index lesion, MRI-targeted biopsy was performed and was
positive, and concomitant systematic biopsy data are available. Version selection already
routes to it correctly; it then refuses, because nothing downstream exists.

| input | status |
|---|---|
| MRI local stage — organ-confined / ECE / SVI | no ledger field; sits in free-text MRI reports |
| maximum index-lesion diameter, mm | no ledger field. The `index_lesion_diameter` rule (highest PI-RADS, largest on ties) is written and nothing feeds it |
| Grade Group from the **targeted** biopsy | no ledger field, and must never be substituted by the overall Grade Group |
| % systematic cores with **clinically significant** cancer | no ledger field; distinct from the plain positive-core percentage |
| coefficients | not implemented, not verified |

**Trigger:** dossiers routinely carrying mpMRI-targeted biopsies, with reports structured
enough to extract from. Coefficients without inputs compute nothing, so the extraction comes
first. Verification then requires fixed test vectors reproducing a reference calculator, as the
MSKCC pre-op model was verified.

### Briganti 2023 — the one the clinician asked for

> *Idéalement, nous voulons utiliser la version Briganti 2023, car ne serait-ce que sur les 15
> dossiers d'essai nous disposons de dossiers avec notamment des données d'imagerie
> compatibles seulement avec la version 2023.*

Selected only when a preoperative PSMA PET/CT was performed **and** is explicitly negative for
nodal and distant disease (`miN0M0`). "Negative" is the requirement, not "performed" — a
positive PSMA PET has already answered the question this nomogram asks.

Everything 2019 needs, plus:

| input | status |
|---|---|
| PSMA PET performed | no ledger field |
| explicit `miN0M0` reading | no ledger field — and *"no suspicious nodes reported"* must **not** be read as `miN0M0` |
| whether biopsy Grade Group is 5 | derivable once Grade Group is reliable |
| coefficients | not implemented, not verified |

**Trigger:** PSMA PET reports present in dossiers, with conclusions readable structurally. The
`miN0M0` rule is the risk: a model that runs on a loosely-worded PET conclusion is worse than
one that refuses, because it returns a number from a population the patient may not be in.

### MSKCC post-operative — rebuild, not repair

The existing `msk_postop_nomogram.py` is **not the current MSKCC model**. Checked against the
official December 2024 coefficients page, not one term matches:

| term | official | the script |
|---|---|---|
| intercept | 5.80317571 | 4.876 |
| patient age | **+0.00659938** | **−0.0077** (opposite sign) |
| preoperative PSA | −0.29742375 + two spline terms | −0.3001 on `log(PSA+1)`, no splines |
| Gleason | **two sets** — biopsy *and* pathologic grade group | one set, matching neither |
| stage | **clinical** 2A/2B/2C/3+ | **pathologic** pT2b–pT3b |
| extracapsular extension | −0.60889233 | absent |
| seminal-vesicle invasion | −0.45156925 | absent |
| surgical margins | −0.93135164 | −0.4154 |
| lymph nodes | −1.17087938 | −1.1060 |
| scaling parameter | 0.93808757 | 1.2662 |
| year of surgery | absent | +0.0153 × (year − 1987) |

The survival function differs too. The script cites Stephenson 2005 and then claims to be
*"mis à jour selon la version dynamique MSKCC"*; the numbers contradict that claim, and its
four-decimal precision against MSKCC's eight is the other tell.

It also needs four fields from the prostatectomy specimen that no dossier records:
extracapsular extension, seminal-vesicle invasion, surgical margin status, nodal status.

**Trigger: post-prostatectomy cases reaching the RCP.** None of the fifteen dossiers has had
surgery — every one sits at the pre-operative decision point, which is what the implemented
model is for. Building this earlier would serve no patient. When it comes it is two jobs:
replacement coefficients from the official page, and the pathology fields with extraction to
fill them.

### MSKCC biochemical-recurrence mortality

Needs prostatectomy pathology, documented rising PSA, recurrence timing and PSA doubling time.
None recorded, and the same absent population as the post-operative model.

**Trigger:** post-prostatectomy cases *and* a working post-operative model, since both rest on
the same pathology fields.

### MSKCC biopsy risk — will not be implemented

Excluded by the clinician:

> *Nous ne sommes pas concernés par le risk of High-Grade Cancer on Prostate Biopsy puisque ce
> cas là ne représente pas un diagnostic établi de cancer de prostate et donc de dossier RCP.*

It answers a question asked before a diagnosis exists, and a case without a diagnosis is not an
RCP case. Implementing it would invite use on dossiers this system is not for. Recorded here so
the decision is not re-opened by someone finding the script — this is a decision, not a
deferral.

### Two endpoints excluded from the implemented MSKCC model

**Cancer-specific survival at fifteen years.** MSKCC reports it at 15 years; the reference
implementation evaluates at 5. Measured: 0.14% against the calculator's 2%, and 4.3% against
42%. Excluded rather than shipped wrong. Restoring it means parameterising the horizon and
re-verifying against the same two reference patients.

**Ten-year progression-free probability.** Not computed, same cause.

**Trigger for both:** a clinician asking for them. Neither was named as used — the request was
local aggressiveness and nodal extension, both of which ship.

---

## Runtime

**Pi 0.84.3** stays parked on branch `pi-0.84.3-ab`, with its package pins committed so the
comparison is reproducible. Measured over 16 runs per arm and rejected: reliability tied at
16/16, the stall reproduced in neither arm, startup variance no better and possibly worse. Its
one clear win — stdout 185 MB → 1.3 MB — was mostly a fix to traffic RCPia's own `--mode json`
instrumentation had provoked, since addressed by filtering `message_update` by event name.

**Trigger:** stdout volume becoming a practical problem again after that filter, or repeated
runs reproducing stalls on 0.80.3 and not on 0.84.3. Any final comparison must run inside the
worker image, or at least on Node 22 — the original A/B used host Node v24.11.1 while
production pins 22.23.2.
