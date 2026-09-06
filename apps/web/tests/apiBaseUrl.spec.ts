import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchActivity } from "@/lib/activity";
import { API_BASE_URL, SERVER_API_BASE_URL } from "@/lib/api";
import { login } from "@/lib/auth";
import { convenePanel } from "@/lib/ledger";
import { docxUrl, downloadUrl } from "@/lib/pps";

/**
 * Where the browser sends its requests.
 *
 * A staging build shipped with `http://localhost:8000` compiled into the client bundle. The
 * container had `NEXT_PUBLIC_API_BASE_URL=/api` set, but `NEXT_PUBLIC_*` is inlined by
 * `next build` and the image was built without it, so every browser-side call — login,
 * upload, convening a panel, progress polling, fiche downloads — went to a host that does
 * not exist for the visitor. Nothing failed loudly: server-rendered pages were fine, so the
 * app looked healthy while none of it worked.
 *
 * These ask what URL each function actually requests, rather than reading the source for a
 * constant, because the defect was that the compiled value differed from the configured one.
 * A relative URL resolves against the page's own origin and reaches the `/api/*` proxy in
 * `app/api/[...path]/route.ts`, which is the only address the browser is allowed to use.
 */

function captureFetch(body = "[]", status = 200) {
  const spy = vi.fn(async () => new Response(body, { status }));
  vi.stubGlobal("fetch", spy);
  return spy;
}

/** The URL passed to the first fetch call, however it was expressed. */
function requestedUrl(spy: ReturnType<typeof captureFetch>): string {
  return String((spy.mock.calls[0] as unknown[])[0]);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the browser's API base URL", () => {
  it("is relative, so it resolves against whatever origin serves the page", () => {
    // An absolute URL is the bug: it pins the client bundle to one deployment at build time.
    expect(API_BASE_URL.startsWith("/")).toBe(true);
    expect(API_BASE_URL).not.toMatch(/^https?:\/\//);
  });

  it("points at the same-origin proxy", () => {
    expect(API_BASE_URL).toBe("/api");
  });
});

describe("the server's API base URL", () => {
  it("is absolute, because a server component cannot fetch a relative URL", () => {
    expect(SERVER_API_BASE_URL).toMatch(/^https?:\/\//);
  });
});

describe("what the browser actually requests", () => {
  it("polls activity on the page's own origin", async () => {
    // The call that was dead on staging: the progress bars polled a refused connection, and
    // the workstation correctly reported "état inconnu" for the whole run.
    const spy = captureFetch();
    await fetchActivity("case-1");
    expect(requestedUrl(spy)).toBe("/api/cases/case-1/activity");
  });

  it("convenes a panel on the page's own origin", async () => {
    const spy = captureFetch("{}", 202);
    await convenePanel("case-1");
    expect(requestedUrl(spy)).toBe("/api/cases/case-1/analysis/panel");
  });

  it("signs in on the page's own origin", async () => {
    const spy = captureFetch('{"id":"u1"}');
    await login("someone@example.org", "secret");
    expect(requestedUrl(spy)).toBe("/api/auth/login");
  });

  it("builds fiche download links on the page's own origin", () => {
    // These are href values, not fetches; an absolute internal URL would render a dead link.
    expect(downloadUrl("case-1")).toBe("/api/cases/case-1/pps/markdown");
    expect(docxUrl("case-1")).toBe("/api/cases/case-1/pps/docx");
  });

  it("never addresses a host of its own", async () => {
    // The whole class of defect, stated once: nothing the browser calls may name an origin.
    const spy = captureFetch();
    await fetchActivity("case-1");
    await convenePanel("case-1");
    for (const call of spy.mock.calls) {
      expect(String((call as unknown[])[0])).not.toMatch(/^https?:\/\//);
    }
  });
});
