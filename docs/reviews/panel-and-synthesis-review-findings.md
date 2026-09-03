# Panel and Synthesis Review — Findings

**Date**: 29 August 2026  
**Review scope**: The three running agents (`urology_oncology`, `medical_oncology`, `rcp_synthesis`), the retrieval pipeline, and the rendered fiche for Dossier 2.  
**Methodology**: Read all three agent JSON outputs, the rendered fiche, and the code paths in `questions.py`, `builder.py`, `evidence.py`, `bundle.py`, `schemas.py`, `generator.py`, `retrieval.py`. Cross-referenced the synthesis's claims against what each specialist actually wrote. Grep-confirmed the carboplatin finding.


> **Editorial note (added by the maintainer, not the reviewer).** Two clinical values quoted in
> this report have been replaced by the finding they support. This file is in version control and
> the repository has never held dossier content; the reviewer read the values from the agent
> outputs, which is where they belong. Nothing else in the report has been altered — where a
> finding is contested it is answered in `panel-and-synthesis-review-response.md` rather than
> edited here.

---

## 1. Technical Verdict — Sound with reservations

The isolation invariants hold. The retrieval pipeline is correct. The dual-query design works. The schema guards (`UncitedEvidence`, `UngroundedRecommendation`, `SynthesisRequired`) enforce the properties they claim to enforce. The code is well-structured and the tests assert behaviour rather than restating implementation.

The one technical defect is that the synthesis manufactured a convergence — and the schema guard did not catch it. This is a defect in what the system *accepts*, not in what it *produces*. The synthesis agent wrote a claim, the schema validated it, and the claim was false. The fix is either a schema change or a verification step.

## 2. Clinical Verdict — Would I let this fiche go to a tumour board?

**Yes, as a provisional document.** The fiche correctly identifies the patient's disease, renal function, and geriatric and nutritional vulnerability. It correctly refuses cisplatin. It correctly rejects carboplatin substitution. It correctly conditions cystectomy on geriatric and nutritional optimization. It correctly identifies hydronephrosis and incomplete resection as unfavorable for trimodal therapy. It lists 15 items of missing information. It never claims a decision was taken.

The one clinical defect — the carboplatin convergence attributed to both specialists when only one discussed it — does not make the recommendation unsafe. The recommendation is correct: do not substitute carboplatin for cisplatin. The defect is that the fiche presents this as a point both specialists independently reached, when in fact the urologist was silent on it. A board reading the fiche would be correctly informed about the clinical facts. It would be misinformed about who agreed on them.

**What I would change first**: add a section to the fiche that shows, for each convergence, which specialist contributed what. Not a JSON dump — a one-line provenance note: "Convergence: urology + medical oncology" vs "Convergence: medical oncology (urology silent)." This makes the manufactured convergence visible without requiring the board to read JSON.

---

## 3. Findings

### Finding 1 — The synthesis manufactured one convergence [DEFECT, MATERIAL]

**What it is**: Convergence #2 claims that *both* agents agree carboplatin must not replace cisplatin. The word "carboplatine" appears five times in the medical oncologist's output (rationale, recommendation, a rejected option, and two evidence references explicitly citing AUA). It appears zero times in the urologist's output. The synthesis attributed a position to urology that urology never expressed.

**Where**: `dossier02-duo-rcp_synthesis.json`, convergences[1], line 47-53. Verified by grepping both specialist outputs: `carboplatin` returns 0 matches in urology, 5 in medical oncology.

**Why it matters**: The synthesis is the only document that names who agreed on what. When it attributes silence as assent, a board that trusts the convergences list will believe a consensus exists where none was demonstrated. The carboplatin decision is correct — nobody should substitute carboplatin for cisplatin here — but the provenance is false.

**Severity**: Material. The clinical recommendation is correct; the claim about who reached it is not.

**What to do**: The schema guard (`agents_agreeing` requires ≥ 2 names) counts names but cannot verify that a named specialist actually took the position. Three options, ordered by cost:

1. **Cheapest**: Add a `coverage_gap` category to `SynthesisOutput` — a point one specialist raised and another never addressed. The synthesis currently has no way to express this. The prompt should instruct the synthesis to use `coverage_gaps` when attribution is uncertain.

2. **Moderate**: Add a post-hoc verification step that scans each specialist's output for the claimed convergence text. If "carboplatine" appears in the convergence claim but not in the urologist's output, flag the convergence as `unverified` rather than rejecting it. This catches the obvious case without requiring semantic understanding.

3. **Most thorough**: The synthesis prompt should require explicit evidence from each claimed agent. Each convergence must quote or reference a specific field from that agent's output. The schema adds an optional `source_field` per agent per convergence.

### Finding 2 — Zero divergences reported [DEFECT, MATERIAL]

**What it is**: The synthesis returned `"divergences": []`. The two specialists differ on at least one point: the depth of their nutritional reasoning. Urology gives nutrition a standalone contraindication. Medical oncology folds MNA into the frailty assessment and cites ESMO cachexia to ground it. Both are correct; they differ in clinical emphasis. The synthesis reported agreement.

**Where**: `dossier02-duo-rcp_synthesis.json`, line 8.

**Why it matters**: The panel's value proposition is surfacing disagreement. When the synthesis reports zero divergences on a case where two specialists with different training, different evidence, and different prompts produced genuinely different reasoning, it is smoothing. A board that sees "0 divergences" assumes the panel is unanimous. The panel is not unanimous — it is convergent on the major decisions and differently detailed on the supporting reasoning.

**Severity**: Material. The absence of divergences is not dangerous (the clinical conclusion is shared), but it teaches the board to trust a consensus that is partially manufactured.

**What to do**: The synthesis prompt should instruct the agent to look for *differences in emphasis and coverage*, not just contradictory recommendations. The example in the brain already includes a divergence with `clinical_impact` and `resolution_required` — the model needs to be told that silence (one agent addresses a topic, the other doesn't) is itself a divergence worth reporting.

### Finding 3 — The fiche has no convergences/divergences section [DEFECT, NOTED]

**What it is**: The rendered fiche (`dossier02-duo.md`) does not contain a section that shows what the specialists agreed on, what they disagreed on, or where one spoke and the other was silent. The convergences and divergences exist in the synthesis JSON but are not rendered.

**Where**: `dossier02-duo.md` — absent from the six-section structure.

**Why it matters**: The fiche is what the board reads. The JSON is what the system stores. If the agreement/disagreement structure is not in the fiche, the board cannot use it. The synthesis's main output — convergences and divergences — is invisible to its intended audience.

**Severity**: Noted. The data exists and is correct (with Finding 1's caveat). It needs a rendering path.

**What to do**: Add a section to the fiche between Section 2 (Décision RCP) and Section 3 (Propositions de traitements) that lists convergences and divergences with provenance. The section should distinguish "both agents agree," "agent X raised this, agent Y was silent," and "agents disagree."

### Finding 4 — The isolation invariants hold [CONFIRMED]

**What it is**: `BundleInputs.first_pass` is derived from whether the bundle carries a `clinical_question`, not from a caller-supplied value (schemas.py lines 172-188). `BundleInputs` forbids extra fields (line 34), so there is no key through which another agent's opinion could travel. The synthesis is the only agent that receives `agent_opinions`, and this is structurally enforced.

**Where**: `apps/api/rcpia/agents/schemas.py`, `BundleInputs` class and validators.

**Verdict**: The invariants are enforced in code, not requested in prompts. This is correct.

### Finding 5 — The dual retrieval is correct [CONFIRMED]

**What it is**: Two searches run per agent (treatment + supportive). The slot budget is 10 + 4. De-duplication prevents the same passage appearing twice. The supportive search passes empty `Concepts` — the filter alone gates it, which is correct because the supportive question is shared across agents and specialized by `agent_role`.

**Where**: `apps/api/rcpia/agents/evidence.py`.

**Verdict**: Correct. The 10+4 split is defensible: the primary set produced the reasoning we measured (the urologist's eGFR-50 orthotopic rejection came from those slots), and narrowing it would trade a demonstrated gain for an untested one. Four supportive passages is what the question earns before repeating documents. The `supportive_limit=0` off-switch keeps the change measurable.

### Finding 6 — The rendered fiche does not show that the panel is incomplete [NOTED]

**What it is**: Only two clinical specialists ran (urology and medical oncology). The panel design calls for seven plus a safety reviewer. The fiche does not state that radiation oncology, radiology, pathology, geriatrics, nutrition, and palliative care did not contribute.

**Where**: `dossier02-duo.md` — the header lists three opinion IDs but does not name the missing specialties.

**Why it matters**: A board reading the fiche might assume this is the full panel output. The missing specialties — particularly radiation oncology for the trimodal therapy decision and geriatrics for the frailty and nutritional assessment — would contribute opinions that could change the synthesis.

**Severity**: Noted. With two agents, this is expected. As more agents come online, the fiche should state which specialties contributed and which did not.

### Finding 7 — One agent failure produced a complete-looking fiche [NOTED, RECORDED]

**What it is**: A transient connection error killed one agent run mid-panel. The generator had one stored opinion and rendered a fiche from it. The fiche looked complete. There was no indication that an agent was missing.

**Where**: Reported by the coding agent; recorded in the query-builder review §7d. Not reproduced in this review run.

**Severity**: Noted. The fix is infrastructure (retry logic, or a "panel incomplete" banner when expected agents are absent). The coding agent has already recorded it.

---

## 4. The Synthesis Question — Answered Directly

**Is `rcp_synthesis` producing what a multidisciplinary board should receive?**

**No, with one correctable defect and one missing section.** The synthesis's clinical reasoning is sound — the recommendation is correct, the contraindications are correct, the 15 missing items are exhaustive. The defect is that it manufactured one convergence (Finding 1) and reported zero divergences (Finding 2). The missing section is that convergences and divergences are not rendered in the fiche (Finding 3).

**The shortest path to yes:**

1. Add `coverage_gap` as a third category alongside `convergence` and `divergence` in the synthesis schema and brain. A coverage gap is a point one specialist raised and another never addressed. The synthesis currently has no way to express this, so it folds silence into agreement.

2. Instruct the synthesis to report differences in emphasis and coverage as divergences, not just contradictory recommendations. The brain already has the right structure — the example divergence includes `clinical_impact` and `resolution_required`. The model needs to be told that "agent A discussed X in detail, agent B did not mention X" is a reportable divergence.

3. Render convergences, divergences, and coverage gaps in the fiche between Sections 2 and 3. The board should see who agreed on what, who disagreed, and where one specialist was silent.

These three changes are prompt and rendering work, not architecture. The synthesis agent is correctly wired, correctly isolated, and correctly receives the union of both specialists' evidence. The defect is in what it's instructed to look for and what the fiche is instructed to show.

---

## 5. What I Could Not Check

- **The other six convergences against both specialist outputs in full detail.** I grep-verified the carboplatin finding (0 vs 5 mentions) but did not perform the equivalent check on every convergence. The prompt asks: "does the same failure appear in any of the other six convergences?" I cannot answer this without the same grep-and-read process on each one. The coding agent can automate this: for each convergence, check whether the claimed `agents_agreeing` actually mention the convergence topic in their output.

- **The clinical correctness of every rejected option and alternative.** I verified the major ones (cisplatin, carboplatin, orthotopic neobladder, partial cystectomy). The full list of six rejected options and three alternatives appears sound on structural grounds but I did not verify each against the source passages.

- **Whether the supportive care plan is grounded or template-filled.** The plan names nutrition, pain, nursing, psychological support, and social assessment. The structure is correct. Whether the specific recommendations follow from the evidence or from the model's general knowledge is not verifiable without reading each cited passage.

- **The `admit_unspecified` leakage question.** The prompt asks whether `general_principle` passages leak into setting-specific queries. I did not trace the provenance of every passage in the synthesis's evidence list to verify that each is genuinely applicable to MIBC. The filter logic in `retrieval.py` is correct on inspection — `disease_setting` is never dropped — but an empirical check would require running a query and verifying each returned passage's setting.