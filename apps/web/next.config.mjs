/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output is only used for the container image. On Windows it needs
  // symlink privileges (Developer Mode), so it stays opt-in via NEXT_OUTPUT.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
};

export default nextConfig;
