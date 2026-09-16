# Data Handling — MVP Decisions

Status: decided for the MVP, 2026-08-16. Revisit before any production use.

## Scope of these decisions

RCPia's MVP is a demonstrator. It runs on developer machines and cloud services, and it
is expected to be **rebuilt on an internal hospital server** before it touches routine
clinical work. The decisions below trade privacy engineering for speed on that basis;
none of them are appropriate for production as written.

## Decisions

### 1. Cloud parsing is accepted for the MVP

`PARSER_PROVIDER=llamaparse` sends each uploaded dossier to LlamaParse Cloud, outside the
hospital network and likely outside Morocco. Accepted for the MVP.

Consequence: **only de-identified or simulated dossiers may be uploaded** while this
remains true. Simulated dossiers may carry invented patient names — that is fine, since no
real person is identified. Real patient names must not be uploaded. The parser adapter
keeps `liteparse` as the local option precisely so a production deployment can parse
inside the hospital.

### 1b. Corpus conversion sends published guidelines to the same provider

`rcpia.corpus.convert` sends **source guideline PDFs** to LlamaParse Cloud to obtain the
markdown the corpus is built from. This is a different question from decision 1 and carries
a different risk.

**No patient data is involved.** These are published clinical guidelines — EAU, AUA, AFU,
SIOG, ESPEN. Nothing about a person leaves the hospital on this path, so the constraint in
decision 1 does not apply to it.

**What does apply is licence.** Several of these documents are all-rights-reserved (the AFU
chapters carry an Elsevier Masson line; AUA and NCCN publish under their own terms), and
uploading one to a third-party service is a use their licence may not cover. The corpus
already records `licence_status` and `licence_evidence` per document for exactly this kind
of question, and today nine of 112 records carry evidenced permission. **Conversion should
follow the licence check, not precede it.**

Conversion is a one-off per document and is deliberately not part of the corpus build: the
build reads markdown from disk and runs with no network and no API key. A converted document
records its provider and job id in a `.conversion.json` sidecar, so what was sent where is
answerable after the fact rather than from memory.

### 2. No de-identification pass before storage

Parsed text is stored verbatim in `document_chunks.text` and in
`extracted_facts.source_excerpt`, because provenance requires quoting the source. Whatever
identifiers a dossier contains will therefore land in PostgreSQL.

Consequence: the database inherits the sensitivity of whatever is uploaded. Simulated
names from simulated dossiers will therefore appear in stored chunks and excerpts, which is
acceptable because they identify nobody. A production build needs either a
de-identification step at parse time, or an internal database.

### 3. Patients are identified by dossier number, sex, and age

The reference dossiers and PPS examples identify a patient as `Dossier: 2` plus
`Homme, 63 ans` — no name, no hospital record number. RCPia follows that convention:

- `cases.reference` holds the dossier number used by the RCP.
- Mapping a dossier number back to a patient stays in the hospital's own records; RCPia
  never stores it.
- The generated PPS identifies the patient the same way the human PPS does.

This is why no table has a patient-identifier column, and why the schema can enforce
`reference` uniqueness per organization without holding personal data.

### 4. Retention and erasure

Not defined for the MVP. The schema supports an audited purge
(`SET LOCAL rcpia.allow_purge = 'on'`); no policy governs when it should run. A production
deployment needs a retention period, an authorization step, and blob deletion alongside
the database purge.

## What must change before production

### Tester access boundary (16 September 2026)

Uploaders can list, read, correct and analyze only cases they created. Administrators
can supervise every case within their own organization, never another organization's.
An individual account does not grant access to colleagues' cases. This rule is enforced
by the shared API case lookup for documents, ledger operations, analyses and exports,
not only by the dashboard. Responses under `/cases` are not cacheable.

The change does not alter stored case ownership or prior clinical evidence. Cases with
no recorded creator remain available only to organization administrators. New testers
see an empty workspace with no automatic example; peer sharing is not implemented.
An administrator's access means these are not confidential spaces from that administrator.

### Remaining production requirements

- Parse inside the hospital (`PARSER_PROVIDER=liteparse`) or contract cloud parsing properly.
- Host PostgreSQL and blob storage on the internal server.
- Define retention, erasure authorization, and access logging review.
- Re-examine whether verbatim excerpts are still acceptable, or need masking.
