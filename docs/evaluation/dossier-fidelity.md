# Patient-information fidelity assessment

`rcpia.evaluation.fidelity` provides offline text-preservation helpers. It does not
call models, retrieve guidelines, change clinical records or grade medical outputs.

The assessment boundary is:

**Patient source -> actual uploaded document -> parsed source -> extracted facts ->
stored ledger -> actual specialist patient inputs -> recorded retrieval question.**

Keep guideline evidence and generated opinions out of patient-content searches.
Otherwise, a drug mentioned in a guideline can falsely appear to be a preserved
patient treatment. `patient_bundle_units` deliberately examines only ledger values,
correction notes and dossier snippets.

## Method

1. Freeze source hashes, input-document hashes, parse generation, case/ledger identity,
   actual run identities and source release. Do not combine different releases under
   a single release label.
2. Select exact source spans independently of generated recommendations. Bind each
   quotation to its inclusive line range with `verify_source_span`. Such a checklist
   is a preservation aid, not a clinician-approved relevance gold set.
3. Check each span against separately attributable units at each stage.
   Ordered text from one source document may form one unit; unrelated facts or
   documents must not be concatenated to manufacture a match.
4. Verify saved field values against the corresponding fields in each actual
   specialist bundle. Compare captured attachment bytes/inputs where available;
   otherwise state that only the stored bundle was available.
5. Read actual saved retrieval provenance. A current-code query replay must be
   explicitly labelled as a replay, not a fresh historical retrieval result.
6. Inspect candidate omissions and successful controls before assigning findings.

## Result semantics

| Result | Meaning |
| --- | --- |
| `whole_normalized_span` | The span appears with case/whitespace normalization, with token boundaries also respected |
| `whole_lexical_span_review` | Words and numeric/operator tokens appear in order after presentation/accent normalization |
| `fragment_only_review` | Only a contiguous fragment was located; inspect the source and representation |
| `not_found_lexically_review` | No matching tokens were found in the supplied units |

Numbers, decimal punctuation, comparison signs and negation words are not rounded,
converted or replaced by synonyms. No semantic matching or clinical interpretation
is performed.

Whole-span presence does not prove correct organ, episode, date or specimen attribution.
Conversely, a multi-field source block may legitimately be represented as several
separate structured fields. Its failure to occur as one whole span is not proof of loss.
The optional `scoped_negative_preserved` result remains unset: lexical matching cannot
attest that clinical property.

Do not publish a "clinical fidelity percentage" from these match counts. Investigate
the quoted content, distinguish source ambiguity from software loss, and preserve
both confirmed findings and unresolved candidates.

## Storage and validation

All patient quotations, archives, manifests and reports belong in an access-restricted
artifact directory outside Git. The helpers accept in-memory evidence; they do not
configure filesystem permissions or certify anonymization.

Run the provider-free synthetic regressions from `apps\api`:

```powershell
python -m pytest -q tests\test_fidelity.py
python -m ruff check rcpia\evaluation\fidelity.py tests\test_fidelity.py
python -m mypy rcpia\evaluation\fidelity.py
```

These checks protect numeric boundaries, qualifiers, source-line binding and the
separation of patient evidence from guidelines and answers. They do not establish
clinical completeness or authorize a production extraction/retrieval change.
