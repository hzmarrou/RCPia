# RCPia API

### Windows runtime files

Pi task/session files and the local blob store use explicit Windows extended paths.
This keeps long session filenames readable by both Node and Python without changing
machine-wide path settings or shortening case/run identifiers. A runtime response
must still be read from its actual journal and pass normal validation; the presence
of a file alone never promotes a failed attempt to a successful opinion.

Synthesis with no usable specialist opinion stops before a provider call, with an
explicit terminal error. Correct the failed specialist execution and request a new
analysis; historical jobs and their attempts are not reset.

FastAPI backend for RCPia v1. Python dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # create .venv and install deps
uv run pytest                            # tests
uv run ruff check .                      # lint
uv run mypy rcpia                        # typecheck
uv run uvicorn rcpia.main:app --reload   # dev server on :8000
```

Health check: `GET /health` -> `{"status":"ok"}`.

## Clinician source inspection

`GET /cases/{case_id}/sources` lists the complete parsed documents available to the
latest ledger and conservative field-review states. `GET
/cases/{case_id}/sources/{document_id}?ledger_version=N&source_fingerprint=HASH`
returns every parsed block in order, including its literal text, page and section.
Pinned manifests are verified; legacy current-source fallback is explicitly labelled.
Upload time is not an examination date, and complete parsed text does not guarantee
complete or accurate extraction of the original file.

An empty field is **unverified**, not automatically absent. A named source value needing
consolidation is `in_report`; uncertain or partially attributed values are
`interpretation`. Only `POST /cases/{case_id}/sources/review`, with an explicit clinician
absence attestation and note, can mark an eligible empty field `absent`.
Each field's `can_attest_absence` flag exposes the exact eligibility predicate rechecked
by POST; clients must not infer eligibility from preserved unknown-value readings.
These append-only, source/version-bound audit annotations do not change clinical facts,
ledger versions, jobs, model calls or analysis eligibility. Retraction uses the same
endpoint with `status: "unverified"`. Changed inputs invalidate the annotation.

All three routes use normal case authorization and private-response headers. See the
[contract, safety limits and regression tests](../../docs/implementation/source-review.md).

## Runtime evidence baseline

`python -m rcpia.evaluation.runtime` evaluates the production evidence-selection function
against explicit frozen ledgers and optional, hash-bound patient context
(`rcpia.runtime-input.v3`), with opt-in traces and versioned provenance. It does not
execute generation or evaluate the full bundle size gate. See
[input format, restricted artifacts, CLI and score boundaries](../../docs/evaluation/runtime-evidence-baseline.md).

## Database

PostgreSQL is the clinical source of truth. Schema changes go through Alembic:

```bash
uv run alembic upgrade head                          # apply migrations
uv run alembic revision --autogenerate -m "message"  # after editing rcpia/core/models.py
uv run alembic check                                 # fail if models and migrations diverge
```

`DATABASE_URL` drives both the application engine and Alembic.

Database tests run against a real PostgreSQL server — the schema relies on enum types,
JSONB, and check constraints. The test database is created and migrated automatically;
point the suite at a server with `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test uv run pytest
```

Use the port the Compose stack publishes (`POSTGRES_PORT`, default 5432). Each run creates
its own ephemeral database derived from that name (`rcpia_test_gw0_4f21ac93`) and drops it
afterwards, so `pytest -n auto` is safe and no existing database is ever touched.

## Append-only clinical records

`extracted_facts`, `case_ledger_versions`, and `audit_events` reject `UPDATE` and `DELETE`
through the `rcpia_deny_mutation()` trigger. Corrections are new rows superseding old ones.

Lawful erasure is still possible, but only as a deliberate, audited purge:

```sql
BEGIN;
SET LOCAL rcpia.allow_purge = 'on';   -- permits DELETE, never UPDATE
DELETE FROM extracted_facts WHERE case_id = '...';
COMMIT;
```

Any such purge must also remove the corresponding blobs and be recorded outside the
purged tables.

## Authentication

RCPia is invite-only: there is no public sign-up path. Accounts exist only where an
administrator issued an invitation, with one exception — the first administrator, who is
created by a command:

```bash
uv run python -m rcpia.auth.bootstrap "CHU Mohammed VI" admin@example.org
# password read from RCPIA_BOOTSTRAP_PASSWORD, or prompted
```

Endpoints: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`,
`GET /auth/invitations` (admin, own organization's pending and unexpired invitations),
`POST /auth/invitations` (admin), `POST /auth/invitations/lookup`,
`DELETE /auth/invitations/{id}` (admin), `POST /auth/activate`.

Admins can manage invitations at `/admin/invitations` in the web app, linked from the
dashboard. The list never returns tokens or their hashes. Creation returns the token
once; the browser builds an `/activate#token=…` link on its own origin. Copy and share
that link with its recipient: no email is sent automatically, and a lost link must be
revoked and reissued.

Sessions are server-side rows in `auth_sessions`, referenced by an httpOnly cookie.
Revocation is therefore immediate: signing out or deactivating a user invalidates existing
sessions on the next request. Only SHA-256 digests of session and invitation tokens are
stored; passwords use Argon2id with a 12-character minimum.

Roles are `admin`, `uploader`, `viewer`. Clinical routers are built with
`protected_router(...)` so every route inherits the role check, and
`tests/test_route_protection.py` fails the build if any route is neither protected nor on
an explicit public allowlist.

Invitation links put the token in the URL **fragment**, which browsers never send to a
server:

```text
https://<web-host>/activate#token=<token>
```

Login failures are throttled on two independent budgets — per account and per client
address — counting failures only, so successful logins never consume quota.

## Blob storage

Dossiers, PPS packages, and Pi session logs live in blob storage; PostgreSQL holds their
keys and checksums. `BLOB_PROVIDER` selects the backend — `local` (a directory under
`LOCAL_BLOB_ROOT`) or `azure` (Azure Blob Storage, which needs the `azure` extra:
`uv sync --extra azure`).

Both backends return the same `BlobInfo` (key, size, content type, SHA-256) and raise the
same `BlobNotFound`, so nothing above the adapter knows which one is configured.

Two rules the adapter enforces:

- **Blobs are immutable.** Azure uploads use `overwrite=False`, matching the append-only
  rule in the clinical tables: a correction writes a new key.
- **Keys never come from filenames.** `rcpia.storage.keys` builds them from case and
  document identifiers; the uploaded filename is kept in the database as data, not used
  as a path.

## Parsing

`PARSER_PROVIDER` selects the parser. `llamaparse` (default) sends the dossier to
LlamaParse Cloud and needs `LLAMAPARSE_API_KEY`; `liteparse` is reserved for local
parsing and currently fails with a clear message rather than silently falling back to the
cloud — a deployment that chose local parsing for confidentiality must never parse
elsewhere by accident.

Parsing runs in the worker, since it takes minutes:

```bash
uv run python -m rcpia.worker
```

The handler stores one `document_chunks` row per parsed block, keeping page, section, and
bounding box so extracted facts can cite them. A re-parse appends a new generation;
older chunks remain available to historical citations. Thin or empty parses produce
French warnings shown next to the document instead of failing silently.

## Complete patient-source quotations

New ledger snapshots pin `ledger.patient_source_manifest`: case/organization, document
IDs and upload checksums, parse generations, ordered chunk IDs, text checksums, pages,
sections and offsets. The `patient_source_units` input contains complete parsed documents,
including unmapped history, inline preambles, internal headings, tables and report
conclusions across parser chunks. This channel bypasses field-extraction boundaries;
it does not change field values, classify clinical findings, or infer missing information.
An examination indicator such as `Oui` remains an indicator, with its full source body
available separately. Current ledger fields, particularly human corrections, remain
authoritative over superseded mentions in the quotations.

Each document is joined with `\n\n` between its original chunks, without stripping or
clipping their text. Exact duplicate document text is sent once, retaining every source
location. New bundles link field `source_ids` to these `patient-<sha256>` quotations
instead of duplicating field excerpts in `source_snippets`. The unchanged snippet/output
limit is 4,000 characters. The dedicated document-unit limit is 111,616 characters
(half the unchanged 223,232-byte bundle ceiling); the final UTF-8 bundle gate includes
all patient units, metadata, ledger, guidance and output schema. An oversized complete
document or bundle is explicitly refused, not shortened.

`record_version(..., user=reviewer)` and `record_version(..., inherit_sources=True)`
retain the preceding snapshot's source manifest for corrections, even after re-parsing.
A snapshot with no appended machine facts also retains that manifest; a new
extraction generation refreshes it even when human corrections hide every machine value.
A source mismatch fails before model invocation, never falling back to current chunks.
Versions without a manifest keep historical snippet behavior and are labelled
`patient_source_coverage.status = "legacy_missing_manifest"`; historical versions,
stored runs and PPS are not backfilled. `"complete_parsed_sources"` attests only that
the pinned parsed text was delivered, **not** that parsing captured every original byte
or that the content is clinically validated.

Provider-free replay entry points in `rcpia.cases.patient_sources`:

```python
documents = [ParsedSourceDocument.model_validate(record) for record in frozen_records]
manifest = build_source_manifest(documents, case_id=case_id, organization_id=organization_id)
units = build_patient_source_units(manifest, documents, ledger_version=1)
```

Each record has `id`, `case_id`, `organization_id`, `parse_run`, `checksum_sha256`,
and `chunks` (`id`, `ordinal`, `text`, optional `page`, `section`, `block_type`).
For a database-backed replay, persist only synthetic case/document/chunk rows and set
the replay version's `ledger["patient_source_manifest"] = manifest.model_dump(mode="json")`.
Then call `await build_bundle(session, case=case, version=version,
agent_role="urology_oncology")` without embedding/reranking adapters. The same path
works for the other active specialists. Stored specialist runs must share that pinned
version for synthesis. Citation validation and PPS evidence renderers resolve the new
IDs to the complete quotation and document/parse/chunk locations.

## Lossless synthesis transport

`bundle_bytes` uses `rcpia.task-bundle.transport.v1` only for synthesis bundles with
complete patient units and patient context. Specialists and legacy inputs keep their
existing wire representation. The 223,232-byte gate measures exactly the bytes written
to the runtime task file; no payload is hidden elsewhere.

The transport keeps all clinical strings, whole documents, every guideline and accepted
opinion. Explicit collection defaults replace repeated metadata. Ledger/observation/chunk
tables declare their column order, with a final `overrides` object for non-default fields.
Context segments reference chunks already included in the patient documents.
Chunk starts and exact text hashes are reconstructed from complete patient text and
recorded end offsets; ordinal defaults to the array index unless explicitly provided.
A reading may share its observation's identical value hash; it is never recalculated
from a clinical value to erase an independent attestation.

`_transport.canonical_sha256` verifies the complete reconstructed canonical bundle before
normal schema, source-integrity and citation validation. Invalid references, conflicting
defaults, altered rules, hash mismatches and malformed tables are explicit errors.
`TaskBundle.model_validate_json(wire)` accepts this transport and older expanded bundles.
For raw JSON consumers, call `rcpia.agents.transport.decode_transport` first. Old readers
that do not know this wire format must not interpret its tables as expanded objects.

`TaskBundle.model_dump`, stored `AgentRun.bundle`, source-unit/document serialization,
source IDs, manifest hashes, clinical output schemas and downstream PPS data retain their
canonical semantics. No historical record or frozen experiment is migrated.

## Partial, source-linked patient context

New snapshots additionally store `ledger.patient_context` (`rcpia.patient-context.v1`);
new bundles carry it as `inputs.patient_context`. This is a deterministic annotation,
**not** a clinical staging engine, a fully reconstructed history or a validated timeline.
Old versions/bundles without this field remain readable and do not acquire it retroactively.

- `observations` cover every recorded field in the catalogue's history, presentation,
  examination, imaging, pathology and decision categories, plus PSA total/free,
  testosterone and urine-culture fields. This includes surgical/family history, previous
  and new pathology, every imaging report and the full clinical/cardiac examination.
  Coverage follows catalogue categories, not a short manually chosen list of report names.
  Entries reference ledger field keys, value hashes,
  fact IDs and source-unit/chunk IDs. Literal ledger status and human authority remain
  separate from assessment state, normalized notation and unresolved relationships.
  `current_ledger_reading` means a currently recorded reading, not active treatment or
  a current clinical episode. Human corrections do not borrow superseded source metadata.
- `observation_scope` records that coverage policy. Other populated scalar fields are
  explicitly listed in `ledger_only_field_keys`: they remain in the ledger and complete
  sources, but are not claimed as context-annotated. Empty, never-recorded fields produce
  no observation; literal unknown placeholders with a retained reading are still represented.
  No history/report observation is dropped for deduplication or budget. If the complete
  bundle exceeds the unchanged 223,232-byte ceiling, delivery is explicitly refused.
- `source_segments` locates **every** pinned parsed chunk, including unmapped narrative
  and explicit partial assessments; it records unresolved dimensions rather than claiming
  those paragraphs were clinically interpreted. No source text is duplicated or clipped.
  On the wire, each segment carries `source_location_index` into the shared
  `source_locations` table (`source_id`, `document_id`), plus its original `chunk_id`.
  Omitted segment attributes inherit the explicit `source_segment_defaults` object,
  including all four unresolved dimensions and metadata-only interpretation. Non-default
  values, including an empty unresolved-dimension list, are retained.
  `PatientContext.model_validate(stored_context).source_segments` restores the expanded
  objects with their original `source_id`/`document_id`; earlier expanded payloads remain
  readable. Round-trip object equality and source-manifest hashes are unchanged.
- `source_assertions` quotes only exact labels within one chunk: `Organe`, `Épisode`,
  `Prélèvement`, `Spécimen`, `Date de l'examen`, `Date du prélèvement`. Values over 300
  characters or unrecognized shapes remain unresolved in the full source. There is no
  propagation across chunks or inference from disease names buried in prose. Dedicated
  organ-specific fields may carry `dedicated_field_scope`, not an asserted source organ.
- Dates accept whole `YYYY-MM-DD` or `DD/MM/YYYY` values only, including the existing
  diagnosis/pathology date fields. Upload time is never assessment time; a date field
  does not date another finding. Different dated readings are not automatically a
  contradiction or a mandate to select the latest value. Original ledger conflict flags
  remain visible as extraction metadata, not clinically adjudicated contradictions.
- Clinical T from `ct_dre` is retained independently. Dedicated stage fields are normalized
  as notation only: missing N/M remain missing, Nx/Mx remain unassessed, OCR `NO` remains
  unresolved, and `p T1 au minimum` stays a qualified lower bound, never T1a. Reports,
  lesion names and MRI sequence names are not scanned globally for TNM.
  `t_prefix`, `n_prefix` and `m_prefix` preserve each component's recorded provenance
  independently. Standalone `pM1`/`pN1` and mixed clinical/pathological notation cannot
  supply a pre-treatment clinical setting; pathological components remain visible separately.

`build_patient_context(entries, source_units, case_id=..., organization_id=...,
ledger_version=..., cancer_type=...)` is the pure replay API in
`rcpia.cases.patient_context`. Its ledger/source generation is checked again before
delivery. Context itself is not citable; specialists and synthesis cite the original
`patient-...` source IDs, resolved by the existing PPS evidence register.

The setting guard now records `rcpia.setting-derivation.v2` in query provenance.
Pathological stage no longer substitutes for clinical stage, and incomplete N/M cannot
produce non-metastatic filters. Muscle invasion recorded as `Oui` remains known even
without TNM. Hormone sensitivity/resistance is **not** computed from medication,
progression words or testosterone placeholders. Only exact existing disease-phase
labels (`mCRPC`, `nmCRPC`, `mHSPC`, `mCSPC`, or their canonical setting identifiers)
can supply a *reported* classification, with matching recorded extent.

When context is supplied, stage/phase attribution must identify the case organ and
explicitly label the episode `actuel`/`current` before those fields narrow a setting.
Missing attribution broadens the filter; it does not block delivery or require all
context fields to be completed. Explicitly foreign-organ or other-episode grade/PSA
inputs cannot be combined with a current clinical stage to narrow a risk setting.
`competing_dimensions` distinguishes conflicting explicit organ/episode labels from
absent metadata, both per reading and per source segment. Adding a competing current-organ
or current-episode label cannot clear a risk-input veto by collapsing attribution to null.
Clinical risk thresholds/calculators are unchanged.
This safety guard changes no corpus, retrieval ranking, passage budget or search policy;
live retrieval assessment is a separate validation task.

## Jobs

The queue is PostgreSQL — no Redis or message broker in v1. `rcpia.jobs.service` provides
`enqueue`, `claim_next`, `complete`, `fail`, `cancel`, `wait_for_user`, and `resume`;
`rcpia.jobs.worker.Worker` polls, dispatches to registered handlers, and records outcomes.

Claiming uses `FOR UPDATE SKIP LOCKED`, so several workers poll the same table and each
job goes to exactly one of them. A claim takes a lease (15 minutes by default); if the
worker dies, the lease expires and another worker reclaims the job. Failures retry with
exponential backoff up to `max_attempts`, keeping the error message on every attempt.

## Field catalogue

`platelets`, `bicarbonate` and `phosphorus` have one definition each. Their existing
aliases are preserved, with the later definitions' units; newly assembled ledgers and
new snapshots therefore contain only one row per field. Historical snapshots are not
rewritten.

**Clinical clarification pending:** the legacy alias `ph` still resolves to
`phosphorus`, but it can also mean blood pH. Deduplication deliberately does not change
that interpretation. A clinical decision is needed before removing or disambiguating
the alias; the cleanup is not an endorsement of that mapping.

## Case lifecycle

Case status describes the current ledger, not a clinical decision:

| Status | Meaning |
| --- | --- |
| `ready_for_review` | The current ledger needs clinician review, including any blocking fields. |
| `analyzing` | A panel on that ledger is waiting for specialists or synthesis, including retries. |
| `completed` | All panels on that ledger have settled and the newest has a successful, citation-attested synthesis. The fiche remains provisional. |
| `blocked` | All panels on that ledger have settled but the newest has no usable synthesis, including exhausted failures or cancellation. This is not the clinical completeness gate. |

A specialist failure does not change the synthesis policy: missing opinions are still explicitly
reported to the synthesis. A usable synthesis may therefore describe an incomplete panel.
Starting another panel returns the case to `analyzing`. A new ledger version returns it to
`ready_for_review`; an older panel finishing cannot mark the corrected version complete.
Document uploads remain in `parsing` until extraction commits a new ledger, including while
extraction retries or has failed. Parsing a document alone cannot restore the old fiche's
completion. An older extraction finishing after a newer upload cannot release its preparation
state.

The worker records terminal outcomes transactionally and reconciles them on every poll, including
crashes, exhausted leases, and historical panels left at `ready_for_review`. Reconciliation neither
starts a new panel nor changes any clinical record. Cases without panels remain awaiting review.

## Lockfile and private registries

`uv.lock` records `https://pypi.org/simple` as the source registry so it stays portable.
Installing behind a mirror (see the root README) makes uv rewrite those entries to the
mirror URL — if a dependency change does that, rewrite them back to `pypi.org/simple`
before committing, and never commit a `[[tool.uv.index]]` block pointing at an internal
mirror.

`uv add` does both of those things when `UV_DEFAULT_INDEX` is set, every time. After
adding a dependency behind the mirror, remove the `[[tool.uv.index]]` block from
`pyproject.toml` and rewrite the lockfile URLs back before committing. Use
`uv run --frozen ...` for everything else, which never re-locks.
