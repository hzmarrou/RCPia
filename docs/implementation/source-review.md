# Clinician source inspection and review annotations

This API separates source inspection from extraction, clinical correction and analysis.
It makes no model/provider calls and does not change clinical facts, ledger publication,
jobs, staging, completeness or `can_run_analysis`. No migration is required: review
annotations use the existing append-only `AuditEvent` table.

## Contract

### `GET /cases/{case_id}/sources`

Returns:

```typescript
{
  case_id: string;
  ledger_version: number | null;
  source_fingerprint: string; // lowercase SHA-256, 64 hexadecimal characters
  scope: "pinned_snapshot" | "current_parsed_unpinned" | "unavailable";
  notice: string | null;
  documents: {
    id: string;
    filename: string;
    parse_run: number;
    uploaded_at: string;
    pages: number[];
    chunks: number;
    coverage: "complete_parsed_source" | "unavailable";
  }[];
  fields: {
    key: string;
    state: "documented" | "absent" | "in_report" | "interpretation" | "unverified";
    label: string;
    explanation: string;
    links: { document_id: string; chunk_ids: string[]; reason: string }[];
    can_attest_absence: boolean;
    reviewed: boolean;
    review_note: string | null;
  }[];
}
```

`filename` is the uploaded basename, not a storage key. `uploaded_at` is the upload
timestamp, **not** an examination date. Examination dates remain in the literal source
text; the API does not infer relationships between dates and reports.

`complete_parsed_source` means every stored block of that parse generation is available.
It does not guarantee that the original file was completely or accurately parsed.
Documents with no ledger version cannot be read through these endpoints.

The latest ledger's `patient_source_manifest`, when present, determines the documents
and parse generations. Ownership, file checksum, every chunk's identity, order, text
digest, page, section and block type are verified with the existing source helpers.
Corrupt or mismatched pinned evidence produces `409`; there is no live fallback.
The model transport's source-unit size limit does not truncate the inspection endpoint:
larger documents remain fully accessible, with context limited to ledger-only context.

Legacy ledgers without a manifest may expose currently parsed documents under
`current_parsed_unpinned`, with the explicit notice:

> Ces sources actuelles ne prouvent pas ce que les anciens avis ont reçu.

This is not historical provenance for earlier opinions.

### `GET /cases/{case_id}/sources/{document_id}`

Both `ledger_version=N` and `source_fingerprint=HASH` query parameters are required.

```typescript
{
  case_id: string;
  ledger_version: number;
  source_fingerprint: string;
  document: {
    id: string;
    filename: string;
    parse_run: number;
    uploaded_at: string;
  };
  blocks: {
    id: string;
    ordinal: number;
    text: string;
    page: number | null;
    section: string | null;
    block_type: string;
  }[];
}
```

Every block is returned in its immutable ordinal order: no shortening, whole-line
filtering, inferred report boundaries or HTML rendering. Clients must render text as
text, not executable markup. Source links identify actual blocks for scrolling and
highlighting, not clinically inferred full-report boundaries. The full document is
available even when the field only cites its first paragraph. A neutral field with no
verified association should offer the global document list instead of a fabricated link.

### `POST /cases/{case_id}/sources/review`

```typescript
{
  expected_ledger_version: number;
  source_fingerprint: string;
  field_key: string;
  status: "absent" | "unverified";
  note: string; // 3–2000 characters after surrounding whitespace is stripped
}
```

Returns the same index shape (`200`). This is an explicit actor attestation, not a
clinical value. The UI should require an affirmative absence-attestation checkbox and
encourage reading the sources first; the server cannot prove that a person read them.

Absence requires available parsed text and an empty/unusable current field. It is refused
when a known value, a verified named source value, or directly related interpretation
would contradict it. Missing fields alone never create absence attestations.
`can_attest_absence` exposes this same eligibility predicate for the current source
binding; POST rechecks it under the case lock. Clients must use this flag rather than
guessing eligibility from raw ledger values or retained readings: a preserved
`non renseigné` reading is provenance, not a usable clinical answer. The flag does not
replace the explicit attestation, source-reading UI safeguards or required note.
`unverified` withdraws the previous annotation while preserving audit history; the
underlying deterministic state still applies (for example a newly documented value
stays documented). `reviewed: true` means a current, valid absence attestation, not clinical
approval. A withdrawal returns `reviewed: false`, with its note.

Each successful action appends `case.source_review_recorded` with case, organization,
actor, field, status, note, ledger version, source fingerprint and field fingerprint.
Nothing edits or deletes earlier events. Only the newest event for the field is
considered; older attestations cannot reappear after withdrawal.

## Field states

| State | Exact label | Display intention / evidence |
|---|---|---|
| `absent` | Information absente du dossier | Red; explicit current clinician attestation only |
| `in_report` | Information présente dans un compte rendu, à préciser | Blue; literal named-field content, or a known incomplete/truncated field with its full parsed source |
| `interpretation` | Interprétation à confirmer | Amber; uncertain/conflicting/inferred value, meaningful field-specific context reasons or directly sourced partial staging |
| `documented` | Information renseignée | Neutral/subdued; usable recorded value, not automatic clinical approval |
| `unverified` | Présence dans les sources à vérifier | Neutral; empty/unusable field without demonstrated presence or attested absence |

Label matching and the existing deterministic extraction reader find candidate source
anchors without persisting candidate facts. Generic mentions of MRI do not supply cTNM.
Placeholders, parser no-content sentinels, separators and page footers do not demonstrate
presence. An explicit `RAS` is usable only for fields whose existing schema accepts it.
Unknown source date/episode/specimen metadata alone does not make every field amber.
Meaningful existing field-review reasons are explained in French rather than exposed as
untranslated internal codes.

## Freshness, races and privacy

The fingerprint binds actual full parsed content, chunk metadata, parse generation,
ledger version, live field state and current intake generation. New data, corrections,
uploads and reparses invalidate old bindings and annotations, even if the pinned document
text remains the older immutable generation.

Source inspection and annotation serialize with ledger publication using the case row
lock. POST revalidates version, source fingerprint and field eligibility under that lock
and checks the binding again before commit. A competing publication makes an old review
request fail rather than attaching it to new data. Audit timestamps are recorded after
lock acquisition, not at the start of a waiting transaction.

Routes call the shared `cases.service.get_case` policy first: uploaders see only their
own cases; organization admins retain normal organization-wide access. Same-organization
peers and foreign administrators receive the normal indistinguishable case `404`.
Viewers receive `403`. A document of another case is indistinguishable from an unknown
document (`404`). Source queries are scoped to both case and organization. No source
content appears in integrity errors.

The existing middleware supplies `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`, including error responses. A fingerprint is a concurrency
token, not authorization or a secret.

Errors: `409` for stale/corrupt context; `422` for invalid payload or ineligible absence;
`404` for inaccessible case/document; `403` for a disallowed role. Reload the source
index after `409`.

## Validation

Provider-free synthetic regressions in `apps/api/tests/test_source_review.py` cover full
text and end qualifiers, upload versus examination dates, literal HTML, oversized
sources, placeholders, context-specific classifications, immutable pinned generations,
legacy notices, stale fingerprints, corruption, cross-case document isolation,
attestation/retraction, unchanged clinical data and eligibility, and concurrent ledger
publication. `test_case_access.py` includes all three routes with valid payload/query
shapes so validation errors cannot mask authorization failures.
