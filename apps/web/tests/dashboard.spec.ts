import { afterEach, describe, expect, it, vi } from "vitest";

import CaseList from "../app/_components/CaseList";
import DashboardPage from "../app/dashboard/page";
import { caseSummary } from "./caseFixtures";
import { elements, text } from "./elements";

vi.mock("next/headers", () => ({
  cookies: async () => ({ getAll: () => [{ name: "rcpia_session", value: "synthetic-session" }] }),
}));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => { throw new Error(`redirect:${path}`); },
}));

const USER = {
  id: "synthetic-user", organization_id: "synthetic-org", email: "demo@example.test",
  full_name: "Compte de démonstration", role: "uploader", is_active: true,
};

function api(accessStatus = 200, casesStatus = 200, cases = [caseSummary("one")]) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
    expect((init.headers as Record<string, string>).cookie).toBe("rcpia_session=synthetic-session");
    expect(init.cache).toBe("no-store");
    if (url.endsWith("/auth/dashboard")) {
      return new Response(JSON.stringify(USER), { status: accessStatus });
    }
    if (url.endsWith("/cases")) {
      return new Response(JSON.stringify(cases), { status: casesStatus });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("the authorized dashboard", () => {
  it("loads the API's case list into the same renderer as the rail", async () => {
    const cases = [caseSummary("one"), caseSummary("two")];
    const fetchMock = api(200, 200, cases);
    const tree = await DashboardPage();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(elements(tree).find((node) => node.type === CaseList)?.props.cases).toEqual(cases);
    expect(elements(tree).some((node) => node.props.href === "/cases/new")).toBe(true);
    expect(elements(tree).some((node) => node.props.href === "/admin/invitations")).toBe(true);
    expect(text(tree)).toContain("Compte de démonstration");
    expect(text(tree)).not.toContain("sera disponible ici");
  });

  it("passes a genuinely empty list to the first-run presentation", async () => {
    api(200, 200, []);
    const tree = await DashboardPage();
    expect(elements(tree).find((node) => node.type === CaseList)?.props.cases).toEqual([]);
  });

  it("redirects an expired session before reading cases", async () => {
    const fetchMock = api(401);
    await expect(DashboardPage()).rejects.toThrow("redirect:/login");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("respects the API's refusal without guessing permission from the role", async () => {
    const fetchMock = api(403);
    const tree = await DashboardPage();
    expect(text(tree)).toContain("Accès");
    expect(elements(tree).some((node) => node.type === CaseList)).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403])("does not mistake a %s on /cases for an empty account", async (status) => {
    api(200, status);
    const tree = await DashboardPage();
    expect(text(tree)).toContain("Dossiers indisponibles");
    expect(elements(tree).some((node) => node.props.role === "alert")).toBe(true);
    expect(elements(tree).some((node) => node.type === CaseList)).toBe(false);
  });

  it.each([[500, 200], [200, 500]])("surfaces API failures (%s, %s)", async (access, cases) => {
    api(access, cases);
    await expect(DashboardPage()).rejects.toThrow(/Unexpected status 500/);
  });
});
