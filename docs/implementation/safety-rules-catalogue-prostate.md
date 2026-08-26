# Merged safety-rule catalogue — prostate

**Status**: **7 of 9** position-bearing prostate documents extracted. Every rule is
`clinician_validation_status: pending`.

**Extracted**: `eau.prostate.2026`, `nccn.prostate.6.2026`, `esmo.prostate-local.2020`,
`esmo.prostate-advanced.2020`, `aua.prostate-localized.2026`, `aua.prostate-advanced.2026`,
`afu.prostate.2024-2026`.

**Not yet extracted**: `aua.prostate-early-detection.2026` (30 rule-bearing passages) and
`aua.prostate-salvage.2024` (24). Both are narrower in scope than the seven above — screening
and post-treatment salvage — and neither covers a decision any of the eight prostate dossiers
currently face. **They are a known gap, not a completed pass.**

**Method**: as for bladder — structural section selection, then filtered to passages stating a
threshold, contraindication or prerequisite. **169 passages read.**

---

## Divergences preserved

### PD1. Three different PSA thresholds for "the cancer is back"

| threshold | what it decides | source |
|---|---|---|
| **> 0.1 ng/mL** persistent after RP | restage with PSMA-PET-CT | ESMO local §recommendations |
| **0.2 ng/mL** + a confirmatory value ≥ 0.2 | *defines* biochemical recurrence after RP | AUA advanced, terminology |
| **> 0.2 ng/mL** | perform PSMA PET/CT if it will change management | EAU §6.4.4.d |
| **> 0.4 ng/mL** | the threshold that *best predicts further metastases* after RP | EAU §7.3.5 |
| **nadir + 2 ng/mL** | recurrence after IMRT/VMAT + IGRT | EAU §7.3.5 |

These are not contradictory — they answer different questions — but a patient with a PSA of 0.15
after prostatectomy is *restageable* by ESMO, *not yet recurrent* by AUA, and well below EAU's
metastasis-predicting threshold. **An agent citing any one of them alone would give a confident
and incomplete answer.** The rule carries all five.

### PD2. The PSA doubling time cut-point moves, including inside one guideline

- **AUA statement 8**: high-risk biochemical recurrence is *"generally… PSADT ≤ 9 months"*
- **AUA statement 22**: high-risk nmCRPC warranting apalutamide/darolutamide/enzalutamide is
  *"PSADT ≤ 10 months"*
- **EAU §6.7.15** and **ESMO**: nmCRPC high risk at *"PSA doubling time < 10 months"*

Nine months and ten months, in adjacent statements of the same document, for two different
disease states. Defensible, and exactly the kind of thing that is misread when quoted without
its state.

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
| PT6 | High-risk PSA doubling time | ≤ 9 months (BCR) / ≤ 10 months (nmCRPC) — see **PD2** | AUA st. 8, st. 22; EAU §6.7.15; ESMO |
| PT7 | PSA thresholds for recurrence and restaging | 0.1 / 0.2 / 0.4 / nadir + 2 — see **PD1** | ESMO, AUA, EAU |
| PT8 | Active surveillance in ISUP GG2 | < 10% pattern 4, PSA < 10 ng/mL, ≤ cT2a, low disease extent on imaging | EAU §6.3.2.f; ESMO |
| PT9 | Low-risk definition | cT1–cT2a, Grade Group 1, PSA < 10 ng/mL | NCCN; EAU; AUA |
| PT10 | High-volume mHSPC | **≥ 4 bone metastases with at least one outside the axial skeleton** | AUA st. 11 |
| PT11 | ADT duration with radiotherapy, high risk | 18–36 months (AUA) against 3 years + 2 years abiraterone (ESMO) — see **PD3** | AUA st. 41; ESMO |
| PT12 | PSA screening start age | 50 general; 45 with family history (EAU); **40–45 for African or Afro-Caribbean ancestry** (AFU) — see **PD4** | EAU §5.2.8; AFU |
| PT13 | Life expectancy for individualised early detection | ≥ 15 years | EAU §5.2.8 |
| PT14 | Equivocal PSA range | 3–10 ng/mL → repeat PSA and use PSA density in risk stratification | EAU §5.4.1.b |
| PT15 | PSA measurement error | ± 15% intraindividual variation; ~5% between laboratories | EAU §5.2.2 |
| PT16 | Systematic biopsy scheme | 12 cores transrectal, medial and lateral per lobe, base to apex | AFU |
| PT17 | Exercise prescription on ADT | 12 weeks supervised combined aerobic and resistance | EAU §8.3.2.e; ESMO |
| PT18 | Alcohol limit on ADT | ≤ 2 units daily; yearly diabetes and cholesterol screening | EAU §8.3.2.e |
| PT19 | Gait speed as a life-expectancy predictor | at 75, ten-year survival 19% at < 0.4 m/s against 87% at high speed | EAU §6.1.2 |
| PT20 | Family-history referral trigger for genetics | first-degree relative or multiple second-degree relatives with Grade Group ≥ 2, particularly **< 60 years**, particularly if metastatic or lethal | AUA localized; AFU |

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

## C. Prerequisites (prostate)

| # | Rule | Sources |
|---|---|---|
| PP1 | **Clinical stage must be based on digital rectal examination only**; imaging and pathology reported separately | EAU §4.5 |
| PP2 | Risk-stratify using clinical T stage, PSA, Grade Group and biopsy tumour volume | AUA st. 1; EAU; NCCN; ESMO |
| PP3 | **Report presence of intraductal and cribriform carcinoma**; ESMO treats both as Gleason pattern 4 and aggressive | EAU §5.3.2.b; ESMO advanced |
| PP4 | Screen health status with G8, mini-COG and the Clinical Frailty Scale | EAU §6.1.5 |
| PP5 | Full specialist geriatric evaluation where G8 ≤ 14; base decisions on life expectancy, health status and comorbidity rather than age | EAU §6.1.5, §6.1.4 |
| PP6 | Transperineal biopsy as first choice, with perineal cleansing and antibiotic prophylaxis, to reduce infectious complications | EAU §5.6.4 |
| PP7 | Adhere to PI-RADS for acquisition and interpretation; evaluate MRI in multidisciplinary meetings with pathological feedback | EAU §5.5.8 |
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
| documents extracted | 7 of 9 prostate position-bearing |
| passages read | 169 |
| numeric thresholds | 20 |
| contraindications | 8 |
| prerequisites | 23 |
| **total merged rules** | **51** |
| divergences preserved | 4 (PD1–PD4) |

**Corpus totals so far: 108 distinct rules** — 57 bladder, 51 prostate — from 16 of 18
position-bearing documents and 333 passages read. The projection of 120–140 holds; completing
the two AUA documents should land it near the low end.
