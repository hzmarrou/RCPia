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

Consequence: **only de-identified or synthetic dossiers may be uploaded** while this
remains true. The parser adapter keeps `liteparse` as the local option precisely so a
production deployment can parse inside the hospital.

### 2. No de-identification pass before storage

Parsed text is stored verbatim in `document_chunks.text` and in
`extracted_facts.source_excerpt`, because provenance requires quoting the source. Whatever
identifiers a dossier contains will therefore land in PostgreSQL.

Consequence: the database inherits the sensitivity of whatever is uploaded. A production
build needs either a de-identification step at parse time, or an internal database.

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

- Parse inside the hospital (`PARSER_PROVIDER=liteparse`) or contract cloud parsing properly.
- Host PostgreSQL and blob storage on the internal server.
- Define retention, erasure authorization, and access logging review.
- Re-examine whether verbatim excerpts are still acceptable, or need masking.
