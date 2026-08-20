# Change request — RCPia corpus v2 → v3

For the agent that built the corpus. Follows `docs/reviews/corpus-v2-change-request.md`,
which you delivered against. This request is based on the six-document validation slice as
**actually loaded into PostgreSQL** — 6 documents, 1,262 chunks — not on a reading of the
files. Every number below is a query against that database, and the queries are given at the
end so you can reproduce and challenge them.

---

## What v2 fixed — please do not regress it

You closed most of the v2 findings, several of them completely:

| v2 finding | Then | Now |
|---|---|---|
| `disease_setting` multi-valued, 13% self-contradictory | list, exclusive claims | single enum, **0** contradictions |
| `urology_oncology` on 99% of chunks | 1,932 / 1,957 | 658 / 1,262 (52%) |
| `renal_safety` role absent | 0 chunks | 107 |
| nuclear medicine role absent | 0 chunks | 101 |
| `diagnostic_workup` on 48% of chunks | one blanket tag | split into `diagnosis` 16% + `staging` 16% |
| NCCN absent | missing | 2 documents, 586 chunks |
| Citable locators | missing | `page_start`, `recommendation_number`, `citation_label` present |

The single-valued `disease_setting` in particular is what makes a correct filter writable at
all. Keep it.

---

## Part 1 — Blocking: the corpus contains diseases we do not treat

The NCCN "Bladder Cancer" guideline also covers **upper tract urothelial carcinoma** and
**carcinoma of the urethra** in the same PDF. Those sections were chunked and shipped:

```
38 chunks matching 'upper tract urothelial | UTUC | renal pelvis | carcinoma of the urethra'
    -> cancer_types = ['bladder']   (all 38)
    -> disease_setting = 'not_specified'
```

Tagged as bladder cancer and specified for no setting, they pass **every** bladder filter we
can write. A patient with a bladder tumour can be shown nephron-preserving surgical criteria
for a tumour of the renal pelvis — a different organ and a different operation, presented as
applicable evidence with a real page number attached.

This is the failure mode the v2 context section described: not a ranking mistake surfacing a
mediocre passage, but a filter mistake surfacing guidance written for a different disease. It
is the one finding here we consider blocking.

**Please choose one and tell us which:**

1. Exclude the UTUC and urethral sections from the bladder documents, or
2. Give them their own `cancer_type` values (`upper_tract_urothelial`, `urethral`) so our
   filter excludes them by construction.

Option 2 is better if you expect RCPia to cover those diseases later; option 1 is honest if
you do not. Either is acceptable. Silently tagging them `bladder` is not.

We will add a defensive check on our side at ingestion, but a validator working around your
labelling is not a fix, and we would rather not run two systems that disagree about what a
passage is about.

---

## Part 2 — `disease_setting = not_specified` on 52% of chunks

655 of 1,262. v2 section 3.4 noted the rate; what is new is that it is **not spread evenly**:

| Document | `not_specified` | Total | Rate |
|---|---|---|---|
| `nccn.bladder.3.2026` | 175 | 258 | **68%** |
| `nccn.prostate.6.2026` | 203 | 328 | **62%** |
| `eau.prostate.2026` | 265 | 443 | **60%** |
| `afu.bladder-tvim.2024-2026` | 12 | 37 | 32% |
| `afu.prostate.2024-2026` | 0 | 144 | **0%** |
| `afu.bladder-tvnim.2024-2026` | 0 | 52 | **0%** |

Two of the three French documents are at zero. The three English documents are at 60-68%.

This is what makes the finding actionable rather than a complaint about guideline prose: the
same pipeline produced perfect labelling on AFU and poor labelling on EAU and NCCN. Whatever
you did on AFU, please do on the other three.

**`not_specified` is a legitimate value and we are not asking you to eliminate it.** A
definition, an epidemiology paragraph, a statement of general principle genuinely applies to
every setting and should pass every filter. The problem is what is hiding among them.

**306 of the 655** carry `clinical_purpose = treatment_selection`. Treatment selection is
setting-dependent by definition — that is what the field is for. Three drawn at random:

- EAU 6.6.5.b.2 — *"The addition of abiraterone to ADT and docetaxel improved rPFS and OS in
  the PEACE-1 trial"*. That is metastatic hormone-sensitive disease.
- EAU 6.2.3.d.1 — *"Low-dose rate (LDR) BT uses radioactive seeds permanently implanted into
  the prostate... can be offered to patients with"*. That is localized, low or intermediate
  risk.
- EAU 6.2.1 — *"Two different strategies of expectant management are available: watchful
  waiting (WW) and active surveillance (AS)"*. That is localized disease.

**What we ask:** re-label EAU and both NCCN documents so that a chunk whose
`clinical_purpose` includes `treatment_selection` carries a real `disease_setting`, or else
carries a stated reason for remaining `not_specified`. Where a passage genuinely spans
settings — a drug's mechanism, a general principle of surgical technique — keep
`not_specified` and mark `clinical_purpose` accordingly rather than `treatment_selection`.

**Do not infer a setting you are not confident about.** v2 closed with *"leaving a field
`not_specified` and telling us is always better than inferring a setting that reads as
certain"*, and that still holds. We would rather have 40% `not_specified` that we trust than
10% that we do not.

---

## Part 3 — `section_path` integrity, which reaches the embeddings

`contextual_text` prepends `section_path` before the passage, and that is what we embed. A
wrong path is therefore baked into the vector, and it degrades the dense retrieval leg
specifically — the leg we depend on most, because 81% of the corpus is English and every
query will be French.

Two problems, both concentrated in EAU.

**Headings accumulating instead of nesting.** Two real paths:

```
6.2.2.f Acute and chronic complications of radical prostatectomy
  > Table 6.2.9: Selected studies of ADT in combination with, or without, RT for PCa
  > 6.2.3.d Brachytherapy
  > 6.2.3.d.1 Low-dose rate brachytherapy

CLINICAL FRAILTY SCALE > 6.2 Treatment modalities > 6.2.1 Expectant management strategies
```

In the first, 6.2.2.f is a *sibling* of 6.2.3.d, not its parent. In the second, the frailty
scale is not an ancestor of treatment modalities at all. A passage about brachytherapy is
being embedded under the heading for complications of prostatectomy.

We have not measured how widespread this is — the pattern is hard to detect mechanically, and
we would rather you audit it than we guess at a number. `eau.prostate.2026` has 271 chunks at
depth 3 or more and a maximum depth of 4, so there is a lot of path to get wrong.

**Table and figure captions used as headings.** Measurable: 34 chunks carry a heading matching
`^Table |^Figure `, 20 in EAU and 12 in NCCN bladder. A table caption is not a section.

**What we ask:** verify heading hierarchy against the source document structure for EAU and
NCCN, and exclude captions from `section_path`.

---

## Part 4 — Geriatrics went backwards

v2 section 3.2 reported `geriatric_frailty` on 5 chunks and noted that all five were the score
instruments themselves, not geriatric oncology guidance. In v2 the role is gone entirely:

```
urology_oncology       658
pathology              260
medical_oncology       253
radiology              212
radiation_oncology     140
renal_safety           107
nuclear_medicine       101
genetics_molecular      99
palliative_supportive   26
geriatric_*              0     <- no chunk carries any geriatric role
```

Yet **33 chunks mention G8, frailty, fragilité or geriatric assessment.** The content is
there in small quantity; the routing tag was dropped. That is a regression, and it is the one
finding that most directly touches how we are scored.

**Why this specific role matters more than its size suggests.** Our application already has
an oncogeriatric referral rule — `rcpia/rules/referrals.py`, raising an assessment on
G8 at or below 14 — and that rule currently carries `project_suggestion` authority rather than
`clinician_approved`, precisely because no guideline in the corpus supports it. So the machine
raises geriatric referrals it cannot cite. Meanwhile **multidisciplinarity is one of the four
dimensions the three specialists score us on**, and this is exactly where a real RCP brings in
a discipline that we cannot.

`palliative_supportive` at 26 chunks (2%) is the same problem one degree less severe.

**What we ask, in order of preference:**

1. **Restore the geriatric role tag** on the 33 chunks that carry the content, so the
   existing evidence is at least routable.
2. **Tell us whether the current sources can support these roles at all.** If EAU, AFU and
   NCCN genuinely contain no oncogeriatric or supportive-care *guidance* — as opposed to
   score definitions — say so plainly. That is a useful answer and we will act on it.
3. **If they cannot, recommend a source.** SIOG, the SoFOG oncogeriatrics recommendations,
   ESMO supportive care, or whatever you judge authoritative for French-language practice.
   Recommend; do not add it unilaterally. Adding a guideline family is a clinical scope
   decision and it goes to our clinician, not to us.

---

## Part 5 — `treatment_selection` is the new over-broad tag

v2 section 3.3 flagged `diagnostic_workup` on 48% of chunks with *"a filter that admits half
the corpus for every patient is not a filter."* You split it into `diagnosis` (16%) and
`staging` (16%), which worked.

The same shape has reappeared one field over:

```
treatment_selection   620   49%
general               250   20%
diagnosis             206   16%
staging               197   16%
follow_up             126   10%
safety                122   10%
pathology              86    7%
treatment_delivery     58    5%
genetics               57    5%
risk_stratification    45    4%
screening              36    3%
supportive_care        26    2%
```

Half the corpus is `treatment_selection`. Combined with Part 2 — where 306 chunks are
simultaneously `treatment_selection` and `not_specified` — the two most important routing
fields are both uninformative on the same large block of chunks.

We are **not** asking for a target percentage. We are asking whether `treatment_selection`
can be split the way `diagnostic_workup` was — for instance separating the *choice between
modalities* from the *indication for a specific therapy* — or whether 49% is genuinely what
these documents contain. If it is genuine, say so and we will stop asking.

---

## Part 6 — Still outstanding from v2

**Terminology mappings.** Section 7 of `RCPIA_CORPUS_ENGINEERING_METHODOLOGY.md` describes
them; no such file arrived in the slice. We now have a concrete use for it and it is more
urgent than it was.

The corpus is 81% English and every query we build will be French. We measured the
consequence directly. A French keyword query returns 56 chunks, all French, zero English; the
English equivalent returns 339, all English, zero French. Stemming does not bridge it:
`métastatique` and `metastatic` both reduce to `metastat`, but `cystectomie` gives
`cystectom` against `cystectomi`, and `vessie` against `bladder` shares nothing at all.

A French-to-English mapping of urological oncology terms — organ names, procedures, drug class
names, disease-state phrases — closes that gap. Roughly fifty terms would cover most of it.
We will hold it in version control on our side and apply it at query time, so it does not
require re-embedding. Plain two-column data is fine; it does not need to be a Postgres
artefact.

**Encoding.** v2 section 3.5 reported mojibake in `publisher`. Please confirm whether it was
fixed; those strings feed citation labels and reach a clinician's page.

---

## Part 7 — What we are doing on our side, so you can see the whole system

We are building retrieval against this corpus now: metadata filter first, then PostgreSQL
full-text search and pgvector in parallel, fused with Reciprocal Rank Fusion, reranked by a
cross-encoder, with a relevance floor below which nothing is returned.

The next step after that is **measurement** — French clinical questions with expected
passages, reporting Recall@5 and Recall@10 broken down by language and source document. That
evaluation will tell us which of the findings above actually cost us retrieval quality and
which are cosmetic.

**We are deliberately not fixing anything in Part 2 or Part 5 before that measurement runs.**
It is possible that the reranker, which reads the passage text, absorbs a lot of the
`not_specified` noise and the filter matters less than we assume. If so we will tell you, and
some of this request becomes optional. You may reasonably want to wait for those numbers
before doing the largest re-labelling work. Parts 1, 3, 4 and 6 do not depend on it.

---

## Part 8 — What to hand back

1. A decision on Part 1 — exclusion or new `cancer_type` values — and the corrected slice.
2. Re-labelled `disease_setting` for EAU and both NCCN documents, with the per-document rate
   reported the same way as the table in Part 2 so we can compare directly.
3. `section_path` corrected for EAU and NCCN, captions removed.
4. Geriatric role restored on the chunks that carry the content, plus your judgement on
   whether the current sources can support the role at all, plus a source recommendation if
   they cannot.
5. Your answer on whether `treatment_selection` can be split.
6. The terminology mappings file.
7. Confirmation on the `publisher` encoding.

As in v2: **ask before guessing on anything clinical.** And please report what you could not
fix alongside what you did — a clean pass on a corpus this size is less likely than a missed
check.

---

## Appendix — how to reproduce every number above

Against the loaded database (`rcpia`, PostgreSQL 17):

```sql
-- Part 1: out-of-scope content, and how it is tagged
SELECT array_to_string(cancer_types,',') AS tagged, count(*)
FROM evidence_chunks
WHERE keyword_text ~* 'upper tract urothelial|UTUC|renal pelvis|carcinoma of the urethra'
GROUP BY 1;

-- Part 2: not_specified rate per document
SELECT document_id,
       count(*) FILTER (WHERE disease_setting='not_specified') AS ns,
       count(*) AS total,
       round(100.0*count(*) FILTER (WHERE disease_setting='not_specified')/count(*),0) AS pct
FROM evidence_chunks GROUP BY 1 ORDER BY 4 DESC;

-- Part 2: the 306 treatment_selection chunks with no setting
SELECT document_id, array_to_string(section_path,' > '), left(text,200)
FROM evidence_chunks
WHERE disease_setting='not_specified' AND 'treatment_selection' = ANY(clinical_purpose);

-- Part 3: captions used as headings
SELECT document_id, count(*)
FROM evidence_chunks
WHERE EXISTS (SELECT 1 FROM unnest(section_path) s WHERE s ~* '^Table |^Figure ')
GROUP BY 1 ORDER BY 2 DESC;

-- Part 4: role coverage, and geriatric content that carries no geriatric role
SELECT unnest(agent_roles) AS role, count(*) FROM evidence_chunks GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) FROM evidence_chunks WHERE keyword_text ~* 'G8|frailty|fragilit|geriatri';

-- Part 5: clinical_purpose distribution
SELECT unnest(clinical_purpose) AS purpose, count(*),
       round(100.0*count(*)/(SELECT count(*) FROM evidence_chunks),0) AS pct
FROM evidence_chunks GROUP BY 1 ORDER BY 2 DESC;
```
