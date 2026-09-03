# Step 2: rule extraction from EAU MIBC 2026, to size the catalogue

**Purpose**: run the extraction prompt against **one** guideline and count what comes out, so
the full-catalogue task can be scoped before it is started. Per the plan: *"If one document
yields 8 rules, the full catalogue is ~300 and we rescope. If it yields 2, we proceed."*

**Result: 33 candidate rules from one document. Rescope.**

---

## Method

`eau.bladder-mibc.2026` holds **227 passages, 178 retrieval-eligible, 233,547 characters**.

Rather than read all 178, the passages were selected structurally — the same principle the
corpus routes by:

| selection | passages | why |
|---|---|---|
| sections whose path contains `recommendation` | 29 | where EAU states its recommendations formally, with strength ratings |
| §6.2–6.4 health status, comorbidity, frailty | 5 | where fitness and assessment prerequisites live |
| §9.3–9.5 first-line therapy by eligibility | 7 | where the platinum eligibility thresholds live |

**41 passages read in full.** The remaining 137 are narrative evidence — trial results,
epidemiology, technique description, HRQoL discussion — which state no rule of their own. That
selection is a judgement and it is the main threat to this count being complete; a rule stated
only in body prose outside a recommendation section would have been missed.

---

## What came out: 33 candidate rules

### A. Numeric thresholds (12)

| # | Rule | Value | Source chunk |
|---|---|---|---|
| T1 | **Unfit for cisplatin** — at least one criterion present | **GFR ≤ 60 mL/min**, PS > 1, grade ≥ 2 audiometric hearing loss, grade ≥ 2 peripheral neuropathy, or NYHA class III | `…9-3-first-line-systemic-therapy-for-metastatic-disease-patie.01.1229` |
| T2 | Eligible for combination therapy (EV+P) | ECOG PS 0–2 **and GFR ≥ 30 mL/min** | `…01.1229` |
| T3 | Not eligible for any combination therapy | PS > 2 and/or **GFR < 30 mL/min** | `…9-5-first-line-therapy-in-patients-not-eligible-for-combinat.01.1235` |
| T4 | Carboplatin candidates (EORTC eligibility) | **GFR < 60** and/or PS 2 | `…9-4-2-b-patients-fit-for-carboplatin-but-unfit-for-cisplatin.01.1234` |
| T5 | First-line pembrolizumab in cisplatin-unfit requires PD-L1 | **CPS ≥ 10**, Dako 22C3 | `…01.1235` |
| T6 | Do not delay radical cystectomy | **> 3 months** (unless receiving NAC) | `…6-7-4-a-summary-of-evidence-and-recommendations-for-robotic-.01.1180` |
| T7 | Minimum institutional volume | **≥ 20 RCs per hospital per year** | `…6-7-9-summary-of-evidence-and-recommendations-for-quality-in.01.1194` |
| T8 | Preoperative RT dose, where used | 45–50 Gy in 1.8–2 Gy fractions | `…6-6-3-summary-of-evidence-and-recommendations-for-pre-and-po.01.1163` |
| T9 | Age is not a bar to cystectomy | **> 80 years** — cystectomy remains an option | `…6-4-1-summary-of-evidence-and-recommendations-for-comorbidit.01.1148` |
| T10 | Adjuvant chemotherapy population | **pT3, pT4 and/or N+** without neoadjuvant treatment | `…6-5-2-c-summary-of-evidence-and-recommendations-for-adjuvant.01.1160` |
| T11 | Adjuvant RT population | **pT3b–4, positive nodes, or positive margins** | `…01.1163` |
| T12 | Radical cystectomy indication | **T2–T4a N0M0** | `…6-7-1-d-recommendation-for-radical-cystectomy.01.1167` |

### B. Contraindications (9)

| # | Rule | Source chunk |
|---|---|---|
| C1 | Do not offer **orthotopic neobladder** to patients with invasive tumour in the urethra or at the level of urethral dissection | `…6-7-5-d-summary-of-evidence-and-recommendations-for-urinary-.01.1189` |
| C2 | Do not offer **preoperative radiotherapy** for operable MIBC | `…01.1163` |
| C3 | Do not offer **preoperative bowel preparation** | `…01.1189` |
| C4 | Do not offer **radiotherapy alone** as primary therapy for localised BC | `…6-9-3-summary-of-evidence-and-recommendation-for-external-be.01.1207` |
| C5 | Do not offer **TURBT alone** as curative treatment | `…6-10-1-a-recommendation-for-transurethral-resection-of-bladd.01.1209` |
| C6 | Do not offer **chemotherapy alone** as primary therapy for localised BC | `…6-10-2-a-summary-of-evidence-and-recommendation-for-chemothe.01.1211` |
| C7 | Do not perform **extended LND** — no survival benefit, increased morbidity | `…6-7-3-a-summary-of-evidence-and-recommendations-for-lymphade.01.1178` |
| C8 | **ASA score must not be used** for comorbidity assessment in this setting | `…01.1148` |
| C9 | **Enfortumab vedotin** not for uncontrolled diabetes, peripheral neuropathy ≥ grade 2, pre-existing significant skin disorders | `…9-4-2-patients-eligible-for-combination-therapy-but-not-elig.01.1231` |

### C. Prerequisites (12)

| # | Rule | Source chunk |
|---|---|---|
| P1 | Assess comorbidity with a **validated score** (e.g. Charlson) before deciding | `…01.1148` |
| P2 | **Screen for frailty and cognitive impairment**; provide CGA where optimisation is needed | `…01.1148` |
| P3 | Base bladder-sparing vs RC in older/frail patients on **tumour stage and frailty** | `…01.1148` |
| P4 | Manage all TMT candidates in a **multidisciplinary team** setting | `…6-1-1-recommendations-for-a-multidisciplinary-team.01.1143` |
| P5 | **Fully inform the patient** of all alternatives before radical cystectomy | `…01.1143` |
| P6 | Perform **restaging CT after neoadjuvant therapy** before proceeding | `…7-1-1-recommendation-for-response-to-neoadjuvant-therapy.01.1213` |
| P7 | Perform **LND as an integral part** of radical cystectomy | `…01.1178` |
| P8 | Test **FGFR3 alterations** to select for erdafitinib | `…4-9-6-summary-of-evidence-and-recommendations-for-urothelial.01.1128` |
| P9 | Determine **HER2 IHC** to select for HER2-directed ADC | `…01.1128` |
| P10 | Sexual-preserving techniques **only in eligible, highly motivated** patients; select on organ-confined disease and absence of tumour in the preserved area | `…6-7-2-a-3…01.1172`, `…6-7-2-b-3…01.1176` |
| P11 | Offer **pharmacological VTE prophylaxis** (extended LMWH) after RC | `…01.1194` |
| P12 | Advise TMT candidates that **post-treatment bladder monitoring is essential** | `…6-8-1-g-summary-of-evidence-and-recommendations-for-trimodal.01.1203` |

---

## Two findings that change existing work

### 1. Rule 1's threshold is wrong, by one boundary

`safety-rules-from-dossiers.md` Rule 1 says `eGFR < 60`. The guideline says:

> *"At least one of the following criteria must be present: PS > 1; **GFR ≤ 60mL/min.**; grade
> ≥ 2 audiometric hearing loss; grade ≥ 2 peripheral neuropathy or NYHA class III"*

**≤ 60, not < 60.** A patient with a clearance of exactly 60 is unfit for cisplatin by the
guideline and would not trigger the rule as written. The extraction prompt's own instruction
covers this: *"Do not soften or harden the threshold."*

### 2. Rule 1 collapses three bands into one

EAU defines **three** platinum tiers, not two, with two cut points and non-renal criteria that
Rule 1 omits entirely:

    fit for cisplatin          GFR > 60, PS ≤ 1, no ≥grade-2 hearing loss or neuropathy, not NYHA III
    fit for carboplatin only   GFR ≤ 60 (EORTC: < 60) and/or PS 2
    unfit for any platinum     GFR < 30 and/or PS > 2

Dossier 1 (GFR 28) is in the **third** band — unfit for any platinum-based chemotherapy — not
merely cisplatin-ineligible. Dossier 2 (GFR 30) sits exactly on that boundary. Rule 1 as
written cannot express that difference, and the difference is what a board would act on.

**Neither correction has been applied.** Both are recorded here for the clinician review.

---

## Sizing: the number that decides the next step

**33 candidate rules from one guideline**, against a threshold of 8 for "rescope".

Extrapolating honestly, rather than by multiplying by 37:

| basis | estimate |
|---|---|
| EAU MIBC share of eligible corpus | 178 / 2,612 = **6.8%** |
| uniform rule density | 33 / 0.068 ≈ **485 raw extractions** |
| adjusted — 17 treatment guidelines are rule-dense; 20 classification, measurement and specialist documents are not | **250–400 raw** |
| after deduplication — AUA, NCCN, ESMO and AFU state the same platinum-eligibility criteria in their own words | **80–150 distinct rules** |

The deduplication factor is the load-bearing assumption and it is not yet measured. It could be
checked cheaply by extracting the platinum-eligibility rule from AUA MIBC and NCCN bladder and
seeing whether the three agree on the number.

### Not all 33 are retrieval-safety rules

Sorting by what each rule could actually govern:

| kind | count | governs |
|---|---|---|
| **patient-state gating** — T1–T5, T9–T12, C1, C9, P3 | **13** | whether a retrieved passage is safe for *this* patient. These are the Part 2 kind. |
| **process and quality** — T6, T7, T8, C2, C3, C7, C8, P4, P5, P7, P11 | 11 | whether the *plan* is sound. These belong to the safety reviewer auditing a proposal, not to passage filtering. |
| **testing prerequisites** — P1, P2, P6, P8, P9, P10, P12 | 9 | what must be documented or measured before a decision. Closest to the completeness rules. |

So the retrieval-filtering catalogue — the one that populates `forbidden_chunk_ids` — is
roughly **40% of the extraction**, or an estimated **30–60 distinct rules** across the corpus.
The other 60% is not waste: it is the material the safety reviewer needs, and it arrives free
with the same pass.

---

## Recommendation

**Rescope, in three ways.**

1. **Extract only from the 17 position-bearing documents**, not all 37. Classification and
   measurement standards (WHO-IARC, PI-RADS, RECIST) state no patient-gating rules, and
   specialist guidance largely restates the treatment guidelines.
2. **Extract by section, not by document.** Recommendation-bearing sections and
   eligibility-criteria prose yielded all 33 rules from 41 of 178 passages. That selection is
   structural and repeatable.
3. **Deduplicate before clinical review.** A clinician reviewing 400 raw extractions is a
   project; reviewing 30–60 distinct rules, each with the three or four guidelines that state
   it, is a session — and the disagreements between guidelines are the interesting part.

### The deduplication factor was measured, and it is weaker than assumed

The platinum-eligibility rule was extracted from AUA MIBC 2024 and NCCN bladder 3.2026 and
compared with T1. **The three guidelines do not duplicate. They complement.**

| source | what it contributes | chunk |
|---|---|---|
| **EAU** | the threshold: *"GFR ≤ 60mL/min."*, with PS > 1, grade ≥ 2 hearing loss, grade ≥ 2 neuropathy, NYHA III | `…01.1229` |
| **AUA** | the criteria, no number: *"eligibility for cisplatin-based NAC should be based on comorbidities and PS, including cardiac status and presence of peripheral neuropathy, hearing loss, and renal dysfunction"* | `aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.03.0434` |
| **NCCN** | the measurement procedure: *"Estimate glomerular filtration rate (GFR) to assess eligibility for Cisplatin"*, and — *"For patients with borderline GFR, consider timed urine collection, which may more accurately determine eligibility for Cisplatin"* | `nccn.bladder.3.2026.…muscle-invasive-bladder-cancer.01.2467`, `…adjuvant-treatment.01.2468` |

So the merged rule is richer than any single source, and citing all three is exactly what the
reasoning appendix is for. But it means **deduplication will not collapse 400 extractions into
80** — the estimate above was optimistic. A more honest range is **150–250 distinct rules**,
with merging rather than discarding as the main saving.

**One of these has immediate clinical force.** NCCN's borderline-GFR caveat applies directly to
**dossier 2, whose GFR is exactly 30** — sitting precisely on the unfit-for-any-platinum
boundary of T3. NCCN says an estimated GFR is not good enough at that boundary and a timed
urine collection should be considered. No rule in `safety-rules-from-dossiers.md` expresses
this, and it would change what the board is told about a real patient.

**Recommended next probe, if one is wanted before committing:** repeat this three-way comparison
for one more rule — the neobladder contraindication (C1) is a good candidate, being crisp and
surgical — to see whether "complement rather than duplicate" holds generally or is specific to
platinum eligibility.
