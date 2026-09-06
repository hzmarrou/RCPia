import { afterEach, describe, expect, it, vi } from "vitest";

import { CaseError, createCase, fetchCase, uploadDossier } from "../lib/cases";

type FetchArgs = [input: string, init: RequestInit];

function stubFetch(response: () => Response) {
  const mock = vi.fn<(...args: FetchArgs) => Promise<Response>>(async () => response());
  vi.stubGlobal("fetch", mock);
  return mock;
}

const CASE = {
  id: "3f2a1b4c-5d6e-4f70-8a91-b2c3d4e5f607",
  reference: "Dossier 2",
  cancer_type: "bladder",
  clinical_question: null,
  status: "draft",
  created_at: "2026-08-17T09:00:00Z",
  updated_at: "2026-08-17T09:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createCase", () => {
  it("sends the case with credentials", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(CASE), { status: 201 }));

    await expect(
      createCase({ reference: "Dossier 2", cancerType: "bladder", clinicalQuestion: "" }),
    ).resolves.toEqual(CASE);

    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({
      reference: "Dossier 2",
      cancer_type: "bladder",
      clinical_question: null,
    });
  });

  it("surfaces the API's rejection message", async () => {
    stubFetch(
      () =>
        new Response(JSON.stringify({ detail: "Un dossier portant la référence existe déjà." }), {
          status: 409,
        }),
    );

    await expect(
      createCase({ reference: "Dossier 2", cancerType: "bladder", clinicalQuestion: "" }),
    ).rejects.toThrow("Un dossier portant la référence existe déjà.");
  });
});

describe("uploadDossier", () => {
  it("posts multipart form data", async () => {
    const fetchMock = stubFetch(
      () => new Response(JSON.stringify({ id: "doc-1" }), { status: 201 }),
    );
    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "dossier.pdf", {
      type: "application/pdf",
    });

    await uploadDossier(CASE.id, file);

    const [url, init] = fetchMock.mock.calls[0];
    // Same origin as the page: an upload names no host of its own. See tests/apiBaseUrl.spec.ts.
    expect(url).toBe(`/api/cases/${CASE.id}/documents`);
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
  });

  it("reports a refused file type", async () => {
    stubFetch(
      () =>
        new Response(JSON.stringify({ detail: "Seuls les fichiers .docx et .pdf sont acceptés." }), {
          status: 415,
        }),
    );
    const file = new File(["free text"], "notes.txt", { type: "text/plain" });

    await expect(uploadDossier(CASE.id, file)).rejects.toThrow(
      "Seuls les fichiers .docx et .pdf sont acceptés.",
    );
  });
});

describe("fetchCase", () => {
  it("treats not-found and forbidden alike, revealing nothing", async () => {
    for (const status of [401, 403, 404]) {
      stubFetch(() => new Response("", { status }));
      await expect(
        fetchCase("http://api:8000", "rcpia_session=x", CASE.id),
      ).resolves.toBeNull();
      vi.unstubAllGlobals();
    }
  });

  it("throws on unexpected failures rather than hiding them as not-found", async () => {
    stubFetch(() => new Response("", { status: 500 }));

    await expect(
      fetchCase("http://api:8000", "rcpia_session=x", CASE.id),
    ).rejects.toBeInstanceOf(CaseError);
  });
});
