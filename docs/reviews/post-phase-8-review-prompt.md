# Review request — three changes after Phase 8

You reviewed Phase 8 (the provisional PPS) and Phase 7 (the agent runtime) previously. This
covers what changed since, plus one infrastructure move.

**What RCPia is.** A web application preparing *provisional* RCP/MDT documents for
urological oncology at CHU Mohammed VI Marrakech. Every output is reviewed by clinicians
before use. Clinical text is French; code is English.

**Range:** `git log e2f2b4f..HEAD`. Only four commits touch code or infrastructure:

| Commit | What |
|---|---|
| `e5ed051` | Field catalogue corrected against 15 real dossiers |
| `b345084` | Multi-paragraph reports no longer truncated at the first blank line |
| `9c3c61c` | **Section 2 now answers, as a project choice rather than a sign-off** |
| `ba2effb` | PostgreSQL 16 → 17 |

The rest are documentation. Two are worth reading as context rather than reviewing:
`docs/reference/blinded-evaluation-results.md` (three specialists scored an earlier RCPia
build against real RCP documents — the machine was **worse in 40% of ratings**) and
`docs/reviews/corpus-v1-review.md`.

Run the suite from `apps/api` with
`TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test uv run --frozen pytest`.
442 tests, ruff and mypy clean at `ba2effb`.

---

## 1. `9c3c61c` — section 2 answers now. This is the one that matters.

**Please spend most of your effort here.** It changes what RCPia asserts to a clinician.

### What changed

Previously all three referral questions in section 2 — *avis oncogériatrique*, *avis
oncogénétique*, *préservation fertilité* — answered `à déterminer`, because no clinical
threshold had been approved. Correct, and also useless: a proof of concept that answers
nothing gives a reviewer nothing to correct.

`rcpia/rules/approval.py` now has a third state between approved and unapproved:

- **approved** — a clinician signed the threshold off; the answer stands alone
- **provisional** — a project choice; the answer is shown *with* a caveat saying it is not
  clinically validated
- **neither** — the rule may not answer; `à déterminer`

`Referral` carries `approved` and `provisional` separately. `PpsDocument.assert_provisional`
refuses a document where an unapproved rule answered, **and** where a provisional answer
arrives without its caveat.

`approved_by` deliberately does not name a person. It reads *"Équipe RCPia — décision
projet, en attente de validation clinicienne"*. Inventing a clinician's name would produce
a record indistinguishable from a real sign-off.

### The question I most want answered

**Does the provisional state make this safer, or does it launder an unapproved threshold
into an answer?**

The argument for: clinicians cannot correct what they cannot see, and a form of
`à déterminer` × 3 tells them nothing about RCPia's reasoning.

The argument against: a reader skimming a fiche sees a ticked box. The caveat sits below it
in italics. I rejected exactly this reasoning in the previous round — you wrote that
*"merely adding 'seuil non validé' does not neutralize a checked box"*, and I agreed. This
change does something close to what you rejected, with the difference being that the state
is now typed and enforced rather than a caption. **Tell me if that difference is real or if
I have talked myself into it.**

### Specific things to attack

**The caveat check is a substring test.**
`if referral.provisional and "non validé" not in referral.justification`. In the last round
you found that `"[X] Non" in framework.validated` passed for a value reading
`[X] Non … [X] Oui`, and I replaced it with typed state. I have now written a substring
check again, three files away. Is it exploitable? What should it be instead?

**The genetics rule matches substrings without word boundaries.**
`rules/referrals.py` has `CANCER_TERMS` including `"colon"`, `"sein"`, `"rein"`, and
`mentions_cancer` does `any(term in lowered for term in CANCER_TERMS)`. A family history of
`colonne vertébrale` contains `colon`. `dessein` contains `sein`. I think this is a real
defect; confirm and say how far it reaches on realistic French clinical text.

**Fertility never answers *Non*.** By design: the rule answers *Oui* below the age
reference and `à déterminer` above it, never *Non*. Wrongly declining to raise fertility
preservation is permanent; wrongly raising it costs a conversation. Two questions — is the
asymmetry right, and does an 85-year-old woman receiving `à déterminer` read to a clinician
as a considered position or as a broken rule?

**The thresholds themselves.** `GERIATRIC_AGE = 75`, `G8_THRESHOLD = 14`,
`FERTILITY_AGE_MALE = 60`, `FERTILITY_AGE_FEMALE = 45`. G8 ≤ 14 is published. The rest are
convention or, for the fertility pair, invented by me. Say if any is wrong enough to matter
even provisionally.

---

## 2. `b345084` — multi-paragraph reports

### What changed

`_read_continuation` in `rcpia/cases/extraction.py` stopped at the first blank line. Since
radiology and pathology reports are written in paragraphs and the first is usually
boilerplate, it kept the acquisition technique and discarded the findings.

On the reference bladder dossier it kept *"Acquisition spiralée en coupes millimétriques"*
and threw away the tumour — grown from 33×21mm to 43×41mm — the ureteral meatus invasion,
and the pleural effusion. The field still looked populated, so nothing downstream could
tell. The agent said the dossier gave *"la description technique de la TDM TAP, sans ses
résultats"*, which was true of what we handed it and false of the dossier — and that
statement went into the PPS as a gap in the patient's record.

A report now runs to the next label the catalogue knows, or to the end of the chunk, capped
at `MAX_VALUE_LENGTH`.

### What to attack

**Over-capture.** The fix trades under-capture for over-capture. An unrecognised
sub-heading — `Technique :`, `Résultat :` — is now absorbed into the report, which I argue
is correct since those belong to it. But what else gets absorbed? Fifteen real dossiers are
listed in `docs/reference/dossier-survey-2026-08-17.md`; the sources are at
`C:\Users\hzmarrou\OneDrive\python\learning\pi\rcpia\docs\dossier*.md`. Find a case where a
report now swallows something that belongs to a different field.

**Provenance.** `source_excerpt` may now be an entire multi-paragraph report rather than one
line. The design's claim is that every fact quotes the exact text it came from. Does a
1,500-character excerpt still satisfy that, and does it degrade the PPS evidence register?

---

## 3. `e5ed051` — field catalogue

### What changed

The catalogue was derived from one bladder dossier. Run against 8 prostate and 7 bladder
dossiers it produced three silent false gaps — a field reported missing while its value sits
in the text:

- prostate grades with **Gleason and ISUP**, not `Grade` — 8/8 prostate dossiers reported
  their grade missing
- **no prostate dossier records a TNM at all** — `ptnm` is now required for bladder only
- **`sex` is absent from 12 of 15 dossiers** and is no longer required

Added the prostate staging inputs that were being read and discarded: biopsy core counts and
sites, and the PNI/EPC/VI/PIN invasion flags.

`gleason` joins `BLOCKING_FIELDS` for prostate — without it a prostate case cannot be
risk-stratified, so no safe provisional proposal exists.

### What to attack

**Short acronym labels.** `vi` (2 characters), `pin`, `epc`, `pni` are matched as whole
normalised labels. How likely is a false positive on real dossier text?

**Is `gleason` right as a blocking field?** Blocking means the analysis refuses to run. The
list is deliberately short — bladder blocks on histology and muscle invasion. Does prostate
genuinely not admit a provisional answer without a Gleason?

**Not inferring sex.** A prostate diagnosis does not make a patient male, so `sex` stays
absent rather than derived — encoding that assumption would misgender trans and non-binary
patients. Confirm nothing downstream now silently assumes a sex it no longer receives; the
fertility rule is the one that reads it.

---

## 4. `ba2effb` — PostgreSQL 16 → 17

Moved before Phase 6, because after a guideline corpus and its embeddings land, a major
version bump means re-embedding rather than dump-and-restore. The brief specified
`pgvector/pgvector:pg16`, but its stated reason is only that the plain image lacks
`pgvector`; nothing depended on 16. Recorded in `docs/decisions.md`.

The dev database was dumped from 16 and restored into 17 rather than rebuilt. Verify the
restore is faithful: 5 cases, 6 documents, 371 facts, 5 agent runs, 3 users, Alembic head
`57e2834f499a`, and all four append-only triggers. **The triggers are the ones I would
check first** — a restore that silently dropped `extracted_facts_append_only` would leave
clinical records mutable with every test still green.

CI (`.github/workflows/ci.yml`) and `README.md` were updated;
`RCPia-v1-implementation-plan.md` and the agent prompt deliberately were not, since they are
the original brief.

---

## Ground rules

- **Verify before reporting.** Write the failing test or run the probe. Earlier rounds
  produced two findings that were not true — one about placeholder directories git simply
  does not track, one right about a lockfile's consequences and wrong about its cause.
- **Rank by clinical consequence.** A path that makes RCPia assert something nobody decided
  outranks anything structural. Say which findings you would block on.
- **Say when a claim holds.** As useful as a finding, and it stops me re-litigating it.
- **Read the rendered PPS, not only the code.** If section 2 now reads as more settled than
  it is, that is a finding with no bug behind it.
- **Out of scope:** the guideline corpus (reviewed separately in
  `docs/reviews/corpus-v1-review.md`, fixes requested in `corpus-v2-change-request.md`),
  Phase 6 retrieval (not built), Phase 9 panel, PDF export. MVP security is deliberately
  light — the production system will be rebuilt on an internal server — but do report
  anything letting one organisation read another's data.
