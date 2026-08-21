# Corpus v5 change request — the unified corpus, and a manifest that proves coverage

Written 21 August 2026, after your account of how the validation slice became the corpus.

**Your analysis is accepted in full, and the slice was our request.** `corpus-v2-change-request.md`
line 134 says *"Do not retag all 38 documents before we can retrieve through the new schema"*
and names the four. That was a reasonable call. What neither of us wrote down was the step
after it, so the slice became the corpus by default across five releases.

**The failure to notice is ours, and it is worse than the naming.** Our loader reads
`documents.jsonl`, `chunks.jsonl`, `chunk.schema.json` and `routing.json`. It has never read a
manifest of any kind. Every guard we have built refuses a release that *contains* something
wrong — an unknown field, an unconstrained enum, a changed text-search configuration. **Nothing
refuses a release that is silently missing a document.** Your `source-manifest.json` covering
only the four SIOG sources would not have been detected by us either way.

That is the same failure this project keeps finding — something real exists, does not arrive,
and no declared expectation exists for it to violate — at the level of whole documents, for
five releases. Part 1 is the fix, and most of it is ours.

---

## Part 1 — A manifest that is a coverage proof

**Blocking. Nothing else in this request should ship before it.**

`manifest.json` must account for **every** reference folder and every document in it, whether
or not it enters the retrieval corpus. Not an incremental list of what changed — a complete
statement of coverage, so that a document we both forget about becomes visible as an omission
rather than as silence.

```json
{
  "release_id": "rcpia-corpus-v5.0",
  "generated_at": "2026-…",
  "reference_folders": [
    {
      "folder": "ref-01",
      "documents": [
        {"source": "EAU_2026_Prostate_Cancer.md",     "disposition": "corpus",
         "document_id": "eau.prostate.2026", "chunks": 443},
        {"source": "EAU_2026_Bladder_NMIBC.md",       "disposition": "corpus",
         "document_id": "eau.bladder-nmibc.2026", "chunks": 0},
        {"source": "EAU_2026_Bladder_MIBC_Metastatic.md", "disposition": "corpus",
         "document_id": "eau.bladder-mibc.2026", "chunks": 0}
      ]
    },
    {
      "folder": "ref-09",
      "documents": [
        {"source": "Questionnaire_G8.md", "disposition": "tool",
         "tool_id": "g8", "note": "Scoring rules are executable. SIOG guidance on what a
                                   low G8 implies is separately in the corpus."}
      ]
    },
    {
      "folder": "ref-26",
      "documents": [],
      "note": "empty — confirmed, not overlooked"
    }
  ]
}
```

`disposition` is one of **`corpus`**, **`tool`**, **`calculator`**, **`excluded`**. Anything
other than `corpus` carries a `note` saying why. An empty folder is stated as empty, because
"nothing here" and "nobody looked" must not render identically.

**We will make our loader check it**, and refuse a release whose manifest does not account for
every folder, or whose `corpus` entries do not match the documents actually delivered. That is
our work, and it is the change that makes this class of failure impossible rather than merely
noticed. It is tracked alongside `KNOWN_CHUNK_FIELDS` and the configuration fingerprints, which
are the same idea one level down.

---

## Part 2 — The retrieval architecture this corpus has to serve

**You have not seen this, and it changes what we need from you.** It comes from an independent
review of our retrieval design, by an agent with no knowledge of the corpus work. We are
relaying it because it changes what a corpus release has to carry, not merely which documents
it contains.

### The requirement

Our clinician was asked what a provisional document should do when AFU, EAU and NCCN disagree.
The answer:

> détecter le conflit → récupérer toutes les positions → les afficher clairement → laisser la
> RCP trancher et enregistrer son choix

**The document never picks a side.** Where the guidelines diverge, the divergence is itself the
finding.

### Why the current pipeline cannot do this

Our retrieval fuses both search legs into one ranked list and returns the best ten. Measured on
the loaded corpus, for a prostate question about active surveillance:

```
eligible passages   nccn 48    eau 23    afu 18
returned in top 10  nccn  8    afu  2    eau  0
```

EAU disappears. Not because it says nothing — it has 23 eligible passages — but because a
single global ranking makes families compete for the same ten slots, and the family with the
most material on a topic takes most of them. If EAU's position differed from NCCN's, the
document would present a consensus that does not exist, with real citations, and nothing
anywhere would signal the omission.

### The architecture

```
Clinical question
      ↓
Safety filters (unchanged — cancer type, disease setting, eligibility)
      ↓
Retrieve candidates separately for each applicable guideline family
      ↓
Rerank within each family
      ↓
Build one structured position per family
   — or record explicitly: "no relevant position found for EAU"
      ↓
Compare positions
   → consensus | partial_divergence | conflict | insufficient_evidence
      ↓
The document presents every position neutrally and synthesises none
      ↓
The RCP chooses, and the choice is recorded
```

The unit stops being *the ten best passages* and becomes *every applicable position, supported
by enough passages*. Never force weak evidence in to fill a slot — an absent position is
recorded as absent, which is information.

This path applies to questions that can produce a clinical recommendation. We can route on
`clinical_purpose`, which we now filter on: `therapy_indication`, `modality_selection`,
`treatment_delivery`. Background questions keep the existing global retrieval.

### What it needs from the corpus — five things

**1. `source_family` as a first-class field.**

We currently derive it by splitting `document_id` on `.` — `nccn.prostate.6.2026` → `nccn`.
That works by accident of your naming and will break the first time a document is named
differently. If retrieval is to stratify by family, the family has to be a declared property
rather than a string convention.

**2. A stated distinction between a guideline authority and a specialist reference.**

This one is load-bearing. AFU, EAU, NCCN — and ESMO and AUA when they arrive — are authorities
that can hold a position on treatment choice. SIOG is not: its entire prostate footprint is
`geriatric_frailty`, 96 passages of 96. WHO/IARC is a classification. RECIST is a measurement
convention.

If SIOG enters a positions comparison for a treatment question, the comparison step will report
a divergence that does not exist — **SIOG is not disagreeing, it is answering a different
question.** That is a false conflict shown to a clinician, which is the failure most likely to
make them stop trusting the mechanism.

`document_type` separates them today, but by accident:

```
afu / eau / nccn    guideline
siog                consensus_guidance
```

That happens to work for ten documents and will not survive ESMO, AUA, WHO/IARC and RECIST.
Please state it as a property, with values you choose and define — something like
`position_bearing: true|false`, or an explicit `source_role`.

**3. A position must be assemblable from the chunks.**

A guideline position is not one sentence. It is *the recommendation*, plus *who qualifies for
it*, plus *the caveat*. Dossier 2's patient is 85 with a DFG of 30, so the qualification is the
part that decides the case.

If chunking separates a recommendation from its eligibility criteria into two chunks with
nothing linking them, we can retrieve one without the other and build a position that is
confidently wrong. Either keep a recommendation with its conditions in one chunk, or give us a
link — a shared identifier, a parent section, `recommendation_number` used consistently.

Tell us which you can do. This is the requirement we are least sure how to specify, and you
know the source structure.

**4. Comparable coverage per family, per topic.**

If EAU covers a topic across twelve chunks and AFU in a single line, a comparison will read as
though EAU has a richer position — when the difference is chunking, not clinical substance.

We are not asking you to equalise anything. We are asking for **a coverage report**: for each
major topic, how many position-bearing chunks each family contributes. It lets us see when a
comparison is skewed by the corpus rather than by the guidelines.

**5. Known divergences, if you can see them.**

Optional, and valuable. If while processing you notice that two families genuinely differ on
something — different thresholds, different first-line choice, one recommending what another
advises against — flag it in the release.

Two uses. It gives us labelled material for the evaluation, which otherwise depends on a
clinician annotating disagreements by hand. And it gives us a check on our own detection: if
our comparison step misses a divergence you flagged, we have a measurable failure rather than
a suspicion.

Do not go looking systematically. Report what you encounter.

### What we are not asking you to do

**Do not detect conflicts as a deliverable.** Deciding that two passages contradict each other
is a clinical judgement, and it belongs to an agent that reads them together with a clinician
reviewing the result. What the corpus supplies is the possibility: every family's position
retrievable, correctly attributed, correctly typed.

---

## Part 3 — Three dispositions, and one correction to how we framed them

Your three-way split is right. Our first draft of this request got the weighting wrong for one
of the three, and we corrected it after reading a real dossier rather than reasoning about one.

### The correction: the scores arrive already calculated

We had proposed that scoring instruments become `tool` — executable calculation — with their
interpretive guidance as a secondary `corpus` deliverable "where a source exists".

That is backwards. Here is dossier 2, an 85-year-old woman with muscle-invasive bladder cancer,
as the clinician submitted it:

```
État général score Pallia 10   : ≥ 4
Score G8 (si ≥ 75 ans)         : 5
État nutritionnel (score MNA)  : 7
Autonomie (échelle ADL)        : 4
État de l'érection (IIEF5)     : RAS
État de la miction (IPSS)      : RAS
OMS                            : 1
DFG                            : 30
```

**The scores are filled in by the clinician before the dossier reaches us.** Our ledger already
has a field for every one of them — `pallia10`, `g8_score`, `mna`, `adl`, `iief5`, `ipss`,
`who_status`, `egfr` — and extraction already reads them.

So computing a score is the rare case, not the common one. **What the system cannot do is say
what the numbers mean.**

### `corpus` — retrievable guidance, and this is where the instruments mostly belong

Text an agent may cite. The test is whether a clinician could read the passage aloud in an RCP
and have it mean something: *what to do*, *for whom*, *with what caveat*.

For every instrument, this is now the **primary** deliverable. Not the questionnaire, not the
scoring table — the guidance that gives a number consequences:

| We have | We need to be able to cite |
|---|---|
| `G8 : 5` | what a G8 well below the cutoff implies, and how it changes treatment intent |
| `MNA : 7` | what malnutrition at this depth means for tolerance of surgery or chemotherapy |
| `ADL : 4` | what loss of independence changes about a radical treatment decision |
| `Pallia 10 : ≥ 4` | what triggers palliative involvement, and what that involvement is |
| `DFG : 30` | what is contraindicated at this clearance, and what replaces it |
| `OMS : 1` | how performance status interacts with the geriatric measures above |

An agent that writes *"this patient is not a candidate for cisplatin"* with no citation is doing
precisely what the blinded expert evaluation scores us down for — conformity was the largest
measured deficit, at 33% worse than the human fiche.

### `tool` — deterministic computation, as a fallback

Scoring rules still belong in code rather than a vector index: an agent should never *retrieve*
how to add up seven items. We already have this layer — `rcpia/rules/` — and it already carries
the concept that matters:

```python
class Authority(StrEnum):
    clinician_approved = "clinician_approved"   # may set the checkbox
    project_suggestion = "project_suggestion"   # may suggest, labelled unvalidated
    none               = "none"                 # may not even suggest
```

**And here is the interlock.** `referrals.py` raises an oncogeriatric assessment at `G8 ≤ 14`.
That rule sits at `project_suggestion` — it may suggest but never decide — **for exactly one
reason: no guideline in the corpus supported the threshold.** The tool computes; the corpus is
what licenses the tool to be believed. Deliver the guidance and the rule can be promoted.

So: deliver the calculation rules, but treat them as the smaller half. They matter when a score
is absent and derivable, which dossier 2 shows is not the usual case.

### `calculator` — structured lookup, not semantic retrieval

Nomograms, TNM tables, RECIST response categories. Lookups and formulas where retrieval would
be actively wrong — asking a vector index for a Briganti probability is a category error.

Distinguish `calculator` from `tool` in the manifest anyway, because they will be implemented
differently and we would rather you tell us which you think each is.

### `excluded`

With a reason. `excluded` and "never considered" must not look the same, which is the whole
point of Part 1.

---

## Part 4 — What we ask for, in priority order

The order below changed after reading dossier 2. It is not an unusual case — an 85-year-old
with comorbidity is ordinary in a urology RCP — and it is dominated by the axes where our
corpus is thinnest.

### What that dossier exposed

pT2 high-grade urothelial carcinoma, muscle invasion confirmed, 43×41mm, invading the left
ureteral meatus with hydronephrosis. Two years earlier it was pT1 with *"muscle non vu"* and
*"mal suivie"*.

The textbook answer is neoadjuvant cisplatin then radical cystectomy. A DFG of 30 puts cisplatin
out of reach. Age 85 with G8 5, MNA 7 and ADL 4 makes radical cystectomy a different proposition
entirely. **The guideline's headline recommendation is largely unavailable, and the case turns
on geriatrics, nutrition, renal function and supportive care.**

What the corpus holds for those:

```
geriatric_frailty       155   SIOG only — and our SIOG prostate document is from 2010
renal_safety            109   fragments of urology guidelines, no nephrology source
palliative_supportive    45   27 of which are NCCN prostate sections mentioning it in passing
nutrition                 —   no source at all; ESPEN sits parsed and unloaded in ref-17
bladder, EAU               0   both guidelines parsed and unloaded
```

### 4.1 EAU bladder — highest priority

```
ref-01/EAU_2026_Bladder_NMIBC.md              Gontero et al., EAU 2026
ref-01/EAU_2026_Bladder_MIBC_Metastatic.md    van der Heijden et al., EAU 2026
```

Both parsed, both beside the EAU prostate file already in the corpus — same publisher, same
edition, same folder, same parse date.

Bladder is half this product's scope and has no EAU material at all. Beyond coverage, this
blocks design work: we are building the behaviour the clinician specified for when guideline
families disagree — *détecter le conflit → récupérer toutes les positions → les afficher
clairement → laisser la RCP trancher*. On prostate that means three authorities. On bladder it
currently means two, and building the comparison logic against two would bake in an assumption
about to change.

We also stated in writing that "EAU has no bladder material." That was wrong. We read the
database and described the world.

### 4.2 Interpretive guidance for the scores the dossier already carries

**New, and now second in priority.** This is the material that turns a number into a
recommendation an agent can cite.

| Source | Serves | Note |
|---|---|---|
| ESPEN clinical nutrition in cancer (`ref-17`) | MNA, weight loss, albumin | no nutrition source exists in the corpus at all |
| ASPEN, if it adds anything ESPEN does not | same | your call — we would rather one good source than two overlapping |
| Pallia-10 guidance (`ref-24`) | Pallia-10 | the instrument is present; what a score ≥ 4 obliges is not |
| Fried frailty | frailty phenotype | computable, but its clinical implications are guidance |
| An oncogeriatric source — see 4.5 | G8, ADL, comprehensive assessment | currently SIOG alone, prostate document from 2010 |
| A renal source | DFG, cisplatin eligibility, dose adjustment | `renal_safety` is served entirely by fragments |

On the renal one specifically: we do not know what the authoritative source is for
platinum eligibility and dose adjustment in impaired clearance, and whether it should be an
onco-nephrology guideline or a chapter within the urology guidelines. **Recommend one; do not
add it unilaterally.** Adding a guideline family is a clinical scope decision and it goes to our
clinician.

### 4.3 The rest of the previously engineered documents

Your list of 34, with our reading of each. Where we disagree with a disposition, say so — you
have seen the sources and we have not.

| Documents | Proposed disposition | Note |
|---|---|---|
| 2 EAU bladder | `corpus` | 4.1 |
| 5 ESMO (one duplicate) | `corpus` | a fourth authority; resolve the duplicate |
| 15 AUA | `corpus`, **curated** | agreed — algorithms, summaries and unabridged versions overlap. Deliver the unabridged only, unless you judge otherwise |
| 2 WHO/IARC pathology | `corpus` | `pathology` is served by fragments of urology guidelines today |
| 2 RECIST | `calculator` + `corpus` | response categories are a lookup; narrative guidance is retrievable |
| 1 TNM (provisional) | `calculator` | staging is a lookup — and flag that it is provisional |
| 7 score instruments | `corpus` **first**, `tool` second | see Part 3; the interpretation is the deliverable that matters |

### 4.4 Never-integrated references

| Reference | Proposed | Why |
|---|---|---|
| Briganti, MSKCC nomograms | `calculator` | formulas, not text |
| ECOG/WHO performance status | `tool` + `corpus` | already used by our rules; how it interacts with geriatric measures is guidance |
| PI-RADS | `calculator` + `corpus` | categories are a lookup; reporting guidance is retrievable |
| ESPEN / ASPEN nutrition | `corpus` | 4.2 |
| Pallia-10 | `corpus` + `tool` | 4.2 |
| GGPO / SFFPO psycho-oncology | **decision pending** | 4.6 |

### 4.5 Oncogeriatrics — an open question we are putting to you

`geriatric_frailty` rests on SIOG alone, and the SIOG prostate document we hold is from 2010 —
we have already had to block 17 of its passages as superseded treatment guidance.

Our clinician was asked whether SIOG or SoFOG should be the reference and asked back which is
more comprehensive. Our reading is that SIOG is broader in scope and international, while SoFOG
is French-language and aligned with francophone practice — and that they may not be
alternatives, since one supplies methodology and the other local application.

**Three things we would like from you**, since you have seen the sources:

1. Is there a more recent SIOG prostate document than 2010? We believe a task force update
   exists around 2017 and we do not have it.
2. Is SoFOG material available and of comparable quality?
3. Given that 81% of the corpus is English and every question is French, does a French-language
   geriatric source carry weight beyond its content?

Recommend. The choice goes to our clinician.

### 4.6 Psycho-oncology — not ours to decide, and not yours

`ref-23` holds 28 SFFPO documents, plus GGPO in `ref-21` and `ref-22`. Substantial, coherent,
largely French.

**It maps to no agent role the corpus defines.** Ours are:

```
urology_oncology 659   medical_oncology 283   pathology 233   radiology 206
geriatric_frailty 155  radiation_oncology 141 renal_safety 109
nuclear_medicine 99    genetics_molecular 88  palliative_supportive 45
```

Loading psycho-oncology means adding a role, which means adding a panel member, which is a
decision about what an RCP fiche should cover. That belongs to our clinician. **Please do not
add it unilaterally, and do not drop it either** — carry it in the manifest as `excluded` with
`"reason": "pending clinical scope decision"` so that it stays visible.

The same applies to AUA if you judge it redundant with NCCN rather than a fourth position.

---

## Part 5 — What we need in the release itself

1. **A complete `manifest.json`** as in Part 1.
2. **A checksum.** v3 and v3.1 carried one; v4 did not. It has caught nothing so far, which is
   what a checksum is for.
3. **A per-document chunk count** in the manifest, so a release that delivers a document with
   zero chunks is visible as a defect rather than as an absence.
4. **The name.** `rcpia-corpus-v5.0` should mean the unified corpus. If v5 is again partial,
   name it for what it is — the honest name is what makes the next omission visible.

---

## Part 6 — Two things you should know about our side

**Our loader will refuse a release that does not account for every folder.** That is the change
Part 1 asks for and we are making it. Expect a release to be rejected during the transition if
the manifest is incomplete; tell us if the shape above is impractical before you build to it.

**We are about to measure retrieval quality, and we will state which release we measured.** The
evaluation is designed and not yet built. You are right that a measurement against v4 would not
represent the full knowledge base, and its report header will name the release and the document
count so that no number gets quoted as though it described a corpus it never saw.

We are not waiting for v5 to build it. The harness, the metrics and the trace do not depend on
corpus scope, and the evaluation's per-document breakdown is precisely the instrument that
would have surfaced this gap — bladder recall against two families and prostate against four,
with the question of why. Building it after the consolidation would mean it had never been
tested against a corpus we know to be incomplete.

The gold set — the clinician-labelled part, the expensive part — waits for v5.
