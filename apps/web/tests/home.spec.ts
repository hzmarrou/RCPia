import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The landing page is a source file, not a rendered tree, in these tests: it is a server
 * component that reaches the API, and the two things worth pinning are structural rather
 * than behavioural — that there is a way in, and that the API's address is not published to
 * whoever happens to open the page.
 */
const SOURCE = readFileSync(join(__dirname, "..", "app", "page.tsx"), "utf8");

describe("the landing page", () => {
  it("offers a way in", () => {
    // It was a health card with no link at all: a visitor who did not already know to type
    // /login had reached a dead end, which is how invited clinicians actually arrive.
    expect(SOURCE).toContain('href="/login"');
  });

  it("points people with no account at the right place", () => {
    expect(SOURCE).toContain('href="/request-access"');
  });

  it("does not publish the API base URL", () => {
    // Told an anonymous visitor where the data lives; told an invited clinician nothing.
    expect(SOURCE).not.toMatch(/\{\s*SERVER_API_BASE_URL\s*\}/);
  });
});
