# RCPia Web

Next.js (App Router) frontend for RCPia v1. Node dependencies are managed with
[pnpm](https://pnpm.io/).

```bash
pnpm install     # install deps
pnpm dev         # dev server on :3000
pnpm test        # vitest smoke tests
pnpm typecheck   # tsc --noEmit
pnpm lint        # next lint
pnpm build       # production build (standalone output)
```

After any `pnpm install` that adds or updates dependencies behind a mirrored registry,
run `pnpm lock:normalize` and commit the lockfile. It strips absolute tarball URLs that
would otherwise pin `pnpm-lock.yaml` to one registry mirror and break
`--frozen-lockfile` in CI.

`pnpm build` produces a normal build. Set `NEXT_OUTPUT=standalone` for the container
build — Windows hosts need Developer Mode for the symlinks that standalone output
creates, so it is opt-in rather than default.

Environment:

- `NEXT_PUBLIC_API_BASE_URL` — browser-visible API URL (default relative `/api`).
- `API_INTERNAL_BASE_URL` — server-side API URL inside Docker (e.g. `http://api:8000`).

## Workstation

The dashboard and workstation rail share the same case list: grouped by deposit day,
newest first, with the last-updated timestamp displayed explicitly in UTC. Status dots
always have text labels; a terminal panel without usable synthesis is not a missing-field
warning.

The workstation follows `docs/design/poste-mock.html`, with light/dark semantic tokens
and locally bundled IBM Plex fonts (no font CDN). Blocking badges and the launch explanation
use the API's issue severities; the launch permission remains `can_run_analysis`.
The existing missing/uncertain counts still include fields marked "Sans objet".

Progress remains indeterminate, showing only reported milestones and elapsed time.
Questions is a disabled, forthcoming tab; no question-answering backend is connected.

## Invitations

`/admin/invitations`, linked from the dashboard, lets an organization administrator
create invitations, copy their one-time activation links and revoke outstanding
invitations. Access is decided by `GET /auth/invitations`, not a frontend role check.
The API lists only the caller's organization's unexpired, pending invitations.

Activation links use the web origin and a URL fragment (`/activate#token=…`).
They are held only in the current page's memory, never in browser storage. Copy a
link before creating another invitation or leaving the page; lost links cannot be
retrieved. No invitation email is sent automatically.
