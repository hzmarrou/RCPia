import { afterEach, describe, expect, it, vi } from "vitest";

import InvitationsPage from "../app/admin/invitations/page";
import InvitationManager from "../app/admin/invitations/InvitationManager";
import {
  createInvitation, fetchInvitations, invitationLink, revokeInvitation,
  type InvitationSummary,
} from "../lib/auth";
import { elements, text } from "./elements";

vi.mock("next/headers", () => ({
  cookies: async () => ({ getAll: () => [{ name: "rcpia_session", value: "synthetic-session" }] }),
}));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => { throw new Error(`redirect:${path}`); },
}));

const INVITATION: InvitationSummary = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "invitee@example.org", role: "uploader", expires_at: "2026-09-13T10:00:00Z",
};

function api(status: number, body: unknown = []) {
  const mock = vi.fn(async (_url: string, _init: RequestInit) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.unstubAllGlobals());

describe("invitation API calls", () => {
  it("asks the admin-protected list endpoint and forwards the session without caching", async () => {
    const mock = api(200, [INVITATION]);
    expect(await fetchInvitations("http://api:8000", "rcpia_session=synthetic-session")).toEqual({
      state: "authorized", invitations: [INVITATION],
    });
    expect(mock.mock.calls[0]).toEqual([
      "http://api:8000/auth/invitations",
      { headers: { cookie: "rcpia_session=synthetic-session" }, cache: "no-store" },
    ]);
  });

  it.each([[401, "unauthenticated"], [403, "forbidden"]] as const)(
    "preserves an API refusal (%s) instead of returning an empty list", async (status, state) => {
      api(status);
      expect(await fetchInvitations("http://api:8000", "")).toEqual({ state });
    },
  );

  it("surfaces an unavailable list", async () => {
    api(500);
    await expect(fetchInvitations("http://api:8000", "")).rejects.toThrow("Unexpected status 500");
  });

  it("creates through the same-origin proxy and returns the one-time token", async () => {
    const issued = { ...INVITATION, token: "synthetic-token-for-test-only" };
    const mock = api(201, issued);
    expect(await createInvitation(INVITATION.email, "uploader")).toEqual(issued);
    const [url, init] = mock.mock.calls[0];
    expect(url).toBe("/api/auth/invitations");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ email: INVITATION.email, role: "uploader" });
  });

  it("revokes by id without putting any token in the request", async () => {
    const mock = api(204);
    await expect(revokeInvitation(INVITATION.id)).resolves.toBeUndefined();
    expect(mock.mock.calls[0]).toEqual([
      `/api/auth/invitations/${INVITATION.id}`, { method: "DELETE", credentials: "include" },
    ]);
  });

  it("does not treat a failed revocation as success", async () => {
    api(404, { detail: "Invitation déjà utilisée." });
    await expect(revokeInvitation(INVITATION.id)).rejects.toThrow("Invitation déjà utilisée.");
  });

  it("preserves a creation refusal and renders structured validation errors readably", async () => {
    api(409, { detail: "Un compte existe déjà." });
    await expect(createInvitation(INVITATION.email, "uploader")).rejects.toThrow("Un compte existe déjà.");
    api(422, { detail: [{ type: "value_error", msg: "Invalid email" }] });
    await expect(createInvitation("invalid", "uploader")).rejects.toThrow("Création de l'invitation impossible.");
  });
});

describe("the activation link", () => {
  it("uses the web origin, encodes the token in the fragment, and has no query", () => {
    const token = "synthetic+token&with=characters";
    const url = new URL(invitationLink("https://rcpia.example.org:8443", token));
    expect(url.origin).toBe("https://rcpia.example.org:8443");
    expect(url.pathname).toBe("/activate");
    expect(url.search).toBe("");
    expect(new URLSearchParams(url.hash.slice(1)).get("token")).toBe(token);
  });
});

describe("the invitation page", () => {
  it("renders only after the API grants access", async () => {
    api(200, [INVITATION]);
    const tree = await InvitationsPage();
    expect(elements(tree).find((node) => node.type === InvitationManager)?.props.initialInvitations)
      .toEqual([INVITATION]);
    expect(text(tree)).toContain("Aucun courriel");
    expect(elements(tree).some((node) => node.props.href === "/dashboard")).toBe(true);
  });

  it("lets an authorized empty organization create its first invitation", async () => {
    api(200, []);
    const tree = await InvitationsPage();
    expect(elements(tree).find((node) => node.type === InvitationManager)?.props.initialInvitations)
      .toEqual([]);
  });

  it("redirects an invalid session", async () => {
    api(401);
    await expect(InvitationsPage()).rejects.toThrow("redirect:/login");
  });

  it("shows the API's forbidden response, with no form or invitation metadata", async () => {
    api(403, [INVITATION]);
    const tree = await InvitationsPage();
    expect(text(tree)).toContain("Accès refusé");
    expect(text(tree)).not.toContain(INVITATION.email);
    expect(elements(tree).some((node) => node.type === InvitationManager)).toBe(false);
  });

  it("does not disguise a server failure as an empty organization", async () => {
    api(500);
    await expect(InvitationsPage()).rejects.toThrow("Unexpected status 500");
  });
});
