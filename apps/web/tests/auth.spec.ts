import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthError, fetchCurrentUser, fetchDashboardAccess, login, lookupInvitation } from "../lib/auth";

type FetchArgs = [input: string, init: RequestInit];

/** A fetch double whose recorded calls keep their argument types. */
function stubFetch(response: () => Response) {
  const mock = vi.fn<(...args: FetchArgs) => Promise<Response>>(async () => response());
  vi.stubGlobal("fetch", mock);
  return mock;
}

const USER = {
  id: "0f1d2c3b-4a59-4687-9c1e-2f3a4b5c6d7e",
  organization_id: "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
  email: "uploader@example.org",
  full_name: "Dr Uploader",
  role: "uploader",
  is_active: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("login", () => {
  it("sends credentials and keeps the session cookie", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(USER), { status: 200 }));

    await expect(login("uploader@example.org", "correct-horse-battery")).resolves.toEqual(
      USER,
    );

    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe("include");
    expect(init.method).toBe("POST");
  });

  it("surfaces the API error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Invalid email or password" }), {
            status: 401,
          }),
      ),
    );

    await expect(login("ghost@example.org", "whatever")).rejects.toThrow(
      "Invalid email or password",
    );
  });
});

describe("fetchCurrentUser", () => {
  it("returns null when the session is not valid", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));

    await expect(fetchCurrentUser("http://api:8000", "rcpia_session=stale")).resolves.toBeNull();
  });

  it("forwards the session cookie to the API", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(USER), { status: 200 }));

    await fetchCurrentUser("http://api:8000", "rcpia_session=live-token");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api:8000/auth/me");
    expect((init.headers as Record<string, string>).cookie).toBe("rcpia_session=live-token");
  });

  it("throws on unexpected API failures rather than pretending nobody is logged in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));

    await expect(
      fetchCurrentUser("http://api:8000", "rcpia_session=live"),
    ).rejects.toBeInstanceOf(AuthError);
  });
});

describe("fetchDashboardAccess", () => {
  it("reports the API's 403 instead of deciding roles in the frontend", async () => {
    stubFetch(() => new Response("", { status: 403 }));

    await expect(
      fetchDashboardAccess("http://api:8000", "rcpia_session=viewer"),
    ).resolves.toEqual({ state: "forbidden" });
  });

  it("reports an unauthenticated session", async () => {
    stubFetch(() => new Response("", { status: 401 }));

    await expect(
      fetchDashboardAccess("http://api:8000", "rcpia_session=stale"),
    ).resolves.toEqual({ state: "unauthenticated" });
  });

  it("asks the role-protected endpoint, not /auth/me", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(USER), { status: 200 }));

    const access = await fetchDashboardAccess("http://api:8000", "rcpia_session=live");

    expect(fetchMock.mock.calls[0][0]).toBe("http://api:8000/auth/dashboard");
    expect(access).toEqual({ state: "authorized", user: USER });
  });
});

describe("lookupInvitation", () => {
  it("sends the token in the body so it stays out of the URL", async () => {
    const fetchMock = stubFetch(
      () =>
        new Response(
          JSON.stringify({
            id: "8f0b1c2d-3e4f-4a5b-9c8d-7e6f5a4b3c2d",
            email: "invitee@example.org",
            role: "uploader",
            expires_at: "2026-08-23T10:00:00Z",
          }),
          { status: 200 },
        ),
    );

    await lookupInvitation("a-very-long-invitation-token");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/invitations/lookup");
    expect(url).not.toContain("a-very-long-invitation-token");
    expect(init.method).toBe("POST");
    expect(init.body).toContain("a-very-long-invitation-token");
  });
});
