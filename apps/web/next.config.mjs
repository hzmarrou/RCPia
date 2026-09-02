/** @type {import('next').NextConfig} */

// **The `/api/*` proxy is a route handler, not a rewrite.** Next evaluates `rewrites()` during
// `next build` and freezes the result into `routes-manifest.json`, so a destination read from
// the environment at runtime is simply absent — measured on the first Azure deployment, where
// the manifest shipped as `"rewrites":[]` and every `/api/*` call returned a Next 404.
//
// Building the destination in would work and would tie each image to one environment. The
// handler in `app/api/[...path]/route.ts` reads `API_INTERNAL_BASE_URL` per request instead,
// so one image runs in staging and production alike.
const nextConfig = {
  reactStrictMode: true,
  // Standalone output is only used for the container image. On Windows it needs symlink
  // privileges (Developer Mode), so it stays opt-in via NEXT_OUTPUT.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
};

export default nextConfig;
