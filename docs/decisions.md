# Product Decisions

Short record of decisions that are not derivable from the code or the design spec.
Newest first.

## 2026-08-16 — RCPia is a web application, not a mobile app

`docs/RCPia-FMPM.md` (the project brief) describes *"une application mobile déployée sur
Android et IOS"*. That line is superseded: RCPia v1 is a **web application** — Next.js
frontend, FastAPI backend — as specified in `RCPia-v1-design-spec.md` and built in
`apps/web` and `apps/api`. The brief was not amended; this record is the authority.

## 2026-08-16 — MVP accepts cloud parsing and unmasked storage

See `docs/transparency/data-handling.md`. Only de-identified or simulated dossiers may be
uploaded; simulated dossiers may contain invented names. The system is expected to be
rebuilt on an internal hospital server before production use.

## 2026-08-16 — Clinical output is written in French

Dossiers, PPS documents, agent reasoning, and rationale appendices are French. Code,
identifiers, database columns, and developer documentation stay English. See
`docs/reference/pps-format.md`.

## Open

- Which of the two reference PPS examples is human-written and which is machine-generated
  (awaiting the treating clinician).
- Retention period and erasure authorization for dossiers and derived records.
