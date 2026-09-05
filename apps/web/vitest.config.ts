import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // Next compiles JSX with the automatic runtime; esbuild would otherwise fall back to the
  // classic one and every component imported by a test fails with "React is not defined".
  esbuild: { jsx: "automatic" },
  resolve: {
    // Mirror the "@/*" path alias from tsconfig.json.
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    globals: true,
    environment: "node",
    include: ["tests/**/*.spec.ts"],
  },
});
