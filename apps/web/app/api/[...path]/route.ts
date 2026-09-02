/**
 * Same-origin proxy to the API, resolved at request time.
 *
 * **Why not `rewrites()` in `next.config.mjs`.** Next evaluates rewrites during `next build`
 * and writes the result into `routes-manifest.json`; setting `API_INTERNAL_BASE_URL` at
 * runtime does nothing, and the manifest ships as `"rewrites":[]`. That was measured on the
 * first Azure deployment: the web app returned 200 and `/api/health` returned a Next 404.
 *
 * Building the destination in would work and would make the image environment-specific — a
 * separate build per environment, and a staging image that silently talks to staging if it
 * ever reached production. A route handler reads the variable per request, so one image runs
 * anywhere.
 *
 * **Why proxy at all.** The browser must only ever address the web app's own origin, which
 * keeps the session cookie host-only and `SameSite=Lax` sufficient — and lets the API run with
 * internal ingress and no public surface. `docs/decisions.md` records the rejected
 * alternative: a shared parent domain needing `SameSite=None; Secure`.
 */

import { NextRequest } from "next/server";

// Cookies and per-user data pass through here; nothing about it may be cached or
// pre-rendered.
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

// Hop-by-hop headers, and the ones the upstream must set for itself. Forwarding `host` sends
// the browser's hostname to a container that answers on another, and forwarding a
// content-length that no longer matches the re-streamed body makes the request hang.
const STRIP_REQUEST = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-length",
]);
const STRIP_RESPONSE = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
]);

async function proxy(request: NextRequest): Promise<Response> {
  const incoming = new URL(request.url);
  // `/api/cases/123/ledger` -> `/cases/123/ledger`. The API is not itself mounted under
  // `/api`; the prefix exists only to mark what this proxy claims.
  const target = new URL(
    incoming.pathname.replace(/^\/api/, "") + incoming.search,
    API_ORIGIN,
  );

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP_REQUEST.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    // Streamed rather than buffered: a dossier upload is multipart and can be tens of
    // megabytes, and reading it into memory first would hold the whole file per request.
    body: hasBody ? request.body : undefined,
    // Required by undici whenever a stream is used as a body.
    ...(hasBody ? { duplex: "half" } : {}),
    redirect: "manual",
    cache: "no-store",
  } as RequestInit);

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE.has(key.toLowerCase())) responseHeaders.append(key, value);
  });
  // `Set-Cookie` must survive intact — it is the session — and `Headers.forEach` collapses
  // repeats, so it is re-read from the raw list where the runtime exposes one.
  const setCookie = (upstream.headers as unknown as { getSetCookie?: () => string[] })
    .getSetCookie?.();
  if (setCookie?.length) {
    responseHeaders.delete("set-cookie");
    for (const cookie of setCookie) responseHeaders.append("set-cookie", cookie);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
