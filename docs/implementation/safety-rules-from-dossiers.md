# Safety rules for patient-specific retrieval

**Status**: Design reviewed and revised. Ready for the reordered implementation below.

---

## Important: what the 15 dossiers are and are not

### The dossiers are a small sample — 15 patients, 8 prostate and 7 bladder

They are enough to show which safety rules matter most for the patients this system actually
sees. They are not enough to define every safety rule the system needs.

A rule like "flag cisplatin passages when GFR < 60" is correct and generalizable — the
guideline states the threshold, and dossiers 1 and 2 happen to trigger it. But a rule about
moderate malnutrition (MNA 17–23.5) would not have appeared because no dossier patient fell
in that range. A rule about albumin < 35 as an independent surgical risk factor would not
have appeared because the dossiers rarely recorded albumin. Rules about drug interactions
beyond imatinib, about ECOG performance status, about prior radiation fields limiting
surgical options — none of these appeared in the sample.

**The 10 rules below are a floor, not a ceiling.** They are the rules that happen to fire on
at least one of the 15 patients. There are certainly more rules in the guidelines that would
fire on patient 16.

### The correct relationship between guidelines and dossiers

| Source | Role |
|--------|------|
| **Guidelines** | Define the rules. Every threshold, contraindication, and prerequisite stated in the guideline text is a candidate rule. This is the comprehensive source. |
| **Dossiers** | Prioritize the rules. A rule that fires on 3 of 15 patients is urgent. A rule that fires on none is still valid — it fires on patient 16. |
| **Dossiers** | Provide test cases. Each rule gets one test per dossier where it should fire and one where it should not. |

The rules should be extracted from the guidelines, not inferred from the dossiers. The
dossiers validate that the extraction is correct and tell you which rules to implement first.

---

## Part 1: Patient summaries

These summaries exist so that every rule can be tested against the specific patients who
should trigger it and the specific patients who should not.

### Bladder patients (7 dossiers)

| Dossier | Age | GFR | MNA | G8 | ADL | Alb | Hb | Stage | Histology | Key concern |
|---------|-----|-----|-----|----|-----|-----|-----|-------|-----------|-------------|
| 1 | 63 | **28** | 11.5 | — | 3 | 30 | 7.8 | pT4 | Epidermoid | GFR 28 — cisplatin fatal. Hb 7.8 — anemia. |
| 2 | 85 | **30** | **7** | **5** | **4** | 35 | 11.6 | pT2 | Urothelial HG | GFR 30 + MNA 7 + G8 5 + ADL 4 — frail, malnourished, renal-impaired. |
| 10 | 56 | — | — | — | — | — | — | pTa LG | Urothelial LG | Low-risk NMIBC. No labs available. |
| 11 | 64 | — | — | — | — | — | — | pT1 LG | Urothelial LG | Low-grade NMIBC. No labs. |
| 13 | 79 | 85 | — | — | — | — | 16.2 | pT4 | Urothelial HG | GFR OK. Nodal mets. Nodules in lung. |
| 14 | 79 | — | — | — | — | — | 12.2 | pT2 | Urothelial HG | No GFR. Hb mildly low. |
| 15 | 71 | 65 | 23.5 | — | 6 | 44 | 9.0 | pT1 min | Urothelial LG | 18 prior resections. Hb 9.0. Recurrent NMIBC. |

### Prostate patients (8 dossiers)

| Dossier | Age | GFR | MNA | Gleason | ISUP | PSA | Stage | Key concern |
|---------|-----|-----|-----|---------|------|-----|-------|-------------|
| 3 | 70 | — | 23.5 | 3+4 | 2 | 7.5 | PIRADS 5, organ-confined | Intermediate risk. |
| 4 | 72 | — | 23.5 | 3+4 | 2 | 5.1 | PIRADS 4 | GIST on imatinib — drug interaction concern. |
| 5 | 70 | — | 23.5 | 3+4 | 2 | 8.9 | PIRADS 5 | Hb 9.2 — anemia before treatment. |
| 6 | 59 | 114 | 23.5 | 5+4 | 5 | 116 | Metastatic, bone mets | High-volume metastatic. |
| 7 | 72 | — | 23.5 | 4+3 | 3 | 34 | PIRADS 5 | Unfavorable intermediate. |
| 8 | 61 | — | 15.5 | 5+4 | 5 | 73 | Relapse, progressing on ADT+abiraterone+docetaxel | Castration-resistant. MNA 15.5 — borderline nutrition. |
| 9 | 70 | — | — | 4+3 | 3 | 34 | T3a, PIRADS 5 | Locally advanced. Missing labs. |
| 12 | 70 | — | — | 4+4 | 4 | 12 | Metastatic, PSMA+ bone mets | High-risk metastatic. |

---

## Part 2: Initial safety rules

These rules were identified by reading the 15 dossiers and asking: for each patient, what
guideline-stated threshold or contraindication would a clinician check before accepting a
retrieved passage as safe?

**Every rule below needs guideline verification.** The thresholds are stated as they appear in
the source documents, but the LLM extraction step must confirm each one against the exact
guideline text. A rule the guideline does not support is removed. A rule the guideline states
differently is corrected.

Each rule has: a condition (patient state), a passage pattern (what text matters), an action
(flag or exclude), a reason, and a source.

### Sourced rules (guideline-derived)

Rules 1–6 are derived from stated guideline thresholds. Rules U1–U3 are in the unsourced
list below.

#### Rule 1a: Cisplatin contraindicated

- **Condition**: `cancer_type = bladder AND 30 <= eGFR < 50` — **or** `50 <= eGFR <= 60 AND (performance_status >= 2 OR significant_comorbidity)`
- **Passage pattern**: text recommends cisplatin-based chemotherapy AND does not address renal eligibility
- **Action**: **flag**
- **Reason**: "Cisplatin-based chemotherapy is contraindicated at this renal function. Patient eGFR: {value}, PS {value}. In metastatic disease, carboplatin-based regimens are an alternative. In non-metastatic MIBC, there is insufficient evidence for carboplatin as neoadjuvant therapy — consider trimodality therapy or cystectomy without NAC."
- **Sources**: EAU MIBC 2026 §9.3 (`eau.bladder-mibc.2026.9-3-first-line-systemic-therapy-for-metastatic-disease-patie.01.1229`); AUA MIBC 2024 for the criteria (`aua.bladder-mibc.2024.neoadjuvant-adjuvant-chemotherapy.03.0434`); AFU TVIM (`afu.bladder-tvim.2024-2026.chimiotherapie-neoadjuvante-a-base-de-cisplatine.01.0108`)
- **Test dossiers**: none of the 15 fall in 30–49 — dossier 2 at exactly 30 does (30 <= 30 < 50, **must fire**); 15 (GFR 65 — must NOT fire), 13 (GFR 85 — must NOT fire)

**The carboplatin advice is conditional on setting, and that correction matters.** AUA states
*"there is insufficient data to recommend non-cisplatin-based regimens as either NAC or adjuvant
chemotherapy (AC) for MIBC"* (`…neoadjuvant-adjuvant-chemotherapy.04.0435`), while ESMO recommends
carboplatin for cisplatin-unfit patients in its **advanced/metastatic** chapter. Both are true of
different settings. The earlier single-sentence advice would have routed a non-metastatic MIBC
patient toward a regimen with no evidence behind it.

#### Rule 1a-contested: Cisplatin at eGFR 50–60 — the guidelines disagree

- **Condition**: `cancer_type = bladder AND 50 <= eGFR <= 60 AND performance_status <= 1 AND no significant comorbidity`
- **Passage pattern**: text recommends cisplatin-based chemotherapy
- **Action**: **flag**, and surface **both** positions
- **Reason**: "Patient eGFR is {value}, in the band where the guidelines disagree. EAU classifies eGFR ≤ 60 as unfit for cisplatin. ESMO states gemcitabine-cisplatin *may be considered* at a clearance of 50–60 in an otherwise fit patient with PS 0–1 and no comorbidity, and AFU sets its threshold at DFG ≥ 50–60. This is a genuine divergence between guidelines, not an error — the board should decide."
- **Sources**: EAU §9.3 (`…01.1229`) against ESMO (`esmo.bladder.2022.advanced-or-metastatic-uc-in-patients-not-eligible-for-cispl…`) and AFU (`afu.bladder-tvim.2024-2026.chimiotherapie-neoadjuvante-a-base-de-cisplatine.01.0108`)
- **Test dossiers**: none of the 15 currently fall in 50–60 — the nearest is dossier 15 at 65. **The rule is kept because it fires on patient 16**, and because a board told "cisplatin is contraindicated" at a clearance of 55 would be told something two of four guidelines contest.

**Why this is a rule and not a resolution.** The divergence is preserved rather than settled by
whoever wrote the rule. A clinician seeing *"EAU says unfit, ESMO and AFU say it may be
considered"* can decide; a clinician seeing only *"contraindicated"* cannot, and would not know
there was anything to decide.

#### Rule 1b: Unfit for any platinum-based chemotherapy

- **Condition**: `cancer_type = bladder AND (eGFR < 30 OR performance_status > 2)`
- **Passage pattern**: text recommends **any** platinum-based chemotherapy — cisplatin *or* carboplatin — AND does not address renal eligibility
- **Action**: **flag**
- **Reason**: "Patient is unfit for any platinum-based chemotherapy (eGFR {value}, PS {value}). Consider non-platinum alternatives — pembrolizumab, enfortumab vedotin, radiotherapy alone, or best supportive care per geriatric assessment."
- **Source**: EAU MIBC 2026 §9.5 — *"severely impaired PS (PS > 2) and/or severely impaired renal function (**GFR < 30mL/min.**) or inadequate organ function"* (`eau.bladder-mibc.2026.9-5-first-line-therapy-in-patients-not-eligible-for-combinat.01.1235`)
- **Test dossiers**: 1 (GFR 28 — must fire), 2 (GFR 30 — must NOT fire, falls in the carboplatin band)

**Why the split matters, and it is the most consequential correction so far.** The single Rule 1
said *"cisplatin contraindicated, consider carboplatin"*. For dossier 1, at eGFR 28, carboplatin
is contraindicated too — so the rule as written would have given **actively wrong advice** to a
real patient, in the direction of offering a therapy he cannot receive. A rule that fires
correctly and then recommends the wrong alternative is worse than no rule.

**A threshold discrepancy, resolved toward the guideline.** The instruction that produced this
split specified Rule 1b as `eGFR <= 30`, while also stating that dossier 2 (eGFR exactly 30)
should trigger 1a. Those two cannot both hold — 30 satisfies `<= 30`. EAU's own text says
**`GFR < 30mL/min.`**, which resolves it in favour of the stated dossier assignment and leaves
the bands gapless:

    unfit for any platinum   GFR < 30
    carboplatin only         30 <= GFR <= 60
    fit for cisplatin        GFR > 60

`< 30` is what is implemented, on the same principle that caught the first error: do not soften
or harden the threshold.

#### Rule 1c: Confirm a borderline GFR before deciding platinum eligibility

- **Condition**: `cancer_type = bladder AND 30 <= eGFR <= 60`
- **Passage pattern**: fires on the **patient**, not on a passage — an annotation added whenever a platinum decision is in scope
- **Action**: **flag**
- **Reason**: "Patient eGFR is {value}, close to the platinum eligibility boundary. NCCN recommends timed urine collection to confirm GFR before deciding cisplatin eligibility, as an estimate may be inaccurate at this range."
- **Source**: NCCN bladder 3.2026 — *"For patients with borderline GFR, consider timed urine collection, which may more accurately determine eligibility for Cisplatin"* (`nccn.bladder.3.2026.2026.adjuvant-treatment.01.2468`), and *"Estimate glomerular filtration rate (GFR) to assess eligibility for Cisplatin"* (`…2026.muscle-invasive-bladder-cancer.01.2467`)
- **Test dossiers**: 2 (GFR 30, exactly on the 1a/1b boundary — must fire), 1 (GFR 28 — must NOT fire, already unambiguously unfit), 13 (GFR 85 — must NOT fire)

**This rule exists because a real patient sits on the boundary.** Dossier 2's eGFR is exactly 30,
the cut point between "carboplatin available" and "unfit for any platinum". The two rules give
opposite advice, and NCCN says the measurement that decides between them is not reliable enough
at that value. No rule in the original set expressed this, and it changes what the board is told
about that patient.

#### Rule 2: Nutritional status gates major surgery

- **Condition**: `disease_setting = bladder_mibc_non_metastatic AND MNA < 17`
- **Passage pattern**: text recommends radical cystectomy AND does not address preoperative nutritional optimization
- **Action**: **flag**
- **Reason**: "Patient MNA is {value} (severe malnutrition: <17). Preoperative nutritional optimization is indicated per ESPEN/ASPEN guidelines before radical cystectomy."
- **Source**: ESPEN nutrition-cancer.2021, ESPEN nutrition-surgery.2021, ASPEN practice tools — verify exact MNA threshold
- **Test dossiers**: 2 (MNA 7 — must fire), 1 (MNA 11.5 — verify whether threshold is <17 or a higher value)

#### Rule 3: Geriatric assessment gates major surgery

- **Condition**: `age ≥ 75 AND (G8 ≤ 14 OR ADL < 6) AND disease_setting = bladder_mibc_non_metastatic`
- **Passage pattern**: text recommends radical treatment AND does not address geriatric assessment or frailty
- **Action**: **flag**
- **Reason**: "Patient is {age} years old with G8 score {value}. Comprehensive geriatric assessment is indicated before radical treatment per SIOG/EAU guidelines."
- **Source**: SIOG bladder.2020, SIOG geriatric-screening.2015, EAU MIBC §6.2-6.4 — verify G8 threshold
- **Test dossiers**: 2 (age 85, G8 5, ADL 4 — must fire), 13 (age 79, G8 missing — behavior when value absent TBD)

#### Rule 4: Active surveillance contraindicated in high-risk prostate cancer

- **Condition**: `cancer_type = prostate AND (Gleason ≥ 8 OR ISUP ≥ 4 OR PSA > 20 OR clinical_stage ≥ T3)`
- **Passage pattern**: text describes active surveillance as an option AND does not state the contraindication for high-risk disease
- **Action**: **flag** (the clearest candidate for EXCLUDE after measurement confirms it)
- **Reason**: "Active surveillance is contraindicated in high-risk prostate cancer. Patient: Gleason {value}, ISUP {value}."
- **Interaction with SETTING_PARENTS**: AUA's principles-of-active-surveillance passage, routed
  `prostate_nonmetastatic_unspecified_risk`, is now reachable for high-risk questions via parent
  admission. When this rule fires on that passage, the annotation should say: "This passage was
  admitted via parent-setting because it is written across risk bands; it is not specific to
  high-risk disease. Active surveillance is contraindicated for this patient."
- **Source**: EAU prostate.2026, NCCN prostate.6.2026, AUA prostate-localized.2026 — verify exact risk thresholds for AS eligibility
- **Test dossiers**: 6 (Gleason 9, ISUP 5 — must fire), 3 (Gleason 3+4, ISUP 2 — must NOT fire)

#### Rule 5: ADT-naive passages for castration-resistant patients

- **Condition**: `cancer_type = prostate AND patient is on ADT AND PSA is rising`
- **Passage pattern**: text recommends ADT initiation or describes hormone-naive treatment pathway
- **Action**: **flag**
- **Reason**: "Patient is on ADT with rising PSA — castration-resistant. This passage addresses hormone-naive disease."
- **Source**: EAU prostate.2026 §6.7, NCCN prostate.6.2026 — verify exact definition of castration resistance
- **Test dossiers**: 8 (on triptorelin + abiraterone, PSA 73, progressing — must fire)

#### Rule 6: Recurrent NMIBC — prior therapy pathway

- **Condition**: `disease_setting = bladder_nmibc AND number_of_prior_resections > 2`
- **Passage pattern**: text about first-line NMIBC management or BCG-naive pathways
- **Action**: **flag**
- **Reason**: "Patient has had {N} prior resections. Recurrent NMIBC after multiple treatments may require BCG-unresponsive pathway (radical cystectomy vs. clinical trial vs. alternative intravesical agents)."
- **Source**: EAU NMIBC 2026, NCCN bladder 3.2026, AUA NMIBC 2024 — verify BCG-unresponsive definition
- **Test dossiers**: 15 (18 prior resections — must fire), 11 (first presentation — must NOT fire)

### Unsourced rules (require guideline verification before implementation)

These rules were observed in the dossiers but have not been confirmed against guideline text.
They are listed separately because they have not earned the status of the sourced rules above.
Verify each against the guidelines before promoting to the sourced list. If a guideline does
not state the rule, it should either be dropped or marked as a clinical-review flag rather
than a guideline-citable rule.

#### Rule U1: Anemia requires correction before chemotherapy or surgery

- **Condition**: `Hb < 10`
- **Passage pattern**: text recommends chemotherapy or major surgery AND does not address anemia management
- **Action**: **flag**
- **Reason**: "Patient is anemic (Hb {value} g/dL). Anemia correction should be addressed before chemotherapy or major surgery."
- **Source**: Not yet verified — check EAU guidelines, general oncology supportive care
- **Test dossiers**: 1 (Hb 7.8), 5 (Hb 9.2), 15 (Hb 9.0), 13 (Hb 16.2 — must NOT fire)

#### Rule U2: Drug interaction with existing cancer therapy

- **Condition**: `patient has active treatment for another malignancy`
- **Passage pattern**: text recommends systemic therapy without addressing cumulative toxicity or drug interactions
- **Action**: **flag**
- **Reason**: "Patient is on {medication} for {condition}. Potential interaction or cumulative toxicity with proposed treatment should be evaluated."
- **Source**: Not a guideline-derived rule — pharmacological due diligence. May belong as a clinical review flag rather than a guideline-citable rule.
- **Test dossiers**: 4 (on imatinib for GIST)

#### Rule U3: Nephron preservation with bilateral ureteral obstruction

- **Condition**: `bilateral hydronephrosis AND (eGFR declining OR creatinine elevated)`
- **Passage pattern**: text discusses radical cystectomy or urinary diversion without addressing renal preservation
- **Action**: **flag**
- **Reason**: "Patient has bilateral hydronephrosis and impaired renal function. Nephron-preserving strategies and renal recovery should be discussed."
- **Source**: Not yet verified — check EAU MIBC guideline for a stated recommendation vs. clinical inference
- **Test dossiers**: 1 (creatinine 24.75, GFR 28, bilateral UHN), 11 (bilateral UHN, no creatinine)

---

## Part 3: Design decisions (settled before implementation)

### Oracle and treatment must be separate

Rules produce the forbidden labels. `harm@1` is measured on the unfiltered pipeline — the
retrieval path without the safety engine wired in. The engine's effect (how many passages it
flags or removes) is reported as a second number alongside `harm@1`.

If the same engine both defines `forbidden_chunk_ids` and removes those passages before
ranking, `harm@1` is 0.00 by construction — not because the system is safe, but because the
metric and the mechanism are the same object. Currently `harm@1` is 0.00 because nothing is
labelled; after wiring the engine inline it would be 0.00 again for a worse reason.

**The measurement path**: rules → hand-label forbidden_chunk_ids → measure harm@1 on unfiltered pipeline.
**The treatment path**: rules → engine flags passages in the retrieval pipeline → report exclusion count separately.
**These are two paths over the same rules, not one path that consumes its own output.**

### Default action is FLAG, not EXCLUDE

Every rule defaults to FLAG for v1. A FLAG means the passage is shown with a safety
annotation. An EXCLUDE means it is removed.

The corpus's own principle says: "Excluding is not deleting… a passage removed silently and
a passage removed for a stated reason look identical from the outside otherwise." A runtime
EXCLUDE is exactly that silent removal — the agent never sees the passage, cannot cite it,
and no downstream reviewer can notice the absence.

Rule 1 is the clearest example. The output a board wants is: "cisplatin is standard for MIBC;
this patient's GFR of 28 contraindicates it; carboplatin is the alternative." Excluding the
standard-of-care passage makes the first clause unsayable. The agent doesn't reason more
safely — it reasons with a hole.

EXCLUDE is reserved for passages that are affirmatively dangerous rather than merely
inapplicable. Rule 4 (active surveillance for Gleason 9) is the clearest candidate. Which
rules deserve EXCLUDE is a decision for measurement, not design — measure FLAG first, then
promote per-rule based on evidence.

### PROMOTE is cut for v1

An unspecified score boost applied before a reranker that re-scores everything has no
measurable target and muddies attribution. The former Rule 2 (carboplatin promotion when
cisplatin contraindicated) is merged into Rule 1's annotation instead: the flag now says
"consider carboplatin-based or non-cisplatin alternatives."

### Passage-side match is keyword-based, not entailment

The passage pattern is a keyword match with a negation check: "cisplatin" appears AND
"eGFR" or "renal function" or "clearance" does NOT appear in the same passage. This is
regex, not semantic understanding. It catches the obvious case — a passage that recommends
cisplatin without mentioning the renal prerequisite. The edge-case test (passage about
cisplatin that also addresses renal function → does NOT fire) is the guard.

This is not body-text routing — the founding principle is about deciding which clinical
situation a passage speaks to. Safety rules are about filtering a correctly-routed passage
for a specific patient. Different problem, different rules.

The passage match runs at query time on the fused candidate list (~50 passages), not on the
full corpus. At that scale and with keyword-based patterns, no offline precomputation table
is needed.

### Safety rules get their own attribution stage

There are now three places a passage can be withheld: corpus exclusion, the routing filter,
and safety rules. The evaluation harness attributes losses to filter / candidate set /
ranking / floor. Add `safety_rule` as a distinct attribution stage so that rule-driven
exclusions are not misattributed to ranking.

---

## Part 4: Build plan (reordered)

The four steps below are ordered by what delivers measurement value fastest, not by what
builds the most code. Step 1 requires no code at all.

### Step 1: Hand-label forbidden_chunk_ids from the sourced rules (do first, today)

Take rules 1, 2, 3, 4, 5, and 6. For each pilot question, identify which passages would
trigger FLAG for which dossiers. Write those chunk_ids into `forbidden_chunk_ids` in the
gold set. Run the evaluation. `harm@1` is now a real number.

This delivers the measurement benefit before any engine exists. It also reframes the
clinician ask: instead of "review 3,532 passages," it becomes "confirm or reject this
shortlist of passages flagged by guideline-derived rules." A far more tractable request.

### Step 2: Size the extraction task (do before extracting all 37 guidelines)

Run the LLM extraction prompt against EAU MIBC 2026 alone — it carries cisplatin
eligibility, comorbidity, frailty, and geriatric assessment, the rules that fire most often.
Count what comes out.

- If one document yields ~8 rules, the full catalogue is ~300 and clinician review is a
  project requiring planning.
- If one document yields ~2 rules, the full catalogue is manageable and clinician review is
  a session.

This determines whether the plan is affordable before committing to the full extraction.

### Step 3: Extract the rescoped rule catalogue

**Step 2 answered the sizing question and the answer forces a rescope.** EAU MIBC 2026 yielded
**33 rules**, against a threshold of eight for "the catalogue is large". The full extraction is
therefore run in a different shape than originally planned. Details and the counts are in
`safety-rules-eau-mibc-extraction.md`.

**Three changes to how the extraction runs.**

**1. Seventeen documents, not thirty-seven.** Only the position-bearing sources — those whose
`source_role` is `treatment_guideline` or `treatment_consensus` — state rules that gate on
patient state. Classification standards (WHO-IARC, PI-RADS), measurement standards (RECIST) and
most specialist guidance either state no such rule or restate the treatment guidelines.

**2. Select by section, not by document.** All 33 EAU rules came from 41 of its 178 eligible
passages, chosen structurally: sections whose path contains `recommendation`, plus the prose
sections stating eligibility criteria and health-status assessment. Reading whole documents
spends most of the effort on narrative evidence — trial results, epidemiology, technique
description — which states no rule. The known cost is recorded: **a rule stated only in body
prose outside a recommendation section will be missed**, and that is the main threat to
completeness.

**3. Merge across guidelines before clinical review, and preserve disagreement.** This is where
the value is, and it was measured rather than assumed. EAU, AUA and NCCN do **not** restate the
same cisplatin rule — they complement each other:

| source | contributes |
|---|---|
| EAU | the threshold — *"GFR <= 60mL/min."* |
| AUA | the criteria without a number — *"comorbidities and PS, including cardiac status and presence of peripheral neuropathy, hearing loss, and renal dysfunction"* |
| NCCN | the procedure — estimate GFR, and confirm a borderline value by timed urine collection |

**One rule carrying three citations is worth more than three rules that appear to disagree.**
Where the guidelines genuinely differ, the difference is preserved in the rule rather than
resolved — that divergence is what a board wants to see, and it is the same principle the
reasoning appendix is built on.

Expected output: **150–250 distinct rules**, each with every guideline that states it, annotated
with which of the 15 dossiers trigger it, and split into sourced and unsourced as in Part 2.

**Roughly 40% of any extraction gates on patient state** and can populate `forbidden_chunk_ids`.
The remainder — process and quality rules such as minimum institutional volume, VTE prophylaxis
and multidisciplinary review, plus testing prerequisites — is not waste. It is the material the
safety reviewer needs to audit a *plan*, and it arrives free on the same pass.

### Step 4: Implement the engine

1. Create `apps/api/rcpia/safety_rules/` with `catalogue.py`, `engine.py`, and
   `test_rules.py`
2. Implement `SafetyEngine.apply(ledger, passages) -> FilteredPassages` using the verified
   rule catalogue, with all actions defaulting to FLAG
3. Write positive, negative, and edge-case tests for every rule
4. Wire the engine into `retrieval.py` between fusion and re-ranking
5. Add `safety_rule` as a distinct attribution stage in the evaluation harness
6. Report engine effect as a separate number alongside `harm@1`, not instead of it

---

## Implementation architecture

### Where rules live

```
apps/api/rcpia/safety_rules/
    __init__.py
    catalogue.py       # Complete rule catalogue (guideline-extracted, verified)
    engine.py          # Applies rules to (ledger, passage) pairs
    test_rules.py      # One test per rule per relevant dossier
```

### Rule schema

```python
@dataclass(frozen=True)
class SafetyRule:
    rule_id: str
    condition: SafetyCondition       # patient state that triggers the rule
    passage_pattern: PassagePattern  # keyword match + negation for passage text
    action: SafetyAction             # flag (default) | exclude (after measurement)
    severity: str                    # contraindication | warning | annotation
    reason_template: str             # human-readable, with {placeholders}
    source_chunk_ids: tuple[str, ...]  # citable guideline passages
    dossier_coverage: tuple[str, ...]  # which dossiers trigger this rule (for testing)
    sourced: bool                      # True if verified against guideline text
```

### Where rules fire in the pipeline

```
Retrieval pipeline:
    ledger → Filter → Keyword + Dense → Fuse → SafetyRules.apply() → Re-rank → Top 10
                                                    │
                                                    ├── flag: add safety_concern to passage metadata
                                                    └── exclude: remove passage (reserved, not default)
```

The safety engine sits between fusion and re-ranking. It receives the fused candidate list
and the patient ledger, applies all rules, and returns:

- The candidate list (possibly with EXCLUDE-flagged passages removed)
- Per-passage safety annotations (flags)
- A count of excluded and flagged passages for attribution

### What the LLM does (offline, once)

1. **Read one guideline first** (EAU MIBC) to size the task
2. **Read all 37 guideline documents** and extract every stated threshold, contraindication,
   and prerequisite as a structured rule
3. **Record the exact source passage and chunk_id** for each rule
4. **Cross-reference against the 15 dossiers** — which rules fire on which patients?

A clinician reviews the extracted rules once. After that, the rules run deterministically —
no LLM is involved at query time.

### What the LLM does NOT do

- Does NOT review passages at query time
- Does NOT decide which passages to exclude for a given patient
- Does NOT generate rules from its own knowledge — every rule must cite a guideline passage in
  the corpus

### Testing

```python
def test_cisplatin_flag_dossier_1():
    """Dossier 1: GFR 28, MIBC. Cisplatin passage must be flagged."""
    ledger = load_ledger("dossier_01")
    passage = Passage(text="...cisplatin-based neoadjuvant chemotherapy...")
    result = engine.apply(rule_cisplatin_gfr, ledger, passage)
    assert result.action == SafetyAction.FLAG

def test_cisplatin_no_flag_dossier_13():
    """Dossier 13: GFR 85, MIBC. Cisplatin passage must NOT be flagged."""
    ledger = load_ledger("dossier_13")
    passage = Passage(text="...cisplatin-based neoadjuvant chemotherapy...")
    result = engine.apply(rule_cisplatin_gfr, ledger, passage)
    assert result.action is None

def test_cisplatin_no_flag_when_passage_addresses_contraindication():
    """A passage that discusses cisplatin AND renal function should not be flagged."""
    ledger = load_ledger("dossier_01")
    passage = Passage(text="...cisplatin is contraindicated when eGFR < 60. For renal impairment, carboplatin...")
    result = engine.apply(rule_cisplatin_gfr, ledger, passage)
    assert result.action is None
```

Every rule needs:
- A positive test (fires on the right patient)
- A negative test (does not fire on a patient who doesn't meet the condition)
- An edge-case test (passage that mentions the triggering text but also addresses the safety concern)

---

## Notes

- **Rules are patient-specific, not corpus-wide.** A cisplatin passage is flagged for a
  patient with GFR 28. The same passage is shown without annotation for a patient with GFR 85.
  The corpus does not change; the filter does.
- **Rules are deterministic and auditable.** Same patient, same passage → same result every
  time. Every flag cites a rule. Every rule cites a guideline passage.
- **Rules are not validated until a clinician reviews them.** The `clinical_status` of the
  rules themselves is `poc_unreviewed`. The safety annotations should carry a visible banner:
  "Safety rule derived from guideline — awaiting clinical validation."
- **Absent measurements are not zero.** If a dossier does not record GFR, the cisplatin rule
  cannot fire — but the system should flag that GFR is missing in the patient summary, because
  its absence prevents a safety check that would otherwise run.
- **MNA 23.5 in four dossiers.** Dossiers 3, 4, 5, and 7 all show MNA 23.5, which is the
  exact cutoff between normal and at-risk on the MNA-SF. Four identical values on a threshold
  may be a form default rather than four measurements. Verify with the clinician before any
  rule keyed on MNA depends on these values.