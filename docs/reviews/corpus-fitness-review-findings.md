# Corpus v5.1 Fitness Review — Findings

**Date**: 27 August 2026  
**Review question**: Is this corpus correctly designed and correctly implemented for the system in Part 1?  
**Corpus state**: v5.1, loaded in PostgreSQL 17 + pgvector. 37 documents, 3,532 passages, 2,612 eligible, 2,512 embedded, 12/12 probes passing, 1,246 tests.  
**Methodology**: Read every document listed in the review prompt (vocabulary.py, specs.json, coverage.json, probes.json, retrieval.py, pilot-findings.md, agent-panel-and-appendix.md, all 37 routing files from prior review); queried the live database for distributional facts; sampled individual passages.

---

## Direct Answer

**The corpus can be used for the agent panel as it is, with one blocking condition.**

The routing architecture is sound. The filter correctly gates on every dimension the design requires. The known gaps (risk-band sparsity, agent-role overcoverage, `not_determinable` routing debt) are real but their mitigations are already in the retrieval layer and the appendix design, not in the corpus itself. The one blocking issue is that three agent roles have 0/0/1 passages — `renal_safety`, `genetics_molecular`, and `nuclear_medicine` — and `renal_safety` gates the most consequential fork in bladder care. You cannot build agents for roles the corpus cannot feed.

---

## §3.1 — Fit Between the Corpus and Isolated Retrieval

### Agent role isolation is meaningful only for the specialist roles, not the core oncology ones

From the live database (eligible passages only):

| role | passages | share of 2,612 |
|------|---------:|---------------:|
| medical_oncology | 2,564 | 98% |
| urology_oncology | 2,409 | 92% |
| pathology | 2,237 | 86% |
| radiation_oncology | 2,017 | 77% |
| radiology | 1,727 | 66% |
| geriatric_frailty | 257 | 10% |
| palliative_supportive | 148 | 6% |
| nutrition | 137 | 5% |
| renal_safety | 1 | <0.1% |
| genetics_molecular | 0 | — |
| nuclear_medicine | 0 | — |

**Finding — material**: The five core oncology roles (medical oncology through radiology) have 66–98% overlap with the full corpus. An agent filtering on `agent_role = "medical_oncology"` sees essentially the same passages as one filtering on `agent_role = "urology_oncology"`. The consensus measure over these agents is therefore measuring agreement over largely identical evidence. This does not break the system — the agents still see different prompt instructions and will reason differently — but it means the **evidence isolation** the design promises (§1.3) is not delivered by `agent_role`. The isolation comes from the prompt, not the passage set.

**Severity**: material. The retrieval evaluation can quantify how much the passage sets actually differ per role by measuring overlap. The isolation claim should be adjusted in the design document — or the corpus should be re-labelled so that role slices are genuinely distinctive. That is a large labelling task and should not block v5.1.

### Three roles have no usable corpus

- `renal_safety`: 1 passage. The single passage (`afu.bladder-tvim.2024-2026.chimiotherapie-neoadjuvante-a-base-de-cisplatine.01.0108`) is about neoadjuvant cisplatin-based chemotherapy — relevant, but one passage cannot support an agent. Renal function gates cisplatin eligibility, which is the most consequential fork in bladder care, and three of the fifteen dossiers turn on it. Probe 5 (`Sécurité rénale avant cisplatine`) passes because it asserts a named passage is reachable — it cannot detect that nothing else is.
- `genetics_molecular`: 0 passages. The vocabulary includes the value and no passage carries it.
- `nuclear_medicine`: 0 passages. Same.

**Finding — blocking**: A `renal_safety` agent with one passage to cite is not an agent; it is a paragraph with a citation. Either populate the role (label existing passages about renal function, cisplatin eligibility, eGFR thresholds from the EAU/NCCN/AUA guidelines) or remove the role and fold renal safety into the medical oncology agent's concerns. The genetics and nuclear medicine roles should similarly be populated or removed before their agents are built.

### Multi-valued roles dissolve the isolation boundary

Passages can carry multiple roles — e.g. the single `renal_safety` passage also carries `medical_oncology` and `urology_oncology`. The filter uses `contains` (PostgreSQL `@>`, line 317 of retrieval.py), which admits a passage if it carries the requested role among its set. So a passage labelled `{urology_oncology, medical_oncology, radiation_oncology}` is admitted to all three agents.

**Finding — noted**: This is the correct filter semantics (a passage genuinely relevant to three specialties should reach all three), but it means multi-valued roles are the mechanism by which the five core oncology roles converge on the same passage set. The convergence is in the data, not the filter. If role isolation is a design requirement, the labelling must be sparser — passages should carry only the role they are *primarily* for. This is a labelling philosophy choice, not a bug.

### Agent roster vs corpus vocabulary mismatch

The corpus vocabulary (`vocabulary.py`) names 11 roles. The agent directory (`agents/`) has 1 `brain.md` (urology_oncology), with 6 other directories planned but not populated. Two vocabulary roles (`safety_reviewer`, not a clinical specialty per the vocabulary note) are not in the agent roster. The prompt asks: "Is the corpus labelled for the panel that exists, or for a different one?"

**Finding — noted**: The corpus labels passages for the agent roles the design names. The agent implementations are not yet built, so the question of mismatch is premature. The real risk is the opposite: the corpus labels 11 roles, only 7 agents are planned, and 3 of those have no corpus. The panel design document should state which agents will actually be implemented for v1 and ensure their corpus coverage, rather than waiting for the agent builder to discover empty slices.

---

## §3.2 — Is the Routing at the Right Granularity?

### 52% `not_specified` is honest, not broken

The composition (from the prompt's own table in §2.3):

| reason | passages | admitted to every setting? |
|--------|---------:|:---:|
| spans_settings | 885 | yes |
| general_principle | 666 | yes |
| not_determinable | 285 | no |

The 1,551 `spans_settings` + `general_principle` passages are genuinely general: NCCN's treatment-principles chapters, EAU-ESMO's Delphi questions, SIOG's geriatric screening. The `ADMISSIBLE_UNSPECIFIED_REASONS` constant in `retrieval.py` correctly separates these from `not_determinable`.

**The 285 `not_determinable` passages are routing debt, and the filter correctly excludes them.** I sampled the live database: AFU bladder TVIM passages about `Évaluation à distance`, `Ganglionnaire`, `Statut FGFR`, `Statut PD-L1`, `Nectine-4`. These are diagnostic/staging tool sections that the routing rules could not place in a specific disease setting because they apply across MIBC stages (non-metastatic and metastatic). The distinction from `spans_settings` is: `not_determinable` means "the rules could not place this, and we don't know if it genuinely spans settings." `spans_settings` means "we checked and it does."

**Finding — noted**: 285 is acceptable for a corpus of 2,612 eligible passages (11%). Each of these represents a routing decision that was not made. The filter correctly withholds them from every setting-specific question. The cost is that a general bladder question cannot reach them either — but `spans_settings` passages cover the truly general material. This is the right tradeoff.

### Risk-band sparsity is a guideline problem, not a routing problem

The prompt asks: "Read a rule file and the sections it routes. Is the corpus under-routing, or are the guidelines really written this way?"

**The guidelines are really written this way.** I verified this in the prior review and confirmed here:

- AUA localized prostate: headings are `RISK ASSESSMENT`, `RISK-BASED MANAGEMENT`, `PRINCIPLES OF MANAGEMENT`. Risk bands live in the body of numbered statements. The rule file note says: "splitting them by path would claim a stratification the headings do not make." **This is correct.**
- AFU TVNIM: the one document that DOES stratify by heading — `très haut risque`, `haut risque`, `CIS isolé` — has those headings and routes them correctly.
- EAU NMIBC: treatment chapter is organised by modality (intravesical chemotherapy, BCG, cystectomy), not by risk band. The rule file says: "the honest rule set is short."

**The filter fix (`SETTING_PARENTS`) is correct and well-defended.** A risk band admits its risk-unspecified parent. The relation runs one way: `prostate_localized_intermediate_risk → prostate_nonmetastatic_unspecified_risk`. It does NOT admit siblings (localized low-risk for intermediate-risk questions). Seven tests pin this. The fix moved `pr-001` from "refused by filter" to "ranking failure" — a real improvement.

**Finding — confirmed**: The corpus is right and the filter is right. The risk-band sparsity is a property of how guidelines write their headings, not a routing failure. The `SETTING_PARENTS` mechanism is the correct mitigation.

---

## §3.3 — Are the Labelling Axes the Right Axes?

### `clinical_purpose` is close to vacuous

From the live database (eligible passages only):

| purpose | passages | share |
|---------|---------:|------:|
| general | 1,765 | 68% |
| diagnosis | 239 | 9% |
| therapy_indication | 197 | 8% |
| supportive_care | 152 | 6% |
| screening | 95 | 4% |
| all others | < 75 each | < 3% |
| **treatment_delivery** | **0** | — |

**Finding — blocking for the positions comparison, not for retrieval**: Sixteen documents declare `treatment_delivery` as a position domain in `specs.json`. Zero passages carry the purpose. This means a question about how to deliver a treatment (e.g., surgical technique for cystectomy, radiotherapy fractionation) will match zero passages when filtered by purpose — OR will match only `general` passages because `admit_universal_purpose` is `True`. The `general` admission parameter is doing the work that `treatment_delivery` should be doing.

The prompt asks: "Is this axis earning its place, is it mislabelled, or is it under-populated?"

**Under-populated.** The 1,765 `general` passages include material that should be split into specific purposes. A passage about surgical technique for cystectomy is `therapy_indication` or `treatment_delivery`, not `general`. The axis is well-designed (the purposes map to the questions a tumour board asks) but the labelling is almost entirely `general`.

**Severity**: material. The `general` admission parameter (`admit_universal_purpose = True`) masks this, so retrieval still works. But the purpose filter cannot discriminate between a staging question and a treatment question, which is the entire reason it exists. Labelling passages with specific purposes is a large task and should be scheduled, not done before the panel is built.

### No passage-to-option mapping

The prompt asks: "Nothing in the corpus labels which passage supports which option." This is correct — the corpus labels disease setting, therapy line, clinical purpose, and agent role, but never "this passage supports radical prostatectomy over active surveillance."

**Finding — noted**: This is a design choice, not an omission. The agent is expected to read the passage and decide which option it supports, citing the passage id. Adding an option-axis to the corpus would require knowing the option set in advance, and the option set is per disease setting and awaiting clinical validation (`docs/reviews/therapeutic-options-for-validation.md`). The current design — passages retrieve by setting, agent reasons over them — is correct for the current state.

### `position_domains` is granular enough

Only 18 of 37 documents are position-bearing (`treatment_guideline` or `treatment_consensus`). The other 19 are classification standards, measurement standards, or specialist guidance — they answer questions but do not hold a stance to be compared. The `source_role` classification in `specs.json` is correct for every document I spot-checked (verified against the actual document types and content).

**Finding — confirmed**: The classification prevents the false-conflict failure described in §1.3. SIOG cannot enter a positions comparison for a treatment question because its `source_role` is `specialist_guidance`, not `treatment_guideline`. This is correct.

---

## §3.4 — Coverage Against the Actual Job

### Is 37 documents enough?

For prostate and bladder cancer, the coverage is strong: EAU, AUA, NCCN, ESMO, and AFU all contribute treatment guidelines for both diseases. The specialist guidance fills geriatric assessment (SIOG 4, ESMO-SIOG 1, AFU 3), nutrition (ASPEN 3, ESPEN 2), palliative care (SFAP 1), and cachexia (ESMO 1). Classification standards (WHO-IARC, PI-RADS) and measurement standards (RECIST 2) are present.

**What is missing that a tumour board would reach for?** I can identify three gaps:

1. **No French-language treatment guidelines for bladder.** AFU TVNIM and TVIM are the only French bladder treatment guidelines. They are from 2024-2026. EAU, AUA, NCCN, and ESMO bladder guidelines are all English. This is a systematic French-question blind spot for bladder — the keyword leg's French query has only AFU to match against for bladder-specific concepts like `tumeur de vessie infiltrant le muscle`.

2. **No SIOG bladder position paper from 2017.** The coverage.json records `siog_bladder_cancer_older_patients_position_paper_2017` as `document_does_not_exist` (ref-27). SIOG bladder 2020 is present, which may cover the same ground.

3. **Superseded SIOG prostate 2010 is excluded in favor of 2019.** This is correct — the 2019 paper supersedes it — but the exclusion reason is `superseded_by_boyle_2019`, which is a curation reason, not a retrieval exclusion. It is not in the corpus at all.

**Finding — noted**: The French bladder gap is real but the AFU guidelines cover both NMIBC and MIBC comprehensively. The keyword leg's two-query design (French concepts against French passages, English against English) is the right architecture for this.

### 89% English / 11% French for French questions

The retrieval design handles this correctly: two keyword queries (French + neutral, English + neutral), fused by reciprocal rank. The neutral vocabulary (TNM codes, drug names, scores) is what bridges the language gap. The prompt's concern about a "systematic French-question blind spot" is partially addressed by this design, but the evaluation has not yet measured language-specific recall.

**Finding — noted**: The pilot evaluation used hand-written concepts, not auto-generated ones from a case ledger. When the query builder exists, French questions will produce French concepts from the ledger, and the French keyword query will match only the 11% French corpus — exactly the concern. The neutral vocabulary is the bridge, but its adequacy has not been measured. This should be in the `dev` evaluation split.

### Psycho-oncology exclusion

Excluded by clinical decision on 24 August 2026. 29 documents deferred, not discarded. The coverage.json records every one with `deferred_to_later_version`. The documents stay curated, converted, and checksummed.

**Finding — noted**: The absence of psycho-oncology breaks nothing in the current panel — there is no psycho-oncology agent. The supportive-care pathway (palliative care, nutrition, geriatric assessment) remains covered. The PPS sections that might expect psycho-oncology content (distress screening, psychological support) will be left visibly empty, which is the correct behavior per the design rule: "A field RCPia cannot fill is left visibly empty."

### 920 excluded passages: sample

I sampled from the live database. All 920 exclusions fall into three categories:
- 618 `bibliography_entry` — reference lists
- 300 `appendix_content` — panel rosters, COI disclosures, methodology chapters, abbreviations
- 2 `local_setting_mislabelled` — NCCN prostate passages about ADT principles and risk stratification that carry a setting the rule author flagged as wrong

The 2 `local_setting_mislabelled` passages are a finding: they are excluded because a routing rule caught a mislabelling, and excluding them with a reason is better than silently serving them to the wrong setting. But they are NCCN prostate content — ADT principles and risk stratification — and excluding them entirely means they cannot answer any question. If the mislabelling is fixed in the routing rules, they should be admitted.

**Finding — noted**: The 2 `local_setting_mislabelled` passages are NCCN prostate content excluded because of a routing error the rule author caught but could not fix at the source. They should be re-routed rather than excluded. A rule that says "this passage's setting is wrong" is useful for the audit but the passage itself is still clinical content.

---

## §3.5 — Safety Properties

### Wrong-setting answer: try to find a path

The prompt asks me to "try to find a path by which a passage written for one clinical situation can reach a question about another."

**The strict filter blocks wrong-setting answers correctly.** `disease_setting`, `cancer_type`, and `agent_role` are never dropped by `widen()` (retrieval.py line 271-276). Only `therapy_line` is relaxed. The `admit_unspecified` parameter admits only `general_principle` and `spans_settings` — not `not_determinable`. A metastatic passage cannot reach a localized question unless it carries `spans_settings` (which means it genuinely spans settings).

**One path exists through multi-valued fields**: a passage labelled `{bladder_nmibc_unspecified_risk, bladder_mibc_non_metastatic}` would be admitted to both an NMIBC question and an MIBC question. The corpus does not label passages with multiple disease settings (the emission pipeline assigns one setting per passage), but a future change could. The `SETTING_PARENTS` mechanism similarly creates a path — a risk-unspecified parent passage is admitted to risk-band questions — but this is by design, not a leak.

**The `clinical_purpose` admission of `general` is the widest path.** 1,765 passages carry `general`, admitted alongside every purpose. A screening question can reach a `general` passage about surgical technique. This is not wrong — `general` genuinely means "applies broadly" — but it dilutes the purpose filter's discrimination.

**Finding — noted**: The safety architecture is sound. No path exists for a wrong-cancer-type answer (the filter never drops `cancer_type`). No path exists for a wrong-setting answer without `spans_settings` or `general_principle`. The multi-valued `agent_role` fields and the `general` purpose admission are the widest apertures, but they are parameters the retrieval evaluation can sweep (`admit_universal_purpose`, etc.) to price.

### `clinical_status = poc_unreviewed` for essentially everything

No clinician has read the passages. The `RetrievedChunk` carries `clinical_status` explicitly (retrieval.py line 339) so the renderer can display it. The PPS and appendix should state this prominently.

**Finding — material**: A system that cites unreviewed passages in a document a board reads is a system whose citations are provisional. The design document already acknowledges this (§1.1: "RCPia never claims a decision was taken"). The same posture should apply to citations: the appendix should carry a visible banner that citations are `poc_unreviewed` until clinical validation. This is a rendering decision, not a corpus decision, but the corpus's `clinical_status` field makes it possible.

### Superseded guidance

The corpus excludes superseded guidance at the curation level (SIOG prostate 2010 excluded with `superseded_by_boyle_2019`). The `historical_treatment_guidance_superseded` exclusion reason exists in the vocabulary but is not used in the live corpus. The filter unconditionally excludes `retrieval_eligible = false` passages (retrieval.py line 292). The design document correctly identifies superseded guidance as more dangerous than irrelevant guidance — it is on topic, reads as authoritative, and carries a real citation. The filter's unconditional exclusion is the right safety measure.

**Finding — confirmed**: Adequately handled. The retrieval filter's unconditional exclusion of non-eligible passages (line 292) cannot be relaxed by widening. A superseded passage cannot reach a question through any path.

---

## Summary of Findings

### Blocking (the agent panel should not be built until this changes)

1. **Three agent roles have no usable corpus.** `renal_safety` (1 passage), `genetics_molecular` (0), `nuclear_medicine` (0). Either populate these roles by labelling existing passages, or remove the roles and fold the responsibility into existing agents. `renal_safety` is the most urgent — it gates cisplatin eligibility in bladder cancer.

### Material (real cost, can be scheduled)

2. **Agent role overcoverage.** The five core oncology roles have 66–98% overlap with the full corpus. Evidence isolation is delivered by the prompt, not by the passage set. The design claim should be adjusted or the labelling made sparser.

3. **`clinical_purpose` is 68% `general`.** The axis is well-designed but under-populated. `treatment_delivery` has zero passages while 16 documents declare it as a position domain. The `general` admission parameter masks this.

4. **`clinical_status = poc_unreviewed`.** Citations are provisional. The appendix should carry a visible banner stating this until clinical validation.

### Noted (worth knowing, no action implied)

5. **285 `not_determinable` passages** — acceptable routing debt (11% of eligible). The filter correctly withholds them.

6. **Risk-band sparsity is a guideline property, not a routing failure.** The `SETTING_PARENTS` filter fix is correct.

7. **Multi-valued roles dissolve isolation boundaries** — a labelling philosophy choice, not a bug.

8. **2 `local_setting_mislabelled` passages** excluded rather than re-routed — NCCN prostate content that should be fixed at the source.

9. **French bladder coverage** relies primarily on AFU — the keyword leg's language split is the right design but has not been evaluated for language-specific recall.

10. **Psycho-oncology exclusion** breaks nothing in the current panel. The decision is documented and reversible.

11. **No passage-to-option mapping** exists — a design choice, not an omission. The option set awaits clinical validation.

12. **The one ineligible-but-embedded passage** (`nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.08.2792`) is the anomaly the prompt offered as a thread. It is excluded as `local_setting_mislabelled` and should not have an embedding. This is a minor emission bug.

13. **Safety architecture is sound.** No path exists for a wrong-cancer-type or wrong-setting answer without an explicit admission parameter. The `general` purpose admission and multi-valued roles are the widest apertures, and both are priced by the retrieval evaluation.

---

## What Is Fine (and Should Not Be Changed)

1. **The routing-by-section-path architecture.** Every rule file is internally consistent, and the prior review confirmed no silent misroutings. The founding principle is correctly implemented.

2. **The filter design.** `widen()` correctly protects `cancer_type`, `disease_setting`, `clinical_purpose`, and `agent_role`. Only `therapy_line` is relaxed. The `ADMISSIBLE_UNSPECIFIED_REASONS` constant correctly separates `spans_settings`/`general_principle` from `not_determinable`.

3. **`SETTING_PARENTS`.** The risk-band admission mechanism is a generalisation, not a widening. It runs one way. Seven tests pin it. This is the right fix for the right measurement.

4. **`position_domains` and `source_role`.** Only 18 of 37 documents are position-bearing. This prevents the false-conflict failure described in §1.3. SIOG cannot enter a treatment comparison.

5. **The two-query keyword design.** French concepts against French passages, English against English, neutral against both. The architecture handles the 89/11 language split correctly.

6. **The WHO/IARC inverted default.** Confirmed correct in the prior review. The note about organ-prefixed settings vs `not_specified` is honest.

7. **The exclusion regime.** Bibliography and appendix exclusions are heading-name-verifiable and correctly applied. No clinical content found wrongly excluded in my spot-check.

8. **The appendix design's safety guards.** Scores locked before appendix revealed; safety reviewer reads for confident-but-wrong reasoning. These are the right guards for the citation-trust risk.

---

## Disagreements with the Team's Own Conclusions

The prompt asks: "Part 2 contains our own conclusions, including one where we decided the corpus was right and the filter was wrong. If any of that is mistaken, say so plainly."

**I agree with the corpus-right/filter-wrong decision on AUA localized risk bands.** The guideline headings genuinely do not stratify by risk, and routing from body text would violate the founding principle. `SETTING_PARENTS` is the correct fix. The pilot-findings.md analysis of finding 2 is accurate.

**I agree that the reranker currently costs recall.** Finding 5 in the pilot is reproducible and correctly analysed. This is not a corpus issue.

**I agree that `bl-004`'s second claim needs re-labelling.** ESMO metastatic passages should not be the gold standard for a non-metastatic question. The filter is right.

**The one disagreement**: The prompt says `treatment_delivery` has 0 passages and 16 documents declare it. The team treats this as a labelling gap to fill later. I think this is more consequential: `treatment_delivery` is how a treatment is performed — surgical technique, radiotherapy fractionation, instillation schedules. These are the passages a urologist reaches for when the board asks "how do we do this?" Having zero of them means the corpus answers "should we do this?" and not "how?" For a system that drafts a PPS, the "how" matters — the PPS records the decision and the modality. **This is my only substantive disagreement with a team conclusion, and I rate it material rather than blocking because the `general` admission masks it for now.**

---

## Should This Corpus Be Used as It Is for the Agent Panel?

**Yes, with one condition.** Fix the `renal_safety` role before building that agent. One passage cannot support an agent that gates the most consequential fork in bladder care. Label existing passages about renal function, cisplatin eligibility, and eGFR thresholds — the EAU MIBC guideline chapters 6.2–6.4 (health status, comorbidity, frailty) are the obvious source. The genetics and nuclear medicine roles can be deferred; renal safety cannot.

Everything else can be scheduled: purpose labelling, role-labelling sparsity, the French bladder gap, the `clinical_status` banner. These are real costs but they do not prevent the panel from being built and evaluated. The architecture — routing by section, filtering by setting, admitting by parent — is correct and well-defended.