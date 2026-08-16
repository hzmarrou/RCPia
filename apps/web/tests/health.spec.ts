import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchHealth } from "../lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchHealth", () => {
  it("returns the API status payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 })),
    );

    await expect(fetchHealth("http://api:8000")).resolves.toEqual({ status: "ok" });
  });

  it("throws when the API responds with an error status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));

    await expect(fetchHealth("http://api:8000")).rejects.toThrow(
      "API health check failed with status 503",
    );
  });
});
