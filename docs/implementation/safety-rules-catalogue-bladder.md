# Merged safety-rule catalogue — bladder

**Status**: extraction complete for the **9 position-bearing bladder documents**. Prostate is
not yet extracted. Every rule is `clinician_validation_status: pending`.

**Method**: the rescoped extraction from `safety-rules-from-dossiers.md` Step 3 — position-bearing
documents only, sections selected structurally (recommendation-bearing paths, decision tables,
AUA numbered statements, evidence-grade markers), then filtered to passages stating a threshold,
a contraindication or a prerequisite. **164 passages read** of 855 eligible across the nine.

**One rule per clinical condition, carrying every guideline that states it.** Where the
guidelines disagree, the disagreement is preserved in the rule rather than resolved — that is
the point of merging, and two of the disagreements below are clinically material.

Sources: `afu.bladder-tvim.2024-2026`, `afu.bladder-tvnim.2024-2026`, `aua.bladder-mibc.2024`,
`aua.bladder-nmibc.2024`, `eau.bladder-mibc.2026`, `eau.bladder-nmibc.2026`,
`eau-esmo.bladder-advanced-variant.2020`, `esmo.bladder.2022`, `nccn.bladder.3.2026`.

---

## The two divergences that change existing rules

### D1. The cisplatin renal boundary is not one number, and Rule 1a is too absolute

Four guidelines state this differently, and the difference falls exactly on the band Rule 1a
governs:

| source | what it says | citation |
|---|---|---|
| **EAU** | unfit for cisplatin if **GFR ≤ 60** (or PS > 1, grade ≥ 2 hearing loss, grade ≥ 2 neuropathy, NYHA III) | `eau.bladder-mibc.2026.9-3-…01.1229` |
| **ESMO** | *"Gemcitabine and cisplatin **can be considered** for patients otherwise fit without comorbidities, a good performance status (0-1) and a **creatinine clearance between 50 and 60 ml/min**"* [III, B] | `esmo.bladder.2022.advanced-or-metastatic-uc-in-patients-not-eligible-f…` |
| **AFU** | NAC recommended for patients able to receive it — *"OMS 0–1, **DFG ≥ 50–60 mL/min**, absence d'hypoacousie sévère, absence d'insuffisance cardiaque sévère, absence de neuropathie périphérique sévère"* | `afu.bladder-tvim.2024-2026.chimiotherapie-neoadjuvante-a-base-de-cisplatine.01.0108` |
| **AUA** | the criteria, no number — *"comorbidities and PS, including cardiac status and presence of peripheral neuropathy, hearing loss, and renal dysfunction"* | `aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.03.0434` |
| **NCCN** | estimate GFR; *"For patients with borderline GFR, consider timed urine collection"* | `nccn.bladder.3.2026.2026.adjuvant-treatment.01.2468` |

**EAU calls a clearance of 55 unfit. ESMO and AFU both say cisplatin may still be given there
to an otherwise-fit patient with PS 0–1.** Rule 1a currently states the contraindication
without that carve-out, so for a patient with GFR 50–60, PS 0–1 and no comorbidity it would
flag a passage that two of four guidelines support.

**Proposed correction to Rule 1a** — split the band, do not resolve the disagreement:

    50 <= eGFR <= 60, PS 0-1, no comorbidity   contested: EAU says unfit, ESMO and AFU
                                               say cisplatin may be considered. Surface both.
    30 <= eGFR < 50                            cisplatin contraindicated by all four
    eGFR < 30 or PS > 2                        unfit for any platinum (Rule 1b, unchanged)

Dossier 2 (eGFR 30) is unaffected — it sits in the second band, where the guidelines agree.

### D2. Carboplatin is not a neoadjuvant substitute, and Rule 1a implies it is

> **AUA**: *"There is insufficient data to recommend non-cisplatin-based regimens as either NAC
> or adjuvant chemotherapy (AC) for MIBC… there is no high level evidence that carboplatin-based
> regimens lead to increased survival in this setting."*
> `aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.04.0435`

> **ESMO**: *"Carboplatin-based ChT is recommended in patients unfit for cisplatin [I, A]"* —
> stated in the **advanced/metastatic** chapter.

Both are true and they are about different settings. Rule 1a's reason text — *"Carboplatin-based
or split-dose cisplatin regimens are alternatives"* — is correct for **metastatic** disease and
**unsupported for neoadjuvant MIBC**. A cisplatin-ineligible patient with non-metastatic MIBC
should be routed to trimodality therapy or cystectomy without NAC, not to carboplatin NAC.

**Proposed correction**: make Rule 1a's reason conditional on `disease_setting`.

### D3. EAU and AFU disagree on whether BCG contraindications are absolute or relative

> **EAU**: *"**Absolute contraindications** of BCG intravesical instillation are: during the
> first two weeks after TURBT; in patients with visible haematuria; after traumatic
> catheterisation; and in patients with symptomatic urinary tract infection."*
> `eau.bladder-nmibc.2026.7-10-recommendations-for-adjuvant-therapy-in-tat1-tu…`

> **AFU** puts almost the same list under **`Contre-indications > Relatives`** — *"Infection des
> voies urinaires symptomatique. Absence ou incertitude de l'intégrité de l'urothélium
> (hématurie macroscopique, sondage traumatique, les 2 à 4 semaines qui suivent un geste sur le
> bas appareil urinaire)"* — and reserves **`Formelles`** for a different list entirely.
> `afu.bladder-tvnim.2024-2026.formelles.01.0163`, `…relatives.01.0164`

**The same clinical facts carry different force in the two guidelines**, and AFU's window is
2–4 weeks where EAU's is 2. This is the kind of divergence a board should see, not have resolved
for them.

---

## A. Numeric thresholds (bladder)

| # | Rule | Value | Sources |
|---|---|---|---|
| BT1 | Cisplatin ineligibility | GFR ≤ 60 (EAU) / 50–60 contested (ESMO, AFU) — see **D1** | EAU, ESMO, AFU, AUA, NCCN |
| BT2 | Unfit for any platinum | GFR < 30 and/or PS > 2 | EAU §9.5 |
| BT3 | Eligible for EV+pembrolizumab | ECOG PS 0–2 and GFR ≥ 30 | EAU §9.3 |
| BT4 | PD-L1 for first-line pembrolizumab when cisplatin-unfit | CPS ≥ 10, Dako 22C3 | EAU §9.5 |
| BT5 | Do not delay radical cystectomy | > 3 months (unless receiving NAC) | EAU §6.7.4.a |
| BT6 | Minimum institutional volume | ≥ 20 RC per hospital per year | EAU §6.7.9 |
| BT7 | Nodal size criteria on CT | short axis > 8 mm pelvic, > 10 mm retroperitoneal | AFU TVIM |
| BT8 | Immediate post-TURBT instillation window | within 24 h | AUA NMIBC st. 15 |
| BT9 | TMT radiotherapy schedule | 55 Gy / 20 fractions lower locoregional recurrence than 64 Gy / 32 | AFU TVIM |
| BT10 | Preoperative RT dose where used | 45–50 Gy in 1.8–2 Gy fractions | EAU §6.6.3 |
| BT11 | Adjuvant chemotherapy population | pT3, pT4 and/or N+ without neoadjuvant treatment | EAU §6.5.2.c |
| BT12 | Adjuvant RT population | pT3b–4, positive nodes, or positive margins | EAU §6.6.3 |
| BT13 | Radical cystectomy indication | T2–T4a N0 M0 | EAU §6.7.1.d, ESMO |
| BT14 | First surveillance cystoscopy after NMIBC treatment | within 3–4 months | AUA NMIBC st. 32, EAU NMIBC |
| BT15 | MIBC follow-up imaging | 6–12 month intervals for 2–3 years, then annually (AUA); 3–4 monthly for first 2 years, 5 years total (ESMO) | AUA st. 30, ESMO — **divergent intensity** |
| BT16 | Post-cystectomy laboratory follow-up | 3–6 month intervals for 2–3 years, then annually — renal function declines with diversion | AUA st. 31 |
| BT17 | Neobladder and age | age is not a formal contraindication, but rarely performed after 80 | AFU TVIM |
| BT18 | BCG-unresponsive definition | persistent/recurrent HG within 12 months of adequate BCG (two induction courses, or one induction plus one maintenance) | AUA NMIBC st. 26, ESMO |

## B. Contraindications (bladder)

| # | Rule | Sources |
|---|---|---|
| BC1 | Do not offer orthotopic neobladder when there is **invasive** tumour in the urethra or at the level of urethral dissection — non-invasive tumour including CIS is **not** a contraindication (AFU) | EAU §6.7.5.d, AFU TVIM |
| BC2 | BCG contraindications — see **D3** for the absolute/relative split. AFU `Formelles`: prior systemic BCG reaction (organ infection or sepsis), severe immunodeficiency, active radiation cystitis, active tuberculosis (a *history* of TB is not a contraindication), pregnancy and breastfeeding | AFU TVNIM, EAU NMIBC |
| BC3 | Leucocyturia, microscopic haematuria and asymptomatic bacteriuria are **not** BCG contraindications and need no delay | AFU TVNIM |
| BC4 | Do not use postoperative intravesical chemotherapy after suspected perforation or extensive resection | AUA NMIBC st. 15 |
| BC5 | Do not offer radiotherapy alone as curative treatment for MIBC | AUA st. 24, EAU §6.9.3 |
| BC6 | Do not offer chemotherapy alone as primary therapy for localised bladder cancer | EAU §6.10.2.a |
| BC7 | Do not offer TURBT alone as curative treatment | EAU §6.10.1.a |
| BC8 | Medically fit patients consenting to RC should not undergo partial cystectomy or maximal TURBT as primary curative therapy | AUA st. 23 |
| BC9 | Do not perform radical cystectomy for Ta low- or intermediate-risk disease until bladder-sparing modalities have failed | AUA NMIBC st. 27 |
| BC10 | Do not offer preoperative radiotherapy for operable MIBC | EAU §6.6.3 |
| BC11 | Do not offer preoperative bowel preparation | EAU §6.7.5.d |
| BC12 | Do not perform extended LND — no survival benefit, increased morbidity (EAU). **AUA differs**: standard LND is the floor, extended not prohibited | EAU §6.7.3.a vs AUA st. 20 — **divergent** |
| BC13 | ASA score must not be used for comorbidity assessment in this setting | EAU §6.4.1 |
| BC14 | Do not use urinary biomarkers in place of cystoscopy in NMIBC surveillance | AUA NMIBC st. 9 |
| BC15 | Enfortumab vedotin not for uncontrolled diabetes, peripheral neuropathy ≥ grade 2, pre-existing significant skin disorders | EAU §9.4.2 |
| BC16 | Sexual preservation must never compromise the oncological quality of surgery | AFU TVIM |

## C. Prerequisites (bladder)

| # | Rule | Sources |
|---|---|---|
| BP1 | Assess comorbidity with a validated score (Charlson) before deciding treatment | EAU §6.4.1 |
| BP2 | Screen for frailty and cognitive impairment; provide CGA where optimisation is needed | EAU §6.4.1 |
| BP3 | Base bladder-sparing vs radical cystectomy in older/frail patients on tumour stage and frailty, not age | EAU §6.4.1, AFU TVIM, SIOG |
| BP4 | Full history and physical including examination under anaesthesia at TURBT, before treatment consideration | AUA st. 1 |
| BP5 | Complete staging — chest imaging and cross-sectional abdomen/pelvis with IV contrast **if not contraindicated** — before MIBC management | AUA st. 2, ESMO |
| BP6 | Manage all trimodality candidates in a multidisciplinary team setting | EAU §6.1.1, ESMO |
| BP7 | Fully inform the patient of all alternatives before radical cystectomy; discuss ileal conduit, continent cutaneous and orthotopic neobladder | EAU §6.1.1, AUA st. 13, AFU TVIM |
| BP8 | Perform bilateral pelvic lymphadenectomy at any curative-intent surgery — minimum external and internal iliac and obturator nodes | AUA st. 19–20, EAU §6.7.3.a |
| BP9 | Perioperative pharmacological thromboembolic prophylaxis for radical cystectomy | AUA st. 16, EAU §6.7.9 |
| BP10 | Optimise performance status perioperatively; enrol in an enhanced-recovery protocol | AUA st. 15, AFU TVIM (RAAC) |
| BP11 | Preoperative optimisation before cystectomy — smoking cessation, chronic disease control (HbA1c, COPD, cardiovascular), **nutritional preparation, treatment of anaemia**, physiotherapy, psycho-social needs | AFU TVIM RAAC table |
| BP12 | Assess sexual function before surgery to guide technique — IIEF-5 in men, FSFI in women | AFU TVIM |
| BP13 | Frozen section of ureteral margins is **not** routine — indicated for hydronephrosis, multifocal tumour, or associated CIS. Urethral margin frozen section when neobladder planned | AFU TVIM |
| BP14 | Restaging CT after neoadjuvant therapy before proceeding | EAU §7.1.1 |
| BP15 | Reassess tumour status 2–3 months after treatment completion | NCCN |
| BP16 | Repeat TURBT when initial resection was incomplete, or when detrusor muscle is absent from the specimen (except Ta LG/G1) | AUA NMIBC st. 12, EAU NMIBC §5.15 |
| BP17 | Prostatic urethral biopsy and upper-tract evaluation before further intravesical therapy, in persistent/recurrent disease or positive cytology | AUA NMIBC st. 22, EAU NMIBC |
| BP18 | Expert genitourinary pathologist review where variant histology is suspected | AUA NMIBC st. 6 |
| BP19 | Variant histology may require divergence from standard urothelial management | AUA MIBC st. 35, EAU-ESMO consensus |
| BP20 | Test FGFR3 alterations to select for erdafitinib; determine HER2 IHC for HER2-directed ADC | EAU §4.9.6, NCCN |
| BP21 | Assign a risk category (low / intermediate / high) at every occurrence and recurrence, using a validated calculator (EAU 2021 or EORTC) | AUA NMIBC st. 5, EAU NMIBC §6.5, NCCN |
| BP22 | Counsel smokers to stop smoking | EAU NMIBC §7.10 |
| BP23 | Discuss coping; offer support group or individual counselling | AUA MIBC st. 33 |

---

## Counts

| | |
|---|---|
| documents extracted | 9 of 9 bladder position-bearing |
| passages read | 164 |
| numeric thresholds | 18 |
| contraindications | 16 |
| prerequisites | 23 |
| **total merged rules** | **57** |
| divergences preserved | 5 (D1, D2, D3, BT15, BC12) |

**Merging worked.** The nine documents produced roughly 90 raw statements that collapsed to 57
distinct rules — a factor of about 1.6, not the 3–4 hoped for. The saving is real but modest,
and it is concentrated where several guidelines say the same thing about surgery. The
disagreements cluster where it matters most: drug eligibility thresholds.

**Extrapolating to prostate**: 9 bladder documents gave 57 rules from 855 eligible passages. The
9 prostate documents hold 1,165 eligible passages, so a similar density suggests **60–80 more**,
for a corpus total of **120–140 distinct rules** — inside the 150–250 estimate, at the low end.

## Still to do

- Extract the 9 prostate position-bearing documents
- Annotate each rule with which of the 15 dossiers trigger it
- Apply the D1 and D2 corrections to Rules 1a and 1b
- Clinical review of all 57, with the five divergences flagged for a decision
