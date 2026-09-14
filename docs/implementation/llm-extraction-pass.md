# LLM extraction pass: filling the gaps the deterministic extractor leaves

**Status**: Not built. This document describes the problem, the design, and the implementation
task.

---

## Runtime correction: explicit unknown values (September 2026)

The historical design below is retained for context. The implemented intake now shares
`fact_schema.is_unknown_value` across deterministic/LLM resolution, ledger projection,
mandatory completeness checks, retrieval exclusion and nomogram parsing.

An empty value or a **whole** not-recorded marker (for example `Non renseigné`,
`non renseignée`, `inconnu`, `unknown`, `not documented`, `NR`) is missing, never `Non`,
zero or a normal finding. Case, accents, whitespace and surrounding punctuation are
normalized only for recognition. A report containing these words elsewhere is not erased.
Explicit negatives remain answers; `RAS` keeps its existing field-dependent meaning.

New machine and human placeholder facts retain their literal value, source quotation or
correction note, and provenance, but have missing status. Live ledger reads and
`entries_from_version` apply the same non-mutating projection to historical snapshots,
including old `confirmed` values: delivered value `null`, status `missing`, red highlight,
with the original wording in readings and an explanatory note. Existing facts, versions
and historical run bundles are not rewritten. Newly built bundles, retrieval, calculators
and the PPS missing-information view consume projected entries.

Projection considers all effective readings, not just the historical representative value.
An unknown reading does not contradict a known answer; two opposing known answers remain
amber and appear in the bundle's structured conflicts even if an unknown reading came first.
Human corrections replace the effective answer without resurrecting superseded readings.
The LLM guard likewise distinguishes an unknown form box from a separate quoted conclusion:
the proposed answer must occur outside the unknown box and outside other fields' readings.
The complete verified quotation is retained, rather than dropping a supporting conclusion
after the first 400 characters; chunk traceability is still checked before storing it.

Unknown mandatory fields block both single-agent and panel POST dispatch. The requested
snapshot is checked as well as the live ledger; an older queued/running job is not silently
reused after a correction. A valid correction appends a fact and version, while an unknown
correction leaves the gate closed. Human nomogram eligibility and clinical-versus-pathological
stage rules are unchanged.

Synthetic regression coverage: `tests/test_unknown_facts.py` (no database) and
`tests/test_unknown_facts_db.py` (private ephemeral PostgreSQL, existing fake Pi only).

---

## Background: how dossier extraction works today

RCPia reads patient dossiers through a deterministic label matcher
(`apps/api/rcpia/cases/fact_schema.py`). It looks for labels it knows — `Créatinine :`,
`PSA totale :`, `Gleason :` — and extracts the value that follows the colon. If it finds a
value, it records it. If the label is absent, it reports the field as missing. If the value is
`RAS` (rien à signaler — nothing to report), it records `RAS`.

This design was chosen deliberately: every extracted fact can be traced to the exact line it
came from, a missing value is reported as missing rather than guessed, and the extraction is
deterministic — same dossier, same facts, every time.

The August 2026 dossier survey (`docs/reference/dossier-survey-2026-08-17.md`) already
identified the limitation: the extractor only recognizes labels it has seen. Across the 15
real dossiers, 86 labels went unrecognized because the catalogue was built from one dossier
and the other 14 filled the same form differently.

## The problem: `RAS` is not a value, and narratives are not read

The current extractor has two failure modes that compound each other:

### Failure mode 1: `RAS` is treated as a value

When a structured field says `RAS`, the extractor records it. "Nothing to report" is a form
convention. It is not a clinical measurement. A creatinine of `RAS` means the field was left
blank or marked not applicable — it does not mean the patient has no creatinine.

The agents already understand this. The v2 brain says: "Une donnée absente n'est pas normale.
Si le DFG n'est pas dans le dossier, ne supposez pas qu'il est normal. 'RAS' n'est pas une
valeur numérique." But the agent can only reason about what the extractor gives it. If the
extractor records `RAS` as the Gleason score, the agent sees a patient with no grade.

### Failure mode 2: The narrative is never read

The extractor reads structured fields — the form lines with labels and colons. It does not
read the narrative paragraphs where the clinician writes the diagnosis in prose.

Dossier 8 is the clearest case. Its narrative states:

> "Adénocarcinome acinaire Gleason : 9 (5+4), ISUP 5 avec effraction de la capsule
> prostatique, engainements péri-nerveux et emboles vasculaires"

The structured fields at the bottom of the form read:

```
Type histologique : RAS
Gleason : RAS
ISUP : RAS
```

The extractor read the structured fields and recorded `RAS` for all three. The query builder
could not derive a disease setting — you cannot stage a prostate cancer with no grade.
Retrieval returned nothing targeted. All three agents refused. A patient with documented
high-volume metastatic castration-resistant prostate cancer, Gleason 9, ISUP 5, progressing
on ADT plus abiraterone plus docetaxel, reached the tumour board with no document at all.

This is the single most serious defect in the system. It is not in any agent. It is in
extraction.

## The fix: a two-pass extraction

The deterministic extractor is the right first pass. It is fast, auditable, and never
hallucinates. But it cannot read prose. The fix is a second pass — an LLM that reads the
full dossier text and extracts facts the deterministic extractor missed.

```
Raw dossier
    │
    ├── Pass 1: Deterministic extractor (already exists)
    │   Reads structured labels it knows
    │   Returns facts it can read
    │   Reports what it couldn't find (missing, RAS, unrecognized labels)
    │
    ├── Pass 2: LLM extractor (what must be built)
    │   Reads the full dossier text
    │   Receives: facts already found, fields reported as missing/RAS
    │   Extracts additional facts from narrative prose
    │   Cites the exact sentence for every fact
    │
    └── Merge
        Deterministic facts take precedence
        LLM facts fill gaps
        Conflicts are recorded, not resolved silently
```

### Design constraints

1. **The LLM runs only on fields the deterministic extractor could not fill.** It receives the
   list of fields reported as missing, `RAS`, or unrecognized. It does not re-extract fields
   the deterministic extractor already found. Deterministic facts take precedence in the merge.

2. **Every extracted fact must cite its source sentence.** The LLM must quote or reference the
   exact sentence it read the value from. A human must be able to verify the extraction. This
   is the same discipline as the agent citing guideline passages.

3. **The LLM must not invent facts.** If a value genuinely does not appear in the dossier, it
   stays missing. The LLM reports "not found in the dossier text" rather than guessing.

4. **`RAS` is a signal to look harder, not a value to accept.** When the deterministic
   extractor reports `Gleason: RAS`, the LLM should search the narrative for the actual
   Gleason score. `RAS` means the structured field was left blank — the value may exist in
   prose elsewhere in the document.

5. **The prompt must be generic.** It works on any dossier — bladder or prostate, initial
   diagnosis or recurrence, French or mixed language. It receives the full dossier text, the
   facts already found, and the list of fields to search for. It is not tuned to dossier 8.

6. **Conflicts are recorded, not resolved silently.** If the deterministic extractor found
   `cT2a` and the LLM extracts `cT3a` from the imaging report, both values are kept. The
   conflict is surfaced in the ledger. The agent decides which to trust — the extraction
   pipeline does not pick a winner.

### What the LLM can extract that the deterministic extractor cannot

- Values written in narrative paragraphs rather than structured fields
- Values under labels the deterministic extractor doesn't recognize (the 86 from the August
  survey)
- Values where the structured field says `RAS` but the narrative states the diagnosis
- Clinical facts spread across multiple sentences ("le patient a été traité par triptoréline
  puis abiratérone" — treatment history, not a single label)
- TNM stage described in prose rather than in a `cTNM :` field ("organo-confinée, sans
  extension extracapsulaire" → cT2)
- Prior treatments, progression status, and clinical context that the structured form captures
  incompletely

### What the LLM must not do

- Change a fact the deterministic extractor already found
- Invent a value when the dossier is genuinely silent
- Produce a value without a source citation
- Produce different values on different runs for the same dossier (the prompt should request
  deterministic behavior: temperature 0 or very low)

---

## Implementation task

Build the LLM extraction pass that runs after the deterministic extractor.

### Step 1: Build the extraction module

Create `apps/api/rcpia/cases/llm_extractor.py` with a single entry point:

```python
async def extract(
    dossier_text: str,
    deterministic_facts: dict[str, Fact],
    missing_fields: list[str],
) -> LLMExtractionResult
```

`LLMExtractionResult` contains:
- `extracted_facts: list[ExtractedFact]` — each with `field`, `value`, `source_sentence`,
  `confidence`
- `still_missing: list[str]` — fields the LLM also could not find
- `conflicts: list[FieldConflict]` — fields where the LLM found a different value than the
  deterministic extractor

### Step 2: Write the LLM prompt

The prompt is generic. It works on any dossier. The model receives:

1. The full dossier text
2. The facts the deterministic extractor already found (so it doesn't duplicate work)
3. The list of fields reported as missing, `RAS`, or unrecognized (so it knows what to look
   for)

It returns a structured JSON array of extracted facts, each with the field name, the value,
and the exact sentence it read the value from.

The prompt must instruct the model:
- Only extract fields from the provided missing/RAS/unrecognized list
- Do not change or re-extract facts the deterministic extractor already found
- For every fact, quote the source sentence verbatim
- If a field genuinely does not appear in the dossier text, mark it as still_missing — do
  not guess
- `RAS` in a structured field means the field was not filled — search the narrative for the
  actual value
- Return only the JSON structure described below, no other text

### Step 3: Wire it into the extraction pipeline

The extraction pipeline (`apps/api/rcpia/cases/`) currently runs the deterministic extractor
and produces a ledger. Modify it to:

1. Run the deterministic extractor (unchanged)
2. Collect the list of fields reported as missing, `RAS`, or unrecognized
3. If that list is non-empty, run the LLM extractor
4. Merge: deterministic facts win, LLM facts fill gaps, conflicts are recorded
5. The merged ledger is what the query builder and agents receive

### Step 4: Measure

1. **Dossier 8 first.** It should go from producing no document to producing a fiche for
   metastatic castration-resistant prostate cancer. The LLM must extract Gleason, ISUP, and
   histology from the narrative.

2. **All 15 dossiers.** Report:
   - How many previously-missing/RAS fields did the LLM fill per dossier?
   - How many fields remained missing after both passes?
   - Did any LLM-extracted fact conflict with a deterministic fact?
   - Did the LLM ever produce a value without a source citation?
   - Did dossier 8, and any other dossier that previously failed, now produce a fiche?

3. **Latency and cost.** The LLM extraction is an additional API call per dossier. Report the
   time and token cost.

### What not to do

- Do not remove or replace the deterministic extractor. It is the first pass and its facts
  take precedence.
- Do not tune the prompt to dossier 8. It must work on any dossier.
- Do not have the LLM re-extract facts the deterministic extractor already found. That wastes
  tokens and creates unnecessary conflicts.
- Do not resolve conflicts automatically. Surface them in the ledger.

---

## Update, 30 August 2026: the deterministic half is done, and it does **not** recover dossier 8

Two changes were made first, on the argument that part of this fix needed no model:

1. **`RAS` on a field where absence cannot be an answer is now recorded as missing**, at the
   point the ledger is assembled rather than in a completeness check that runs afterwards.
   `ABSENCE_IS_NOT_AN_ANSWER` in `fact_schema.py` covers histology, Gleason, ISUP, cTNM, pTNM and
   muscle invasion — the last found by a test that checks the set against `BLOCKING_FIELDS`
   instead of against a hand-written list. The form's wording is preserved in the entry note,
   because *the box said RAS* and *nobody filled the box* are different facts about a dossier.

2. **Fifteen labels were added** (91 → 106 recognised): digital rectal exam, hormone therapy,
   ureteral stents, bilirubin fractions, platelets, GGT, bicarbonate, phosphorus, uric acid,
   vitamin D. The remaining unrecognised label texts are report structure — `Conclusion`,
   `Technique`, `À l'étage thoracique`, `Latéralement` — and must stay unrecognised, or every
   radiology paragraph becomes a fact.

**Dossier 8 still produces no document.** All three agents refuse, twice each. The refusal is now
honest — *"Type histologique : information manquante"* rather than *"la valeur enregistrée
(« RAS ») ne constitue pas une donnée exploitable"* — but the outcome is identical.

**And one claim in the argument for doing this first was wrong.** It was suggested that `RAS`
might have been mis-staging patients by being parsed as a value. It was not:
`parse_stage("RAS")` and `parse_gleason("RAS")` already returned `None`, so the query builder was
never fooled. What the change bought is that every reader of the ledger now agrees — the fiche,
the completeness check, the query builder and the agents all see one absence — not a correction
of a staging error that was never occurring.

**What this means for the pass described above.** It is not an optimisation on top of label work.
Dossier 8's *Gleason 9 (5+4), ISUP 5* exists only inside a narrative sentence, and no label
catalogue reaches it. **The LLM pass is the only thing that will produce a document for that
patient**, and the measurement in Step 4 should expect the deterministic changes to have moved
nothing on that dossier.

Two design notes from the code, for whoever builds it:

- **Conflicts should reuse `LedgerEntry.readings`**, not a new `FieldConflict` type. The ledger
  already models several readings per field, each with its own source excerpt, and the bundle
  already surfaces contested fields to agents from it. A second mechanism would reach the agent
  by a different path and one of the two would be forgotten.
- **`confidence` needs a consumer or it should go.** Nothing in the design reads it, and a float
  nobody acts on will eventually be thresholded by someone guessing.
- **Provenance must reach the fiche**, not only the log. A value read from prose by a model is
  not the same kind of fact as one read from a filled field, and the clinician holding the page
  is the person who needs to know which it was.
