/**
 * Where the API lives — which is two different answers, and conflating them shipped a
 * staging build whose every browser-side call went to `http://localhost:8000`.
 *
 * **The browser** must only ever address the web app's own origin. `app/api/[...path]/route.ts`
 * proxies `/api/*` to the API, which is what lets the API run with internal ingress and keeps
 * the session cookie host-only. So the browser's base URL is a *relative* path, and it needs
 * no configuration to be correct anywhere.
 *
 * **The server** renders inside the container and cannot use a relative URL at all: it needs a
 * real origin, and on Azure that is the internal FQDN rather than the browser-visible host.
 *
 * The trap this replaces: `NEXT_PUBLIC_*` is inlined by `next build`, not read at runtime.
 * Setting `NEXT_PUBLIC_API_BASE_URL=/api` on the container did nothing, because the image was
 * built without it and the old `"http://localhost:8000"` default was already baked into the
 * client bundle. It looked fine — every server-rendered page worked — while login, upload,
 * convening a panel and progress polling were all dead. Defaulting to the proxy means the
 * variable is an override, not a requirement, so forgetting it cannot break a deployment.
 * Same build-time-versus-runtime trap `route.ts` documents for `rewrites()`.
 */

// Used from the browser. Relative on purpose: same origin, any deployment, no build arg.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

// Used from server components and route handlers, which need an absolute origin.
export const SERVER_API_BASE_URL =
  process.env.API_INTERNAL_BASE_URL ?? "http://localhost:8000";

export type HealthResponse = {
  status: string;
};

export async function fetchHealth(
  baseUrl: string = API_BASE_URL,
): Promise<HealthResponse> {
  const response = await fetch(`${baseUrl}/health`, { cache: "no-store" });

  if (!response.ok) {
    throw new Error(`API health check failed with status ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}
