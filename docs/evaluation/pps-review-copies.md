# Decision-led PPS and reviewer copies

New synthesis jobs use `rcpia.pps-proposal.v4` by default. They produce an explicit
preferred strategy, ranked alternatives, sequenced actions, proposed referral/trial
answers, palliative and supportive care, social planning and follow-up. Conditions
and missing information remain visible. A required preliminary work-up is a
concrete proposal, not a fabricated treatment or clinical approval.

The synthesis supplies two distinct outputs: the detailed `pps_proposal` and the
concise `pps_fiche`. The normal fiche, Markdown and browser paths render the latter;
the companion probatory document retains the full proposal, attribution and
missing-information detail. Rendering does not invoke another model or truncate
text. Historical v1 proposals without a concise fiche retain their existing view.

Native fiche, probatory and neutral reviewer Word exports share the same document
settings repair: an existing `w:zoom` element without its required `w:percent`
receives `100`. Existing percentages are preserved and an absent optional zoom
element is not added. This changes document settings only, never clinical content.
Existing saved proposals can be exported again without rerunning any clinical agent.

The institutional fiche contract (`rcpia.pps-fiche.v2`) requires narrative
diagnosis/comorbidity text and short phase titles with explicit bullet items.
Conditional alternatives continue the numbered plan; their source ranks and
conditions are preserved. Social entries describe the planned organization.
The destination checkbox identifies a proposed discharge location, not an
already completed discharge.

Checkbox explanations remain limited to 35 words, precisions to 600 characters/
85 words, and the final review trigger to 80 characters/18 words. The authored
clinical text has a 1100-word ceiling; a verbatim operator question is not edited
to satisfy the prose-style rules. These are rejection limits, never truncation.
The earlier `rcpia.pps-fiche.v1` shape remains readable using its recorded schema.

The National reference marker is only available when the proposal actually cites
an AFU source. International complements remain explicit where used; no source
category is invented to match another plan. An operator-supplied question remains
a question, not a confirmed diagnosis, stage or resectability finding.

These structural checks do not prove semantic equivalence; clinical review must
check that the summary preserves the conditions that change the treatment choice.

## Output reliability and audit

The v4 execution contract keeps the detailed proposal and fiche schemas unchanged.
After validating the original structured citations, the service completes a missing
outer `evidence_references` entry only from an explicit `pps_proposal.evidence_ids`
use (including nested actions/referrals). It copies that use's existing rationale
or action verbatim into `supports`; it never invents an ID, selects new evidence,
changes a clinical statement, or removes an existing reference. Existing grounding,
allowlist, attribution and complete-register checks still apply. An entirely
ungrounded outer recommendation is not repaired from incidental support citations.

The accepted database output includes those mirrored entries. Its
`agent.run_completed` audit event records `reference_register_completion` with the
exact citation and source-field paths. The archived Pi transcript retains the raw
model response, including the original register omission. A mirrored entry records
the model's existing support claim; it does not prove clinical entailment.

Schema/JSON failures still reject the attempt. Within the existing queue attempt
budget, a failed validation stores bounded technical feedback in the job payload.
The next bundle carries it in `constraints`, never as patient data or a specialist
opinion. It includes field locations and length bounds, not the failed clinical
draft. Feedback is bound to the exact patient/evidence/opinion inputs and output
schema; a mismatch explicitly stops that retry. Diagnostics are included in the
same 223,232-byte limit and each attempted bundle remains archived.

No text is truncated and no length bound is increased. In particular, diagnosis
and comorbidities remain limited to 450 characters each; an overlong summary must
be reformulated by the model while preserving decision-relevant conditions.

An explicit interrupted provider response (`error`, `aborted`, `length`, or an
error message) is a runtime failure even when Pi exits with code zero or leaves
parseable partial text. Its transcript is retained. A later completed response
can recover only with its own nonempty answer; an empty, thinking-only or tool-only
retry cannot reuse an earlier failed answer. An earlier answer cannot mask a final
provider error.

The six reference sections share their content between Word and Markdown; the
web representation uses the same typed concise fiche. Actual
validation, communication to the GP, calculator eligibility and approval of
clinical thresholds remain separate from proposed choices. Existing outputs
without `pps_proposal` retain the legacy presentation.

For generated proposals, the displayed operator question comes from the lead
run's stored bundle. When that bundle has no explicit question, the saved fiche
wording remains. A later edit to the live case cannot replace the question
the recorded proposal answered.

Labelled originals retain the existing provisional disclaimer and this exact title:

`FICHE RCP — CREATED BY AN AI AGENT — PROPOSITION DE PRISE EN CHARGE (PPS)`

The companion probatory export retains detailed specialist attribution. Selecting
a preferred strategy does not change the recorded disagreements or invent consensus.

## Deployment compatibility

New standalone and automatic synthesis jobs carry `pps_proposal_contract`.
The synthesis deduplication fingerprint includes that contract. Workers explicitly
refuse unversioned/obsolete synthesis jobs (including v3) rather than silently running them under
a changed instruction set. Drain old jobs before rollout, or request a new
synthesis after an obsolete job settles. Historical successful runs remain readable;
their stored schema distinguishes them from required current proposals.

The lossless compact-JSON prerequisite is included. The byte ceiling remains
223,232; no clinical strings or evidence units are trimmed to accommodate the new
schema. An oversized bundle still fails explicitly.

## Offline reviewer package

Keep the originals and a manifest in an access-restricted directory outside Git.
Each case requires one Word document from each origin, identified by its SHA-256.
Use neutral case aliases, not patient identifiers:

```json
{
  "cases": [{
    "alias": "D01",
    "plans": [
      {"origin": "real", "path": "C:\\private\\real.docx", "sha256": "<64 hex characters>"},
      {"origin": "old_rcpia", "path": "C:\\private\\old.docx", "sha256": "<64 hex characters>"},
      {"origin": "new_rcpia", "path": "C:\\private\\new.docx", "sha256": "<64 hex characters>"}
    ]
  }]
}
```

```powershell
python -m rcpia.evaluation.pps_review --root C:\private --manifest C:\private\manifest.json --output C:\private\review-001
```

The command creates a new generation and never overwrites previous copies or keys.
It randomizes A/B/C separately within each case. Distribute **only `reviewer`**,
and only after `COMPLETE.json` exists. Keep `coordinator`, its identity key, source
paths, hashes and masking record out of reviewers' hands until scoring is complete.
The coordinator record contains clinical material and needs the same access control
as the originals.

An optional case-level `comparison_question` supplies the same exact question
to all three reviewer copies. Each replacement is recorded in the coordinator
receipt and does not change diagnostic fields or underlying medical proposals.
Use only an explicitly supplied comparison question; do not silently treat
another plan's assumptions as patient facts.

When revising the presentation of an already assigned case, each input may specify
its existing `letter` (`A`, `B` or `C`). Supply all three letters exactly once;
partial or duplicate assignments are refused. This preserves the coordinator's
existing mapping without reshuffling documents between iterations.

All three origins receive the same compact A4 template: Arial, identical margins,
six headings, fixed label/value tables and one common evaluation-only notice.
Both Unicode and bracket checkbox notation become ASCII `[X]`/`[ ]` without
changing the choice. Explicit origin banners and administrative approval, delivery,
attendance and header metadata are removed consistently, not filled with simulated
answers. Treatment status, clinical trial choices and qualifications remain.

Template `rcpia.pps-review-template.v2` groups action conditions and timing with
their action, gathers social fields into the same four rows, and moves trailing
requirements into treatment precisions. Exactly duplicated trailing requirements
are recorded as duplicates rather than printed twice. Every copy closes with its
own next-review content; no meeting date or schedule is invented. Paragraph
wrapping and table labels are standardized without paraphrasing medical text.
Different clinical detail can still produce different document lengths.
Plan sub-items are rendered with the same Word bullet style for every source,
including older documents whose bullets were only implicit in paragraph layout.

Unsupported structures, tracked changes, comments, unclassified preambles or
remaining origin markers in clinical content stop packaging for manual review.
Nested tables and mixed administrative/clinical multi-label rows are refused
before masking. Paragraph extraction is checked against the source XML text,
so an unsupported inline container cannot silently hide a condition.
The tool must not erase a clinical caveat simply because it gives an origin clue.
It does not rewrite historical decisions to match the new system, fabricate
grades, validate the medicine or guarantee complete blinding. Differences in
clinical content and writing may still reveal the source.

This produces presentation-normalized Word copies, not a new expert-score workbook.
Previously frozen evaluation packets, keys and ratings must remain unchanged.
