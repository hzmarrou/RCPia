import { readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { config } from "../middleware";

/**
 * Guard against a private route that forgets the gate.
 *
 * A page missing from the matcher is not a data leak — the API holds everything and answers
 * 401 to a session it does not recognise. What it does is turn "you are signed out" into
 * "404, this page could not be found", which tells a clinician their case has vanished. The
 * `/poste` workstation shipped that way for one commit, which is why this exists.
 */

// Top-level route folders anyone may reach without a session, with the reason.
const PUBLIC = new Set([
  "login", // the way in
  "activate", // the invitation token is the credential
  "request-access", // for people who have no account yet
  "api", // the proxy route; the API behind it does its own authentication
]);

function routeFolders(): string[] {
  return readdirSync(join(__dirname, "..", "app"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("_"))
    .map((entry) => entry.name);
}

describe("the middleware matcher", () => {
  it("covers every route folder that is not deliberately public", () => {
    const guarded = config.matcher.map((pattern) => pattern.split("/")[1]);
    const unguarded = routeFolders().filter(
      (folder) => !PUBLIC.has(folder) && !guarded.includes(folder),
    );

    expect(unguarded, "add these to the matcher, or to PUBLIC with a reason").toEqual([]);
  });

  it("guards the workstation", () => {
    expect(config.matcher).toContain("/poste/:path*");
  });
});
