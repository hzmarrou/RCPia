# Response to the v3 design proposal

Confirmed, with one ordering change, one correction to our own Part 4, and five details to
pin down before you start. The conservative approach is the right call and we would rather
have fewer settings assigned correctly than more assigned plausibly.

---

## The ordering change: do 3 before 2 and 4

Your item 2 assigns settings "only from reliable section ancestry". Your item 3 rebuilds the
section ancestry, because it is currently wrong. Item 2 therefore depends on item 3 having
already run.

This is not theoretical. We looked at the chunks behind our Part 4 claim, and four of the
first eight carry `CLINICAL FRAILTY SCALE` as a top-level ancestor of passages about watchful
waiting and active surveillance inclusion criteria — sections that have nothing to do with
frailty. Deriving a disease setting from that ancestry would produce confident, wrong labels.

Please run: **3 (paths) → 2 (settings) → 4 (routing)**, and regenerate `contextual_text` once
at the end rather than after each stage.

---

## A correction to our Part 4 — our number was inflated by the Part 3 bug

We reported "33 chunks mention G8, frailty, fragilité or geriatric assessment." That count is
partly an artefact of the very problem in Part 3: the corrupted `section_path` prepends
`CLINICAL FRAILTY SCALE` into `keyword_text`, so passages about active surveillance match a
search for "frailty" without containing any geriatric content.

The real number is smaller than 33, and we cannot tell you what it is until paths are fixed.
Please do not treat 33 as a target.

**What we did confirm is more useful:** genuine oncogeriatric guidance does exist in the
corpus, and it is concentrated in EAU section 6.1, *Estimating life expectancy and health
status* — including 6.1.3 health status screening, 6.1.3.a co-morbidity, 6.1.4 conclusion,
and 6.1.5 *Recommendations for evaluating health status and life expectancy*. That is real
guidance, not the score instruments v2 complained about. Route that.

The v2 concern still stands as a rule: **do not tag a score instrument as geriatric
guidance.** An agent handed the G8 questionnaire as its evidence has been given a form, not a
recommendation. If you tag instruments at all, mark them distinguishably.

---

## Item-by-item

**1. Exclude UTUC and urethral — confirmed.** Your reasoning is ours. RCPia covers prostate
and bladder; a new cancer type is a clinical scope decision that belongs to our clinician,
not to a corpus correction. Exclude them.

**2. Conservative setting repair — confirmed**, subject to the ordering above.

The "add a reason when treatment content remains unspecified" is a good idea and it is a
schema addition, so we need its shape before we can store it. Please make it a **small enum,
not free text** — for example `spans_settings`, `general_principle`, `ancestry_unreliable`,
`not_determinable`. Free text cannot be counted, filtered or regression-tested; an enum tells
us at a glance whether the remaining unspecified chunks are legitimately general or merely
undetermined. Name the field and add it to `chunk.schema.json`.

**3. Section paths — confirmed.** Reconstructing nesting from numbered headings is the right
mechanism for EAU, which numbers everything. Note that NCCN does not number consistently, so
tell us what you use there.

**4. Specialist routing — confirmed**, with the correction above.

On supportive care: same question as geriatrics. If EAU, AFU and NCCN contain no real
supportive-care *guidance*, say so plainly rather than tagging thinly. A recommendation for
an external family is welcome; adding one is not yours to do, and we have not yet put that
question to our clinician.

**5. Splitting treatment purpose — confirmed, but drop the compatibility mapping.**

The three-way split is exactly right. `modality_selection` versus `therapy_indication` is the
distinction we could not name ourselves.

But there are no existing `treatment_selection` consumers to be compatible with. We store and
index `clinical_purpose`; nothing filters on it yet. A compatibility alias would mean two
names for the same concept from the day it ships, and one of them would quietly become the
one people use. Give us the clean three values and let `treatment_selection` disappear.

If you have consumers of your own that need it, keep it on your side, not in the delivered
schema.

**6. Terminology mappings — confirmed.** Two columns, versioned, plain data. We apply it at
query time on our side, so it never touches the stored vectors.

**7. Negative tests — confirmed, with one question.** How do you test "no wrongly nested EAU
headings" mechanically? We could not devise that check, which is why Part 3 gave you two
examples and a caption count rather than a number. If your test is "every path segment's
number is a prefix of the next", say so — that is a good check and we would like to run it
too. If the check is weaker than that, tell us what it actually catches.

---

## Five things to pin down before you start

**1. Are `chunk_id` values stable across this correction?** This is the most important
question in this document.

Your ids embed the section slug —
`afu.bladder-tvim.2024-2026.autres-traitements-neoadjuvants.01.0014`. Item 3 rebuilds section
hierarchy, so unless ids are pinned, every id changes.

Consequences on our side: stored agent runs cite chunk ids, and our forthcoming retrieval
evaluation set will name expected passages. If ids churn on every corpus correction, no
citation and no evaluation survives a release.

Our current run history is five development runs and disposable, so this is not urgent
damage on that side. The larger issue is on ours, and we found it while reviewing your
design: our loader upserts but never deletes, so a release that *removes* content leaves the
removed chunks in place, and a release that changes ids inserts the new ones alongside the
old rather than replacing them. Your item 1 is the first release that removes anything, so
this would have bitten on exactly the chunks we called blocking. We are fixing it before we
ingest v3.

That fix means removals and id changes will both be handled correctly on our side. So answer
this question for the reason that remains: durable ids let a stored citation and an
evaluation set survive a release. Either:

- keep ids stable across corrections and let them drift from the new section slugs, or
- tell us plainly that ids are release-scoped and not durable identifiers.

Either answer is workable. Not knowing is not.

**2. How many chunks will re-embed?** Regenerating `contextual_text` changes
`content_sha256`, and we re-embed on hash change. If all 1,029 English chunks regenerate we
pay for all of them, which is fine but worth knowing. Please regenerate `contextual_text`
**only where the path actually changed**, not unconditionally.

**3. Report the before-and-after `disease_setting` rate per document**, in the same table
shape as Part 2 of the request. That is how we will judge whether the repair worked. A single
overall percentage will not show us whether EAU improved or only NCCN did.

**4. Does `unit_type` get the same treatment?** We did not raise it, and it has the same
problem. In the eight geriatric chunks we inspected, a passage on active surveillance
inclusion criteria is tagged `pathology_guidance`, and passages on watchful waiting and
active surveillance are tagged `staging_guidance`. If `unit_type` is derived from section
ancestry too, item 3 may fix it for free — but please check rather than assume.

**5. Confirm the publisher encoding fix.** It appears in your test list (item 7) but not in
your work items (1–6). If it is already fixed, say so and we will drop it.

---

## Our sequencing, so you can plan

We are building retrieval against the current corpus now, and we will measure it — Recall@5
and Recall@10 on French clinical questions, broken down by language and source document —
**before** your v3 lands. That gives us a baseline. When v3 arrives we re-ingest and re-run
the identical evaluation, and we can then say whether the correction helped rather than
assuming it did.

This means two things for you:

- You are not blocking us. Take the time to do items 2 and 3 properly.
- If our measurement shows that `not_specified` costs us little — because the reranker reads
  the passage text and absorbs the noise — we will tell you, and item 2 becomes optional
  rather than required. You may reasonably prefer to do items 1, 3, 4, 5 and 6 first and hold
  item 2 until those numbers exist.

Your call. Items 1 and 3 we would like regardless, and item 3 is the one that improves
retrieval quality most directly, because `contextual_text` is what we embed.

---

Confirmed. Proceed once you have answered question 1 on `chunk_id` stability — that one we
need before you build, not after.
