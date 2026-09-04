import { afterEach, describe, expect, it, vi } from "vitest";

import { docxUrl, downloadUrl, fetchPps, probatoireUrl } from "../lib/pps";

type FetchArgs = [input: string, init: RequestInit];

function stubFetch(response: () => Response) {
  const mock = vi.fn<(...args: FetchArgs) => Promise<Response>>(async () => response());
  vi.stubGlobal("fetch", mock);
  return mock;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchPps", () => {
  it("forwards the session cookie and never caches a clinical document", async () => {
    const mock = stubFetch(() => json({ case_reference: "CASE-1" }));

    await fetchPps("http://api", "session=abc", "case-1");

    const [url, init] = mock.mock.calls[0];
    expect(url).toBe("http://api/cases/case-1/pps");
    expect(init.headers).toEqual({ cookie: "session=abc" });
    expect(init.cache).toBe("no-store");
  });

  it("separates 'no analysis yet' from a real failure", async () => {
    // The page says something different for each, so the caller must be able to tell.
    stubFetch(() => json({ detail: "lancez une analyse" }, 409));

    const result = await fetchPps("http://api", "", "case-1");

    expect(result.kind).toBe("not-ready");
    expect(result.kind === "not-ready" && result.detail).toContain("lancez une analyse");
  });

  it("treats a missing case and another organization's case identically", async () => {
    for (const status of [401, 403, 404]) {
      stubFetch(() => json({}, status));
      const result = await fetchPps("http://api", "", "case-1");
      expect(result.kind).toBe("forbidden");
    }
  });

  it("raises on an unexpected status rather than rendering an empty document", async () => {
    stubFetch(() => json({}, 500));

    await expect(fetchPps("http://api", "", "case-1")).rejects.toThrow(/500/);
  });

  it("returns the document when the API answers", async () => {
    stubFetch(() => json({ case_reference: "CASE-1", ledger_version: 2 }));

    const result = await fetchPps("http://api", "", "case-1");

    expect(result.kind).toBe("ok");
    expect(result.kind === "ok" && result.pps.ledger_version).toBe(2);
  });
});

describe("downloadUrl", () => {
  it("points at the markdown endpoint for the case", () => {
    expect(downloadUrl("case-1")).toMatch(/\/cases\/case-1\/pps\/markdown$/);
  });
});

describe("the Word downloads", () => {
  it("hands the fiche and its evidence register two separate endpoints", () => {
    expect(docxUrl("case-1")).toMatch(/\/cases\/case-1\/pps\/docx$/);
    expect(probatoireUrl("case-1")).toMatch(/\/cases\/case-1\/pps\/docx\/probatoire$/);
  });

  it("never points the fiche at the probatoire", () => {
    expect(docxUrl("case-1")).not.toBe(probatoireUrl("case-1"));
  });
});
