import { afterEach, describe, expect, it, vi } from "vitest";

import { LedgerError, correctField, fetchLedger, type Ledger } from "../lib/ledger";

type FetchArgs = [input: string, init: RequestInit];

function stubFetch(response: () => Response) {
  const mock = vi.fn<(...args: FetchArgs) => Promise<Response>>(async () => response());
  vi.stubGlobal("fetch", mock);
  return mock;
}

const LEDGER: Ledger = {
  case_id: "9d1a2b3c-4e5f-4071-8293-a4b5c6d7e8f9",
  version: 1,
  entries: [
    {
      key: "creatinine",
      label: "Créatinine",
      category: "biology",
      value: "17.33",
      status: "confirmed",
      origin: "machine",
      highlight: "neutral",
      unit: null,
      note: null,
      source_excerpt: "Créatinine : 17.33",
      source_document_id: "doc",
      source_chunk_id: "chunk",
      fact_id: "fact",
      corrected: false,
      readings: [
        {
          value: "17.33",
          source_excerpt: "Créatinine : 17.33",
          source_document_id: "doc",
          source_chunk_id: "chunk",
          fact_id: "fact",
        },
      ],
    },
  ],
  missing_fields: ["bone_scan"],
  uncertain_fields: [],
  issues: [],
  can_run_analysis: true,
  updated_at: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchLedger", () => {
  it("hides the difference between missing and forbidden", async () => {
    for (const status of [401, 403, 404]) {
      stubFetch(() => new Response("", { status }));
      await expect(
        fetchLedger("http://api:8000", "rcpia_session=x", LEDGER.case_id),
      ).resolves.toBeNull();
      vi.unstubAllGlobals();
    }
  });

  it("forwards the session cookie", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(LEDGER), { status: 200 }));

    await fetchLedger("http://api:8000", "rcpia_session=live", LEDGER.case_id);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`http://api:8000/cases/${LEDGER.case_id}/ledger`);
    expect((init.headers as Record<string, string>).cookie).toBe("rcpia_session=live");
  });

  it("throws on unexpected failures instead of showing an empty ledger", async () => {
    stubFetch(() => new Response("", { status: 500 }));

    await expect(
      fetchLedger("http://api:8000", "rcpia_session=live", LEDGER.case_id),
    ).rejects.toBeInstanceOf(LedgerError);
  });
});

describe("correctField", () => {
  it("sends the correction with its reason", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(LEDGER), { status: 201 }));

    await correctField(LEDGER.case_id, {
      factType: "creatinine",
      value: "173.3",
      note: "Virgule mal lue",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(`/cases/${LEDGER.case_id}/ledger/corrections`);
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({
      fact_type: "creatinine",
      value: "173.3",
      note: "Virgule mal lue",
    });
  });

  it("can state that a value is genuinely unknown", async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(LEDGER), { status: 201 }));

    await correctField(LEDGER.case_id, {
      factType: "bone_scan",
      value: null,
      note: "Examen non réalisé",
    });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body)).value).toBeNull();
  });

  it("surfaces the API's refusal", async () => {
    stubFetch(
      () =>
        new Response(
          JSON.stringify({ detail: "Le champ psa_total ne s'applique pas à ce type de cancer." }),
          { status: 422 },
        ),
    );

    await expect(
      correctField(LEDGER.case_id, { factType: "psa_total", value: "12", note: "Erreur" }),
    ).rejects.toThrow("ne s'applique pas");
  });
});
