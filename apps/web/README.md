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

- `NEXT_PUBLIC_API_BASE_URL` — browser-visible API URL (default `http://localhost:8000`).
- `API_INTERNAL_BASE_URL` — server-side API URL inside Docker (e.g. `http://api:8000`).
