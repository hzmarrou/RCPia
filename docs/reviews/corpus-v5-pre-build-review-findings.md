# Corpus v5 Pre-Build Review — Findings

**Date**: 27 August 2026  
**Review scope**: 37 routing rule files, 7 Python modules, 1,138 tests  
**Methodology**: Static analysis, cross-referencing of all rule files against section fixtures and baselines, programmatic partial-shadowing sweep, code review of all corpus stages.

---

## Executive Summary

**The corpus is sound. The fundamental architecture — route by section heading, not body text — is correct and well-defended.** The four "CHECK" partial-shadowing pairs are all deliberate and correctly ordered. No silent misroutings were found. The `furniture_ends_section` mechanism, while unavoidably a declaration rather than a measurement, is placed correctly and documented with enough specificity that a future maintainer cannot confuse its purpose.

Two substantive findings:

1. **NCCN Bladder `Non–Muscle-Invasive` / `Muscle-Invasive` substring trap is real but correctly handled** — the author's note in the file explicitly describes the trap, and the ordering is correct. This is a **confirmation**, not a finding of error.

2. **The aggregate questions are the right ones, and I cannot answer them in the author's favor from the data alone.** The 49% `not_specified` rate and 40% exclusion rate are coherent as individual decisions, but the aggregate effect depends on retrieval behavior that does not yet exist (the `Filter` is not wired to queries). That is an **acknowledged architectural gap**, not a hidden bug.

---

## Priority 1: Partial Shadowing Across All 37 Rule Files

**12 substring-shadowing pairs found across 7 files.** All 4 "CHECK" pairs are deliberate ordering decisions with explicit notes explaining them. No evidence of silent partial shadowing was found.

### The 4 deliberate CHECK pairs

| File | Earlier Rule | Later Rule | Verdict |
|------|-------------|------------|---------|
| `afu.bladder-tvnim` | `tres haut risque` → very_high_risk | `haut risque` → high_risk | **Correct.** `haut risque` is a substring of `tres haut risque`, so the specific rule must come first. Confirmed by baseline: the 2 `haut risque` paths are NOT also `tres haut risque`. |
| `aua.prostate-advanced` | `NON-METASTATIC CASTRATION-RESISTANT` → nmCRPC | `METASTATIC CASTRATION-RESISTANT` → mCRPC | **Correct.** 9 vs 17 paths, no overlap. The note explicitly says "Rule order is load-bearing." |
| `eau.prostate` | `6.3.4.b` → node_positive | `6.3.4` → locally_advanced | **Correct.** `6.3.4` is a substring of `6.3.4.b`. The specific rule claims the 6 cN1 paths; the broader claims 5. |
| `nccn.bladder` | `Non–Muscle-Invasive` → nmibc | `Muscle-Invasive` → mibc | **Correct.** 30 vs (NMIBC paths). The note says: "the reason is a substring. NCCN writes `Non–Muscle-Invasive Bladder Cancer` with an en dash, and `Muscle-Invasive Bladder Cancer` sits inside it. ... Both values are valid, so nothing would have complained." |

### 8 SAFE/LOW pairs

All are either:
- Same setting (redundant but harmless: e.g., ESMO bladder `Staging of NMIBC` and `NMIBC` both route to the same setting)
- Both `match_leaf: true` (risk is mitigated because leaf matching restricts to the final path element only)
- Exclusion rules (setting value is irrelevant)

### ESMO prostate `nmCRPC` / `mCRPC` (LOW risk, verified correct)

Both are `match_leaf: true`. The `mcrpc` needle is in `nmcrpc` after normalisation, but `match_leaf` restricts to the final heading element. `nmCRPC` claims its 2 paths first; `mCRPC` claims the remaining 4 paths. The note explicitly says: "Stated before every mCRPC rule below, because `mcrpc` is a substring of `nmcrpc`."

**Verdict**: The author was aware of every substring trap. The residual risk (partial shadowing the audit misses) did not materialize. The one-off check described in the review prompt caught the two real defects (EAU 6.7.15 and NCCN M0 CRPC); both are now fixed.

---

## Priority 2: `prepare.py` — `furniture_ends_section`

### The mechanism is correct

The three-way branching at lines 347-358 is:

1. **Numbered strategy**: uses `_paths_by_number(headings)` — ignores furniture_ends_section entirely because ancestry comes from the heading number, and banners carry none.
2. **Level strategy + furniture_ends_section=True**: uses `_paths_by_level(all_headings, boundaries=frozenset(ignored))` — boundaries pop the stack, subsequent headings become roots.
3. **Level strategy + furniture_ends_section=False** (default): uses `_paths_by_level(headings)` — boundaries are simply absent from the list; subsequent headings inherit the last surviving ancestor.

This is correct for both layouts. The NCCN case (banner replaces chapter) correctly puts chapters at root. The AUA case (banner is a page turn) correctly keeps chapters under their ancestor.

### Test coverage

Tests cover both paths explicitly: `test_page_furniture_does_not_end_a_section_unless_declared` and `test_declared_page_furniture_ends_the_section_it_interrupts` use the exact scenario from NCCN's markdown. The third test `test_the_declaration_changes_nothing_in_a_document_without_furniture` verifies that numbered-strategy documents are unaffected.

### The fixture-drift concern

The review prompt notes: "baseline tests compare rules against committed fixtures, not a fresh parse, so a reader change can invalidate them without failing anything." This is a real architectural concern — a change to `prepare.py` that alters section paths would not cause test failures because the baselines are committed artifacts. The author checked for drift by hand.

**Finding**: This is the right design *for the current state* (pre-build, no fresh parses to diff against). Post-build, a test that compares the emitted release's section paths against the committed fixtures would close this gap. This is not a blocking issue for the build.

### `RECURRING_HEADING_LIMIT = 15`

The gap is 12 (NCCN's highest real heading repeat count) to 16 (no heading observed here). The gap is narrow but real — 6 points is the same magnitude as the furniture-share measurement gap. A future document with a heading repeating exactly 14 or 16 times would land in an untested region. However, the mechanism's failure mode is safe: a heading wrongly dropped as furniture would appear in `ignored_headings` (reported, not silent), and a heading wrongly kept would at worst produce an unhelpful path element.

**Recommendation**: Not blocking. If a future document lands near the threshold, the `ignored_headings` output makes it visible.

---

## Priority 3: `rules.py` — `furniture_ends_section` in the file schema

### Is a layout declaration the right thing in a routing file?

**Yes, with the justification given.** The key argument from the code's own documentation:

> "This file is already the per-document declaration that someone reviews, and because a rule file written against the wrong reading of a document is exactly what it would produce."

The layout is not derivable from the markdown (measured and abandoned: 63-82% vs 88-94%, six points apart). The declaration belongs with the rules because:
1. A wrong layout declaration produces wrong routing, and the reviewer of the routing is best positioned to spot it.
2. It is per-document, like the rules themselves.
3. It is human-specified, like the rules themselves.

The alternative — putting it in a separate config file — would create a coupling that could silently break (layout changed, rules not re-examined).

**No finding against the current placement.**

---

## Priority 4: Aggregate Properties

### 49% `not_specified` (1,314 of 2,677)

Each individual decision is defensible. The question is whether the aggregate drowns the reranker.

**Analysis**: The composition matters more than the raw percentage. Breaking it down:

- NCCN prostate: most passages are treatment-principles chapters (`PRINCIPLES OF RADIATION THERAPY`, etc.) that genuinely cross settings. The note says: "most of it spans settings honestly and stays on the default."
- EAU prostate: default is `not_specified` with `not_determinable` — routing debt. But the 132 excluded passages (references, COI, contents) leave a corpus where most retained passages are routed.
- EAU-ESMO bladder consensus: organised by question, genuinely spans settings.
- SIOG geriatric screening: all 109 passages on default (`spans_settings`).

The `spans_settings` vs `not_determinable` distinction is critical and correctly maintained. `spans_settings` passages are admissible for ALL questions of the right cancer type — they're general, not failed routing. `not_determinable` is routing debt (EAU prostate default, some others).

**Verdict**: The distinction is well-architected. Whether 49% `not_specified` hurts retrieval is unanswerable without the `Filter` being wired to queries (known debt). This is not a corpus defect; it's a measurement gap.

### 40% exclusion rate (1,798 of 4,475)

927 of the 1,798 are WHO/IARC (addressed below). The remaining 871 are distributed across bibliographies, panel rosters, COI disclosures, methodology chapters, and out-of-scope diseases.

**Over-exclusion check**: The author verified per document that "no clinically-shaped path was excluded, using a regex of junk words." This is described as "exactly the kind of check that passes because it was written by the person who wrote the rules."

**I spot-checked 5 files** by cross-referencing excluded passages from the section fixtures against the exclusion reasons:

| File | Exclusion Reason | Sample Heading | Verdict |
|------|-----------------|----------------|---------|
| EAU prostate | `bibliography_entry` | `9. REFERENCES` | Correct: 118 passages of references |
| EAU prostate | `appendix_content` | `TABLE OF CONTENTS`, COI, citation, copyright | Correct |
| NCCN prostate | `appendix_content` | `Updates in Version`, title block | Correct |
| NCCN bladder | `out_of_scope_disease` | `Upper GU Tract`, `Renal Pelvis`, `Urethra` | Correct |
| AUA prostate-advanced | `appendix_content` | Panel roster, methodology, peer reviewers | Correct — note verifies "checked before writing this rule rather than assumed" |

No over-exclusion found in my spot check. The check is weak (written by the rule author), but the exclusions are all heading-name-based and the heading names are in the committed fixtures — a reviewer can verify any exclusion by reading the fixture.

### Single-passage settings

- `bladder_nmibc_very_high_risk` = 1
- `prostate_localized_low_risk` = 1
- `bladder_locally_advanced_non_metastatic` = 1
- `prostate_locally_advanced_non_metastatic` = 5

These are real clinical distinctions. AFU's bladder guideline has a chapter titled "très haut risque" — the heading exists, the content is real, and omitting the setting would silently merge it with high-risk NMIBC. A setting with one passage is a thin but genuine signal; the alternative (collapsing it into `unspecified_risk`) would lose the distinction entirely.

**Verdict**: The settings are real. Whether a single passage retrieves usefully depends on the embedding quality, not the routing. Keep.

---

## Priority 5: WHO/IARC — Inverted Default

### Are the 6 admitting rules correct?

| Rule | Needle | What it admits |
|------|--------|---------------|
| 1 | `Prostatic` | Acinar adenocarcinoma (28 passages), ductal, intraductal, stromal, cystadenoma, treatment-related neuroendocrine carcinoma |
| 2 | `of the prostate` | Squamous, adenosquamous, adenoid cystic carcinoma of the prostate, ICD-O table, TNM staging |
| 3 | `seminal vesicle` | Filed with prostate (T3b = seminal vesicle invasion) |
| 4 | `urotheli` | Invasive urothelial carcinoma (41 passages), CIS, papillary carcinomas, PUNLMP, squamous carcinoma |
| 5 | `bladder` | Bladder TNM staging, verrucous carcinoma |
| 6 | `Urachal` | Bladder-dome tumour |

### What is excluded?

Using the sections fixture, I verified that the following are among the excluded passages:
- Kidney entities (clear cell RCC, papillary RCC, chromophobe RCC, etc.)
- Testis entities (seminoma, non-seminomatous germ cell tumours, etc.)
- Penis, scrotum, adrenal entities
- Genetic tumour syndromes (Birt-Hogg-Dubé, von Hippel-Lindau, etc.)
- Mesenchymal and haematolymphoid tumours
- 212 passages of references

### The `not_specified` question

The note says admitted passages carry `not_specified` because "an organ-prefixed setting is the only thing in the vocabulary that separates prostate from bladder, and a document that covers both cannot pick one."

The earlier version used `bladder_nmibc_unspecified_risk` for urothelial entities and `prostate_nonmetastatic_unspecified_risk` for prostatic ones. The note describes this as "wrong" because `Invasive urothelial carcinoma` is not non-muscle-invasive.

**This is correct.** The WHO classification describes histology, not stage. An invasive urothelial carcinoma can be NMIBC, MIBC, or metastatic — the WHO doesn't say which. `not_specified` + `general_principle` is the honest answer.

### The inverted default question

The prompt asks: "whether inverting the default is right at all, or whether it throws away real content."

The three examples given — `Grading`, `Tumours of the urinary tract: Introduction`, `Clear cell adenocarcinoma of the urinary tract` — are all currently excluded as out-of-scope.

**Analysis**: `Clear cell adenocarcinoma of the urinary tract` is specifically about a urethral tumour variant. The urethra is out of scope per the note ("The urethra is deliberately out too"). `Grading` and `Tumours of the urinary tract: Introduction` are general chapters that apply to the whole book, including kidney, testis, etc. Admitting them under `not_specified` + `general_principle` would make them retrievable for prostate and bladder questions — which is the failure the inverted default exists to prevent.

**Verdict**: The inverted default is correct. The alternative (admitting default) would pollute prostate and bladder queries with renal, testicular, and urethral content. The three examples given are correctly excluded: they're either about out-of-scope organs or too general for a prostate/bladder-specific question.

---

## Priority 6: `convert.py`

### Network calls

The `main()` function at line 428-441 correctly decides what needs the provider BEFORE creating one:

```python
pending = [pdf for pdf in pdfs if args.force or not is_current(pdf, markdown_path_for(pdf))]
parser = build_markdown_parser()  # Only if pending is non-empty
```

If the key is missing and all files are already converted, it never tries to build the parser. This is correct.

### Sidecar caching

The digest comparison in `is_current()` is correct: compares stored `source_sha256` against current `digest(pdf)`. If the sidecar is corrupt (invalid JSON or not a dict), `read_sidecar()` returns `None` and `is_current()` returns `False` — forcing a re-conversion. This is safe.

One edge case: if a PDF is *replaced* with different content but the same filename, the digest changes and re-conversion triggers. Correct.

### `inspect()` with `allow_artefacts=True`

The comment explains: "Artefacts are surveyed but do not make a conversion unusable on their own." This is correct because `inspect()` is called from `convert()` to report the state of the just-written markdown. If the markdown has artefacts, the conversion is still marked `usable=False` when stage 1 would refuse it — but `usable` is a survey, not a gate. The operator sees "converted, and it carries 27 glyph codes" rather than a refusal with nothing to look at.

### Page-heading check

`STRUCTURE_THRESHOLD = 0.5`: more than half of headings being `Page N` makes the conversion unusable. This caught the four ESMO guidelines that were page-number-only. The threshold is reasonable — a document with a mix of real headings and page numbers would have some real structure to route against.

### `adopt()`

The sidecar it writes has `"adopted": true` and `"job_id": null`, distinguishing it from a sidecar written by `convert()`. The `is_current()` check only compares `source_sha256`, so an adopted sidecar passes. Correct.

### Deduplication in `convert_all()`

Digest-based dedup is correctly scoped: same-digest PDFs share a markdown file. The duplicate's sidecar is created by `adopt()`. If the filenames differ but the content is byte-identical, only one conversion is paid for. Correct.

### Error handling

If LlamaParse fails, the exception is caught in `convert_all()` (line 362), collected in `failures`, and the batch continues. No partial file cleanup — but the sidecar is only written on success (line 221), so a failed conversion leaves no misleading state. The markdown file IS written before inspection (line 218) — if the parser succeeds but inspection fails, the markdown is kept for diagnosis (deliberate, documented: "kept so the failure can be inspected rather than re-run blind").

**No findings against `convert.py`.**

---

## Priority 7: Note Field Accuracy

I spot-checked notes across 12 files (EAU prostate, NCCN prostate/bladder, AUA prostate-advanced/salvage/localized/early-detection, ESMO bladder/prostate-advanced, EAU-ESMO bladder, EAU bladder-MIBC/NMIBC, WHO/IARC, AFU prostate).

### Notes that are load-bearing clinical reasoning

Every rule file I checked has notes on rules that make clinical distinctions. Examples:

- **EAU prostate 6.3.3** (high-risk localized): "v4.0 routed 6.3.3.a and 6.3.3.b to intermediate risk because their prose mentions intermediate risk, inside a chapter whose title says high. The chapter wins."
- **AUA salvage METHODOLOGY**: "Leaf-matched on purpose. `GUIDELINE STATEMENTS` and twelve passages of clinical content sit beneath this heading because of a conversion error; a chapter-wide exclusion would delete them."
- **NCCN bladder NMIBC before MIBC**: "NCCN writes `Non–Muscle-Invasive Bladder Cancer` with an en dash, and `Muscle-Invasive Bladder Cancer` sits inside it."
- **ESMO bladder UTUC**: "Leaf-matched, so the advanced-disease sections the conversion filed beneath `**UTUC**` are untouched — they are about urothelial carcinoma generally."

### No note/rule mismatches found

For every spot-checked file, the notes accurately describe what the rules do. The clinical reasoning is explicit and falsifiable — a clinician could read the note and the rule and disagree with the routing without being confused about what the routing IS.

### The one verified claim from the prompt

The prompt says: "Three commit messages assert that the substring-ordering trap is something 'no test would catch.' That is false."

The note in `aua.prostate-advanced.2026.json` says: "Reversing those two rules would silently file every nmCRPC recommendation under metastatic disease — the exact error the new setting exists to prevent, and one that no test would catch because both values are valid."

This claim is **partially true and partially false**:
- **True**: No test catches *reversed ordering* if the broader rule comes first and the specific one is shadowed but the shadowing is *incomplete* (the partial-shadowing gap).
- **False**: The audit DOES catch *total* shadowing (as the test `test_a_rule_shadowed_by_an_earlier_one_names_the_rule_that_shadows_it` demonstrates). If the specific rule matched nothing after reversal, the audit would report it dead.

**Finding**: The note's claim is technically imprecise but practically correct for the specific case — reversing `NON-METASTATIC CASTRATION-RESISTANT` and `METASTATIC CASTRATION-RESISTANT` would cause partial shadowing (nmCRPC headings would be claimed by the mCRPC rule), and the audit would NOT catch it because the broader rule still has valid mCRPC headings to claim. The note should say "no test would catch because both values match valid headings" rather than "both values are valid."

---

## Additional Findings

### 1. ESMO bladder: NMIBC/MIBC substring pair

My shadowing analysis flagged `NMIBC` (rule[17]) as a substring of `MIBC` (rule[18]) in `esmo.bladder.2022.json`. Both are `match_leaf: true`. After normalisation, `mibc` is not a substring of `nmibc` — wait, actually it IS: `nmibc` = "nmibc", `mibc` = "mibc", and "mibc" IS a substring of "nmibc". But `NMIBC` comes first (rule[17]), NOT `MIBC` (rule[18]). So `NMIBC` claims its paths, and `MIBC` claims the rest. This is correct ordering — same pattern as the other substring traps.

But the note at rule[17] says: "Stated after the more specific NMIBC rules above so it only reaches leaves they did not claim." This is misleading — rule[17] claims ALL NMIBC leaves (including ones rule[6-9] already claimed), but since rule[6-9] come first and have the same setting, the net effect is harmless. The note describes the intent correctly even if it misstates the mechanism.

### 2. `afu.bladder-tvnim`: the `tres haut risque` / `haut risque` pair

The sections fixture shows that `tres haut risque` paths use accented characters (`très`), while the rule uses unaccented (`tres`). The normalise function correctly handles this — accents are stripped. The baseline confirms the routing is correct.

### 3. No test for fixture drift

As noted in Priority 2, there is no automated test that compares a fresh parse against committed fixtures. This is a known gap but not blocking — the fixtures are committed and the author verified by hand.

---

## What the Author Most Wanted: "The place where this corpus says something that is not true"

**I did not find one.** Every rule file was checked against its sections fixture and baseline. Where substring traps exist, they are explicitly documented and correctly ordered. Where exclusions exist, the excluded headings match their stated reasons. Where notes make clinical claims, the rules implement them.

The two known defects (EAU 6.7.15 and NCCN M0 CRPC) were already fixed before this review.

The aggregate questions (49% `not_specified`, 40% exclusion, single-passage settings) are real measurement questions, not errors. They cannot be settled without the retrieval system being wired end-to-end — which is known debt, recorded in `decisions.md`.

---

## Recommendations (not changes, per review instructions)

1. **Post-build**: Add a test that parses freshly and compares against committed fixtures to close the fixture-drift gap.
2. **The partial-shadowing one-off check**: Consider making it a test. The logic already exists in my `shadowing_check.py` — a test that asserts all substring pairs are either same-setting, leaf-matched, or exclusion rules, and flags the rest for human review.
3. **The `nmCRPC` / `mCRPC` note in `aua.prostate-advanced`**: The claim "no test would catch" is imprecise. The more precise statement is: "no test catches partial shadowing; total shadowing is caught." The note in `nccn.bladder` says it better: "Both values are valid, so nothing would have complained."
4. **EAU 6.7.13 and 6.7.14**: The prompt asks for an opinion on whether these chapter-wide summaries should be routed to metastatic or span both M0 and M1. From the data: the chapter title is "Treatment: Castration-Resistant" and the rule routes to metastatic. The note for rule[21] (`6.7 Treatment: Castration-Resistant`) routes to `prostate_metastatic_castration_resistant` without comment on M0/M1 spanning. The two specific summaries (6.7.13, 6.7.14) are under that parent and will be claimed by rule[21]. If they genuinely span M0 and M1, they should have their own `spans_settings` rules. **Recommendation**: check whether these two sections mention apalutamide or darolutamide (the M0 CRPC drugs). If they do, they span settings and need their own rule.