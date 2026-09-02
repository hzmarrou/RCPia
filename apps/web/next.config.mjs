/** @type {import('next').NextConfig} */

// **Same-origin reverse proxy.** The browser talks only to the web app's own hostname, and
// `/api/*` is rewritten to the API container. That keeps the session cookie host-only, so no
// cross-site rules apply and `SameSite=Lax` is enough — the alternative, a shared parent
// domain with `SameSite=None; Secure`, was rejected in `docs/decisions.md` as the weaker
// position.
//
// It also means the API needs no public ingress at all: in Azure Container Apps it is
// internal-only, reachable from the web app and the worker and from nowhere else.
//
// Unset locally, where both answer on `localhost` and the rewrite is unnecessary.
const apiOrigin = process.env.API_INTERNAL_BASE_URL;

const nextConfig = {
  reactStrictMode: true,
  // Standalone output is only used for the container image. On Windows it needs
  // symlink privileges (Developer Mode), so it stays opt-in via NEXT_OUTPUT.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async rewrites() {
    if (!apiOrigin) {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

export default nextConfig;
