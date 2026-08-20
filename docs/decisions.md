# Product Decisions

Short record of decisions that are not derivable from the code or the design spec.
Newest first.

## 2026-08-16 — Session cookie topology must be decided before deployment

Locally the web app and the API both answer on `localhost`, so a host-only session cookie
set by the API is visible to the Next.js server. That does **not** survive a deployment
where the two get separate hostnames.

Before deploying, pick one:

1. **Same-origin reverse proxy (preferred).** One public hostname; `/api/*` is proxied to
   the API container. The cookie stays host-only and no cross-site rules apply.
2. **Shared parent domain.** `web.example.org` and `api.example.org` with
   `SESSION_COOKIE_DOMAIN=.example.org`. Requires `SameSite=None; Secure` for
   browser-initiated cross-site calls, which is a weaker position.

`SESSION_COOKIE_DOMAIN` and `SESSION_COOKIE_SAMESITE` exist so the choice is
configuration, not a code change. Phase 14 (Azure Container Apps) must resolve it.

## 2026-08-16 — Login throttling is per-process for the MVP

`rcpia.auth.throttle` counts attempts in process memory: ten per address per five minutes.
With one API container that is accurate. Running several replicas under-counts by the
number of replicas, so a shared counter (PostgreSQL, or the reverse proxy) is required
before the API is scaled out.

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

## 2026-08-20 — PostgreSQL 17, not 16

`RCPia-v1-implementation-plan.md` and the agent prompt both specify
`pgvector/pgvector:pg16`. Their stated reason is only that the standard `postgres` image
lacks `pgvector`; the major version was whatever was current when the brief was written,
and nothing in the design depends on 16.

Moved to `pgvector/pgvector:pg17` before Phase 6, because that is the last moment it is
cheap. Once a guideline corpus and its embeddings are in the database, a major-version
bump means dumping and re-embedding rather than dumping and restoring.

It also keeps `pg_textsearch` available as an option — it requires 17 or 18, and provides
real BM25 ranking rather than `ts_rank_cd`. Not adopted: it needs `shared_preload_libraries`
and has no phrase search, which matters for compound clinical terms such as
*BCG-unresponsive*. Revisit if the retrieval evaluation shows keyword recall is the
limiting factor.

Not 19: unreleased as of this date, and `pgvector` publishes no `pg19` image. Its SQL/PGQ
property-graph support is processed internally as views over relational queries, so it
would give nicer traversal syntax rather than new capability — worth revisiting only when
Wiki Thinking gives us a real traversal use case.

The brief was not amended; this record is the authority.

## Open

- Which of the two reference PPS examples is human-written and which is machine-generated
  (awaiting the treating clinician).
- Retention period and erasure authorization for dossiers and derived records.
