# Merged safety-rule catalogue — prostate

**Status**: **9 of 9** position-bearing prostate documents extracted. Every rule is
`clinician_validation_status: pending`.

**Extracted**: `eau.prostate.2026`, `nccn.prostate.6.2026`, `esmo.prostate-local.2020`,
`esmo.prostate-advanced.2020`, `aua.prostate-localized.2026`, `aua.prostate-advanced.2026`,
`afu.prostate.2024-2026`, `aua.prostate-early-detection.2026`, `aua.prostate-salvage.2024`.

**Method**: as for bladder — structural section selection, then filtered to passages stating a
threshold, contraindication or prerequisite. **223 passages read.**

---

## Divergences preserved

### PD1. Three different PSA thresholds for "the cancer is back"

| threshold | what it decides | source |
|---|---|---|
| **> 0.1 ng/mL** persistent after RP | restage with PSMA-PET-CT | ESMO local §recommendations |
| **0.2 ng/mL** + a confirmatory value ≥ 0.2 | *defines* biochemical recurrence after RP | AUA advanced, terminology |
| **> 0.2 ng/mL** | perform PSMA PET/CT if it will change management | EAU §6.4.4.d |
| **> 0.4 ng/mL** | the threshold that *best predicts further metastases* after RP | EAU §7.3.5 |
| **≤ 0.5 ng/mL** | the PSA at or below which **salvage radiotherapy should be given** — it is more effective at lower PSA | AUA salvage st. 1–2 |
| **≥ 0.7 ng/mL** | a **high-risk feature** warranting ADT alongside salvage RT; RTOG 9601 found no overall-survival benefit from adding ADT below it | AUA salvage st. 13 |
| **nadir + 2 ng/mL** | recurrence after IMRT/VMAT + IGRT | EAU §7.3.5 |

These are not contradictory — they answer different questions — but a patient with a PSA of 0.15
after prostatectomy is *restageable* by ESMO, *not yet recurrent* by AUA, and well below EAU's
metastasis-predicting threshold. **An agent citing any one of them alone would give a confident
and incomplete answer.** The rule carries all seven.

**The salvage guideline sharpens this into a timing problem.** Salvage radiotherapy should be
given at **≤ 0.5**, and AUA defines recurrence itself at **0.2**. The window between the moment
a recurrence can be called and the moment salvage becomes less effective is **0.3 ng/mL wide** —
which is inside the ±15% intraindividual PSA variation EAU records at PT15. A system reporting
these thresholds without that variation alongside would imply a precision the assay does not
have.

### PD2. The PSA doubling time cut-point moves, including inside one guideline

- **AUA statement 8**: high-risk biochemical recurrence is *"generally… PSADT ≤ 9 months"*
- **AUA statement 22**: high-risk nmCRPC warranting apalutamide/darolutamide/enzalutamide is
  *"PSADT ≤ 10 months"*
- **EAU §6.7.15** and **ESMO**: nmCRPC high risk at *"PSA doubling time < 10 months"*
- **AUA salvage statement 13**: a high-risk feature warranting ADT with salvage RT is
  *"PSADT ≤ 6 months"*

**Three AUA cut-points — six, nine and ten months — across three documents, for three disease
states.** Each is defensible in its own context and none is wrong. But "high-risk doubling time"
is a phrase a clinician or an agent will carry between contexts, and it means something different
in each. The rule stores the state alongside every value; a citation that drops the state is
unsafe.

### PD3. ADT duration alongside radiotherapy for high-risk disease

- **AUA statement 41**: *"long-course (18 to 36 months) ADT with radiation therapy"* (Strong, Grade A)
- **ESMO**: for very-high-risk localised or cN1 — *"neoadjuvant and adjuvant ADT (3 years) and
  abiraterone (2 years)"* [I, A], noting abiraterone is **not EMA or FDA approved in this setting**

A range against a fixed duration, and ESMO adds a drug its own note says is unapproved here.
Both belong in front of a board.

### PD4. Screening start age, and an ancestry criterion only one guideline states

- **EAU §5.2.8**: offer early PSA testing from **50**; from **45** with a family history
- **AFU**: initiation at **50** for the general population; *"En cas d'origine Africaine ou
  Afro-Caribéenne l'initiation peut se faire à 40 ou 45 ans"*

**AFU names an ancestry criterion EAU does not.** Worth flagging beyond the clinical point: the
corpus vocabulary already carries `ancestry_unreliable` as a `not_specified_reason`, so this is a
dimension the routing has previously judged unsafe to infer. A rule that fires on ancestry needs
that value to come from the dossier, never from a name or an assumption.

---

## A. Numeric thresholds (prostate)

| # | Rule | Value | Sources |
|---|---|---|---|
| PT1 | Life expectancy gate for local treatment | **≥ 10 years** for benefit; < 10 years → watchful waiting | EAU §6.1.4, §6.3.1.d, §6.3.2.f, §6.3.3.c; ESMO; AUA st. 9 |
| PT2 | Geriatric screening trigger | **G8 ≤ 14 → full specialist geriatric evaluation**; systematic health-status evaluation over 70 (SIOG) | EAU §6.1.3, §6.1.5 |
| PT3 | Cognitive screening trigger | **mini-COG ≤ 3/5 → refer for full cognitive assessment**; may need capacity evaluation | EAU §6.1.3.c |
| PT4 | Malnutrition by weight loss over 3 months | < 5% good; **5–10% at risk; > 10% severe** | EAU §6.1.3.b |
| PT5 | Castration-resistance definition | **confirm testosterone < 50 ng/dL before diagnosing CRPC** | EAU §6.7.13 |
| PT6 | High-risk PSA doubling time | **≤ 6 months** (salvage RT) / ≤ 9 months (BCR) / ≤ 10 months (nmCRPC) — see **PD2** | AUA st. 8, st. 22; **AUA salvage st. 13**; EAU §6.7.15; ESMO |
| PT7 | PSA thresholds for recurrence and restaging | 0.1 / 0.2 / 0.4 / **0.5** / **0.7** / nadir + 2 — see **PD1** | ESMO, AUA advanced, **AUA salvage st. 1–2, st. 13**, EAU |
| PT8 | Active surveillance in ISUP GG2 | < 10% pattern 4, PSA < 10 ng/mL, ≤ cT2a, low disease extent on imaging | EAU §6.3.2.f; ESMO |
| PT9 | Low-risk definition | cT1–cT2a, Grade Group 1, PSA < 10 ng/mL | NCCN; EAU; AUA |
| PT10 | High-volume mHSPC | **≥ 4 bone metastases with at least one outside the axial skeleton** | AUA st. 11 |
| PT11 | ADT duration with radiotherapy, high risk | 18–36 months (AUA) against 3 years + 2 years abiraterone (ESMO) — see **PD3** | AUA st. 41; ESMO |
| PT12 | PSA screening start age and interval | 50 general; 45 with family history (EAU); **40–45 for African or Afro-Caribbean ancestry** (AFU); **every 2–4 years for ages 50–69** (AUA, Strong Grade A) — see **PD4** | EAU §5.2.8; AFU; **AUA early-detection st. 6** |
| PT13 | Life expectancy for individualised early detection | ≥ 15 years | EAU §5.2.8 |
| PT14 | Equivocal PSA range | 3–10 ng/mL → repeat PSA and use PSA density in risk stratification | EAU §5.4.1.b |
| PT15 | PSA measurement error | ± 15% intraindividual variation; ~5% between laboratories | EAU §5.2.2 |
| PT16 | Systematic biopsy scheme | 12 cores transrectal, medial and lateral per lobe, base to apex | AFU |
| PT17 | Exercise prescription on ADT | 12 weeks supervised combined aerobic and resistance | EAU §8.3.2.e; ESMO |
| PT18 | Alcohol limit on ADT | ≤ 2 units daily; yearly diabetes and cholesterol screening | EAU §8.3.2.e |
| PT19 | Gait speed as a life-expectancy predictor | at 75, ten-year survival 19% at < 0.4 m/s against 87% at high speed | EAU §6.1.2 |
| PT20 | Family-history referral trigger for genetics | first-degree relative or multiple second-degree relatives with Grade Group ≥ 2, particularly **< 60 years**, particularly if metastatic or lethal | AUA localized; AFU |
| PT21 | Supplementary DRE trigger during screening | **PSA ≥ 2 ng/mL** — strongly consider DRE to establish risk of clinically significant cancer | AUA early-detection st. 8 |
| PT22 | What counts as an elevated screening PSA | the definition has changed — **4 ng/mL** from early studies, **3 ng/mL** from the Finnish ERSPC arm. AUA records both rather than fixing one | AUA early-detection §PSA screening |
| PT23 | Range where adjunctive biomarkers are used | mildly elevated PSA, typically **2.5–10 ng/mL** | AUA early-detection |
| PT24 | Negative MRI does not exclude cancer | negative predictive value of PI-RADS 1–2 for GG2+ is **91%** — about **1 in 10** still has GG2+ on biopsy | AUA early-detection |
| PT25 | Atypical findings on biopsy that mandate repeat | **ASAP** carries a 30–50% cancer detection rate on repeat biopsy (10–20% of those GG2+); **AIP** lacks definitive criteria for intraductal carcinoma | AUA early-detection st. 28–29 |
| PT26 | Salvage radiotherapy timing | give salvage RT at **PSA ≤ 0.5 ng/mL**; it is more effective at lower PSA — see **PD1** | AUA salvage st. 1–2 |

## B. Contraindications (prostate)

| # | Rule | Sources |
|---|---|---|
| PC1 | **Do not prescribe adjuvant ADT to pN0 patients** | EAU §6.3.5.g |
| PC2 | **Do not offer first-generation antiandrogens** (bicalutamide, flutamide, nilutamide) with LHRH agonists in mHSPC, except to block testosterone flare | AUA st. 18 |
| PC3 | Do not use MRI as an initial screening tool | EAU §5.5.8 |
| PC4 | Do not use additional imaging for staging in low-risk and favourable intermediate-risk disease | EAU §5.8.5; AUA st. 6 |
| PC5 | PSA testing cannot be recommended for asymptomatic men with life expectancy < 10 years [I, D] | ESMO local |
| PC6 | Abiraterone not recommended for nmCRPC — other options exist and there is no approved indication | AUA |
| PC7 | Ablative techniques limited to trial enrolment, or informed intermediate-risk patients declining standard therapy | AUA localized |
| PC8 | Gleason score reporting not recommended for mCSPC on biopsy of previously irradiated tissue or pure neuroendocrine carcinoma | ESMO advanced |
| PC9 | **Do not use DRE as the sole screening method** in otherwise asymptomatic patients; there is insufficient evidence to add DRE to PSA-based screening. Does not apply to symptomatic patients, where DRE is diagnostic | AUA early-detection st. 8 |
| PC10 | **Do not discontinue screening on the basis of a negative biopsy alone** | AUA early-detection st. 20 |
| PC11 | **Do not add docetaxel** to salvage radiotherapy with ADT; abiraterone is likewise not recommended in the salvage setting — the STAMPEDE population had a median PSA of 34–40 ng/mL and salvage-setting data are not mature | AUA salvage st. 21 |

## C. Prerequisites (prostate)

| # | Rule | Sources |
|---|---|---|
| PP1 | **Clinical stage must be based on digital rectal examination only**; imaging and pathology reported separately | EAU §4.5 |
| PP2 | Risk-stratify using clinical T stage, PSA, Grade Group and biopsy tumour volume | AUA st. 1; EAU; NCCN; ESMO |
| PP3 | **Report presence of intraductal and cribriform carcinoma**; ESMO treats both as Gleason pattern 4 and aggressive; AIP is the lesion class that falls short of intraductal criteria and mandates repeat biopsy | EAU §5.3.2.b; ESMO advanced; **AUA early-detection st. 29** |
| PP4 | Screen health status with G8, mini-COG and the Clinical Frailty Scale | EAU §6.1.5 |
| PP5 | Full specialist geriatric evaluation where G8 ≤ 14; base decisions on life expectancy, health status and comorbidity rather than age | EAU §6.1.5, §6.1.4 |
| PP6 | Transperineal biopsy as first choice, with perineal cleansing and antibiotic prophylaxis, to reduce infectious complications | EAU §5.6.4 |
| PP7 | Adhere to PI-RADS for acquisition and interpretation; radiologists should use PI-RADS in reporting mpMRI; evaluate MRI in multidisciplinary meetings with pathological feedback | EAU §5.5.8; **AUA early-detection st. 14** |
| PP8 | Discuss every hormone-sensitive metastatic patient in a multidisciplinary team | EAU §6.6.8 |
| PP9 | **Germline testing for all advanced prostate cancer; somatic tumour testing for metastatic disease** — BRCA1, BRCA2, CDK12, PALB2 at minimum, ideally before first mCRPC treatment | AUA advanced st. 4; ESMO §biomarkers; NCCN |
| PP10 | Early identification of hereditary disease — urologist screens, oncogenetics consultation confirms | AFU; AUA |
| PP11 | Perform extended pelvic lymph node dissection where a dissection is done — ilio-obturator, internal and external iliac bilaterally to the iliac bifurcation | EAU §6.3.3.c, §6.3.4.d; AFU rec. 12 |
| PP12 | Nerve-sparing at radical prostatectomy where oncologically appropriate | AUA st. 20 |
| PP13 | Measure baseline bone mineral density on long-term ADT to assess fracture risk | EAU §7.4.6; ESMO; AUA bone health |
| PP14 | Dental evaluation before bisphosphonates or denosumab — osteonecrosis of the jaw risk | AUA bone health |
| PP15 | Assess extent of metastatic disease and stratify low against high volume in newly diagnosed mHSPC | AUA st. 10–11 |
| PP16 | Baseline labs in mCRPC — PSA, testosterone, LDH, haemoglobin, alkaline phosphatase — plus metastatic sites, symptoms and performance status | AUA st. 25 |
| PP17 | Serial PSA at 3–6 month intervals with PSADT calculated from the onset of castration resistance | AUA st. 20 |
| PP18 | Obtain tissue diagnosis before treating suspected advanced disease, where clinically feasible | AUA st. 1 |
| PP19 | Monitor after therapy with PSA and symptom assessment | AUA st. 45; EAU §7.3.5 |
| PP20 | Active surveillance monitoring — serial PSA and repeat biopsy; MRI augments risk stratification but does not replace periodic biopsy | AUA st. 18–19; EAU §6.2.1 |
| PP21 | Inform patients that all treatments carry risk; weigh urinary, sexual and bowel effects against cancer risk, life expectancy and comorbidity | AUA st. 9 |
| PP22 | Provide an individualised risk estimate of post-treatment recurrence | AUA st. 10 |
| PP23 | Base treatment choice in mCRPC on performance status, symptoms, comorbidities, disease location and extent, genomic profile, patient preference and prior therapy | EAU §6.7.14 |
| PP24 | Shared decision-making before screening; PSA is the **first** screening test | AUA early-detection st. 1–2 |
| PP25 | **Repeat the PSA before acting on a newly elevated value** | AUA early-detection st. 3 |
| PP26 | Before biopsy, inform the patient that a detected cancer may be safe to monitor with active surveillance rather than treat | AUA early-detection st. 12 |
| PP27 | Where MRI shows a suspicious lesion in a biopsy-naive patient, perform **targeted *and* systematic** biopsies — combined detection of GG2+ was 87% against 69% targeted and 80% systematic alone | AUA early-detection st. 15 |
| PP28 | After BCR following primary radiotherapy or ablation, **perform a prostate biopsy** to confirm local recurrence before local salvage therapy | AUA salvage st. 23 |
| PP29 | Where salvage local therapy follows **focal** ablation, offer **whole-gland** treatment by prostatectomy or radiotherapy | AUA salvage st. 25 |
| PP30 | Inform the patient that salvage radiotherapy risks urinary, erectile and bowel function, weighed against recurrence risk, life expectancy, comorbidity and preference | AUA salvage st. 4 |

---

## Dossier annotation — the 8 prostate patients

| | age | Gleason | ISUP | PSA | stage | other |
|---|---|---|---|---|---|---|
| D3 | 70 | 3+4 | 2 | 7.5 | PIRADS 5, organ-confined | MNA 23.5 |
| D4 | 72 | 3+4 | 2 | 5.1 | PIRADS 4 | **GIST on imatinib** |
| D5 | 70 | 3+4 | 2 | 8.9 | PIRADS 5 | **Hb 9.2** |
| D6 | 59 | 5+4 | 5 | **116** | metastatic, bone | eGFR 114 |
| D7 | 72 | 4+3 | 3 | 34 | PIRADS 5 | |
| D8 | 61 | 5+4 | 5 | 73 | **CRPC**, progressing on ADT + abiraterone + docetaxel | MNA 15.5 |
| D9 | 70 | 4+3 | 3 | 34 | **T3a**, PIRADS 5 | missing labs |
| D12 | 70 | 4+4 | 4 | 12 | metastatic, PSMA+ bone | **intraductal carcinoma** |

**Fires on 3 or more**: PP1 (DRE-only staging), PP2 (risk stratification), PP21/PP22 (risk
communication), PT1 (life expectancy gate), PT9 (low-risk definition — as an exclusion for all
eight), PP19 (post-therapy monitoring).

**Fires on 1 or 2, and each matters**:

| rule | fires on | why |
|---|---|---|
| **PP3 intraductal/cribriform reporting** | **D12** | intraductal carcinoma is recorded; ESMO treats it as pattern 4 and aggressive. This is gold-set question `pr-003`. |
| **PT5 confirm testosterone < 50 before diagnosing CRPC** | **D8** | recorded as castration-resistant; the confirmatory testosterone is not in the dossier |
| **PT6 PSADT** and **PP17 serial PSA with PSADT** | **D8** | progressing on ADT; no doubling time recorded |
| **PP9 germline and somatic testing** | **D6, D8, D12** | all metastatic or castration-resistant; **none records a genetic test** |
| **PP15 low against high volume mHSPC** | **D6, D12** | both metastatic to bone; volume not stratified in either dossier |
| **PP13/PP14 bone density, dental review** | **D6, D8, D12** | all on or heading for long-term ADT |
| **PT4 malnutrition by weight loss** | **D8** | MNA 15.5; weight-loss percentage not recorded |
| **PT2/PP4 G8 screening** | **D4, D7 (72), D3, D5, D9 (70)** | five patients at or over 70, and **no prostate dossier records a G8 score at all** |
| **PC1 no adjuvant ADT to pN0** | D9 | T3a, candidate for surgery with nodal assessment |

### Three findings

**1. No prostate dossier records a G8 score.** EAU requires systematic health-status evaluation
over 70 and a full geriatric evaluation at G8 ≤ 14. **Five of eight prostate patients are 70 or
over**, and the screening instrument that decides whether they get a geriatric assessment is
absent from every one of them. This is the same intake gap the bladder annotation found for
eGFR, in a different field.

**2. The metastatic and castration-resistant patients have no genetic testing recorded.** PP9 is
stated by AUA, ESMO and NCCN together — the strongest three-source convergence in the prostate
set — and fires on D6, D8 and D12. None of the three records germline or somatic testing. It also
lands on the corpus gap already known: `genetics_molecular` holds **zero passages**, so even when
the rule fires there is nothing to cite.

**3. D8's castration-resistance is asserted, not confirmed.** PT5 requires a testosterone below
50 ng/dL before the diagnosis is made. The dossier records progression on ADT, abiraterone and
docetaxel, and no confirmatory testosterone. The rule cannot verify a label the whole treatment
pathway depends on.

---

## Counts

| | |
|---|---|
| documents extracted | **9 of 9** prostate position-bearing |
| passages read | 223 |
| numeric thresholds | 26 |
| contraindications | 11 |
| prerequisites | 30 |
| **total merged rules** | **67** |
| divergences preserved | 4 (PD1–PD4), two of them extended by the final two documents |

The last two documents added **16 new rules** and **6 new citations to rules already in the
catalogue** — a merge rate of about 27%, higher than the bladder half's, because screening and
salvage are topics the other seven documents largely do not cover.

**Corpus total: 124 distinct rules** — 57 bladder, 67 prostate — from **18 of 18**
position-bearing documents and **487 passages read**. That lands inside the 120–140 projection.

### What the last two documents changed

Neither covers a decision the 15 current dossiers face, and **no rule from either fires on any
existing dossier** — as expected. Their value was in extending two divergences that already
existed:

- **PD1 gained two thresholds** (0.5 and 0.7 ng/mL), taking the count of PSA values that mean
  something different to **seven**. It also produced the sharpest single observation in the
  prostate set: the window between calling a recurrence (0.2) and losing salvage effectiveness
  (0.5) is **0.3 ng/mL**, narrower than the ±15% assay variation EAU records at PT15.
- **PD2 gained a third AUA cut-point** — 6 months for salvage, against 9 for biochemical
  recurrence and 10 for nmCRPC. Three numbers, three documents, one guideline body, one phrase:
  *"high-risk PSA doubling time"*.

Both are arguments for storing the disease state alongside every threshold. A citation that
carries the number without the state is not merely incomplete — it is wrong in a way that reads
as authoritative.

## Still to do

- Clinical review of all 124 rules, with the nine divergences flagged for decision
- A pass over rule **pairs**, not rules — the bladder annotation found two rules that interact
  with neither naming the other
- Populate `genetics_molecular`, or accept that PP9 fires with nothing to cite
