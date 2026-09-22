import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import DiscussionPlaceholder from "../app/_components/DiscussionPlaceholder";
import {
  SourceAttestation, SourceBadge, SourceBlocks, SourceNotice, SourceStatus,
} from "../app/_components/SourceReview";
import type { LedgerEntry } from "../lib/ledger";
import {
  annotateSource, canAttestAbsence, fetchSourceDocument, fetchSources, originalDocumentUrl,
  SOURCE_STATES, SourceError, SourceReviewController,
  type SourceDocument, type SourceField, type SourceIndex, type SourceState,
} from "../lib/sources";

const FIELD = {
  key: "synthetic_field", state: "unverified" as const, label: "Champ synthétique",
  explanation: "Aucune présence attestée.", links: [], reviewed: false, review_note: null,
  can_attest_absence: true,
};
const INDEX: SourceIndex = {
  case_id: "synthetic-a", ledger_version: 2, source_fingerprint: "a".repeat(64),
  scope: "pinned_snapshot", notice: null,
  fields: [FIELD],
  documents: [{
    id: "doc-a", filename: "Compte rendu synthétique", parse_run: 1,
    uploaded_at: "2026-09-01T10:00:00Z", pages: [1, 2], chunks: 2, coverage: "complete_parsed_source",
  }],
};
const DOCUMENT: SourceDocument = {
  case_id: INDEX.case_id, ledger_version: INDEX.ledger_version, source_fingerprint: INDEX.source_fingerprint,
  document: INDEX.documents[0],
  blocks: [
    { id: "block-a", ordinal: 0, text: "Texte synthétique <script>alert('test')</script>\n[.*", page: 1, section: "Résumé", block_type: "paragraph" },
    { id: "block-b", ordinal: 1, text: "Fin du compte rendu : réserve importante, sans conclusion automatique.", page: 2, section: null, block_type: "paragraph" },
  ],
};
const ENTRY: LedgerEntry = {
  key: FIELD.key, label: FIELD.label, value: null, category: "imaging", status: "missing",
  origin: "machine", highlight: "red", unit: null, note: null, corrected: false, readings: [],
  source_excerpt: null, source_document_id: null, source_chunk_id: null, fact_id: null,
};
const response = (data: unknown) => new Response(JSON.stringify(data));
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function fetchMock() {
  const mock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", mock);
  return mock;
}
async function loadedController() {
  const mock = fetchMock();
  mock.mockResolvedValueOnce(response(INDEX));
  const controller = new SourceReviewController(INDEX.case_id, INDEX.ledger_version);
  await controller.load();
  return { mock, controller };
}
afterEach(() => vi.unstubAllGlobals());

describe("source API contract", () => {
  it("uses authenticated, no-store reads with encoded case/document identifiers and exact pin", async () => {
    const mock = fetchMock().mockResolvedValueOnce(response(INDEX)).mockResolvedValueOnce(response(DOCUMENT));
    await fetchSources("case / test");
    await fetchSourceDocument(INDEX, "doc / test");
    expect(mock.mock.calls[0][0]).toBe("/api/cases/case%20%2F%20test/sources");
    expect(mock.mock.calls[0][1]).toMatchObject({ cache: "no-store", credentials: "include" });
    expect(mock.mock.calls[1][0]).toContain("/sources/doc%20%2F%20test?");
    const url = new URL(String(mock.mock.calls[1][0]), "https://test.invalid");
    expect(url.searchParams.get("ledger_version")).toBe("2");
    expect(url.searchParams.get("source_fingerprint")).toBe(INDEX.source_fingerprint);
    expect(mock.mock.calls[1][1]).toMatchObject({ cache: "no-store", credentials: "include" });
    expect(originalDocumentUrl("case / test", "doc / test")).toBe("/api/cases/case%20%2F%20test/documents/doc%20%2F%20test/content");
  });

  it("omits the optional ledger query when no ledger exists, but never omits the fingerprint", async () => {
    const mock = fetchMock().mockResolvedValue(response(DOCUMENT));
    await fetchSourceDocument({ ...INDEX, ledger_version: null }, "doc-a");
    expect(String(mock.mock.calls[0][0])).not.toContain("ledger_version");
    expect(String(mock.mock.calls[0][0])).toContain(INDEX.source_fingerprint);
  });

  it("sends only an audited annotation, never a fact or an analysis request", async () => {
    const mock = fetchMock().mockResolvedValue(response(INDEX));
    await annotateSource(INDEX, FIELD.key, "absent", " Motif synthétique ");
    expect(mock).toHaveBeenCalledTimes(1);
    expect(mock.mock.calls[0][0]).toBe(`/api/cases/${INDEX.case_id}/sources/review`);
    expect(JSON.parse(String(mock.mock.calls[0][1]?.body))).toEqual({
      expected_ledger_version: 2, source_fingerprint: INDEX.source_fingerprint,
      field_key: FIELD.key, status: "absent", note: "Motif synthétique",
    });
  });

  it.each([401, 403, 404, 409, 422, 500])("surfaces HTTP %s instead of an empty source index", async (status) => {
    fetchMock().mockResolvedValue(new Response("", { status }));
    await expect(fetchSources(INDEX.case_id)).rejects.toMatchObject({ status });
  });

  it("reports network and malformed response failures", async () => {
    const mock = fetchMock().mockRejectedValueOnce(new Error("offline"));
    await expect(fetchSources(INDEX.case_id)).rejects.toThrow("connexion");
    mock.mockResolvedValueOnce(new Response("not json"));
    await expect(fetchSources(INDEX.case_id)).rejects.toThrow("illisible");
  });
});

describe("source request lifecycle", () => {
  it("exposes loading, then ready, without turning an empty field into an absence", async () => {
    const { controller } = await loadedController();
    expect(controller.getSnapshot()).toMatchObject({ phase: "ready", documentPhase: "idle", opened: false });
    expect(controller.getSnapshot().index?.fields[0].state).toBe("unverified");
  });

  it("ignores a late case-A index after disposal while case B stays independent", async () => {
    const mock = fetchMock();
    const late = deferred<Response>();
    mock.mockReturnValueOnce(late.promise).mockResolvedValueOnce(response({ ...INDEX, case_id: "synthetic-b" }));
    const a = new SourceReviewController(INDEX.case_id, 2);
    const pending = a.load();
    a.dispose();
    const b = new SourceReviewController("synthetic-b", 2);
    await b.load();
    late.resolve(response(INDEX));
    await pending;
    expect(a.getSnapshot().index).toBeNull();
    expect(b.getSnapshot().index?.case_id).toBe("synthetic-b");
  });

  it("ignores a late case-A document after switching to B", async () => {
    const { mock, controller: a } = await loadedController();
    const late = deferred<Response>();
    mock.mockReturnValueOnce(late.promise);
    const pending = a.openDocument("doc-a");
    a.dispose();
    const b = new SourceReviewController("synthetic-b", 2);
    mock.mockResolvedValueOnce(response({ ...INDEX, case_id: "synthetic-b" }));
    await b.load();
    late.resolve(response(DOCUMENT));
    await pending;
    expect(a.getSnapshot().document).toBeNull();
    expect(b.getSnapshot().document).toBeNull();
  });

  it("discards old fingerprint document responses on source reload and clears attestation", async () => {
    const { mock, controller } = await loadedController();
    const late = deferred<Response>();
    mock.mockReturnValueOnce(late.promise);
    const opening = controller.openDocument("doc-a");
    const newer = { ...INDEX, source_fingerprint: "b".repeat(64) };
    mock.mockResolvedValueOnce(response(newer));
    await controller.load();
    late.resolve(response(DOCUMENT));
    await opening;
    expect(controller.getSnapshot()).toMatchObject({ index: newer, document: null, opened: false });
  });

  it("discards late documents when a newer document has been selected", async () => {
    const mock = fetchMock().mockResolvedValueOnce(response({
      ...INDEX, documents: [...INDEX.documents, { ...INDEX.documents[0], id: "doc-b" }],
    }));
    const controller = new SourceReviewController(INDEX.case_id, 2);
    await controller.load();
    const late = deferred<Response>();
    mock.mockReturnValueOnce(late.promise).mockResolvedValueOnce(response({ ...DOCUMENT, document: { ...DOCUMENT.document, id: "doc-b" } }));
    const opening = controller.openDocument("doc-a");
    await controller.openDocument("doc-b");
    late.resolve(response(DOCUMENT));
    await opening;
    expect(controller.getSnapshot().document?.document.id).toBe("doc-b");
  });

  it.each([
    { case_id: "another-case" },
    { ledger_version: 3 },
    { source_fingerprint: "b".repeat(64) },
    { document: { ...DOCUMENT.document, id: "another-document" } },
    { document: { ...DOCUMENT.document, parse_run: 2 } },
  ])("refuses a mismatched document envelope %j", async (change) => {
    const { mock, controller } = await loadedController();
    mock.mockResolvedValueOnce(response({ ...DOCUMENT, ...change }));
    await controller.openDocument("doc-a");
    expect(controller.getSnapshot()).toMatchObject({ phase: "error", index: null, document: null, opened: false, error: { status: 409 } });
  });

  it("requires explicit reload after stale source rejection, and can recover", async () => {
    const { mock, controller } = await loadedController();
    mock.mockResolvedValueOnce(new Response("", { status: 409 }));
    await controller.openDocument("doc-a");
    expect(controller.getSnapshot().phase).toBe("error");
    expect(mock).toHaveBeenCalledTimes(2);
    mock.mockResolvedValueOnce(response(INDEX));
    await controller.load();
    expect(controller.getSnapshot().phase).toBe("ready");
    expect(controller.getSnapshot().document).toBeNull();
  });

  it("does not show stale source classification for a different ledger version", async () => {
    fetchMock().mockResolvedValue(response({ ...INDEX, ledger_version: 3 }));
    const controller = new SourceReviewController(INDEX.case_id, 2);
    await controller.load();
    expect(controller.getSnapshot()).toMatchObject({ phase: "error", index: null, error: { status: 409 } });
  });

  it("makes failed document reads explicit without deleting the source inventory", async () => {
    const { mock, controller } = await loadedController();
    mock.mockRejectedValueOnce(new Error("offline"));
    await controller.openDocument("doc-a");
    expect(controller.getSnapshot()).toMatchObject({ phase: "ready", index: INDEX, documentPhase: "error", document: null, opened: false });
  });
});

describe("manual absence review", () => {
  it("requires a read source, attestation and a trimmed reason; preserves all ledger data", async () => {
    const { mock, controller } = await loadedController();
    const savedEntry = JSON.stringify(ENTRY);
    await controller.review(ENTRY, "absent", "Synthétique", true);
    expect(mock).toHaveBeenCalledTimes(1);
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    await controller.review(ENTRY, "absent", "Synthétique", false);
    await controller.review(ENTRY, "absent", " a ", true);
    expect(mock).toHaveBeenCalledTimes(2);
    mock.mockResolvedValueOnce(response({ ...INDEX, fields: [{ ...FIELD, state: "absent", reviewed: true, review_note: "Vérifié" }] }));
    await controller.review(ENTRY, "absent", "Vérifié", true);
    expect(mock).toHaveBeenCalledTimes(3);
    expect(controller.getSnapshot().index?.fields[0].state).toBe("absent");
    expect(JSON.stringify(ENTRY)).toBe(savedEntry);
    expect(mock.mock.calls.every(([url]) => !String(url).includes("/analysis") && !String(url).includes("/ledger/"))).toBe(true);
  });

  it("does not enable absence for an empty document or a document not yet opened for this field", async () => {
    const { mock, controller } = await loadedController();
    mock.mockResolvedValueOnce(response({ ...DOCUMENT, blocks: [] }));
    await controller.openDocument("doc-a");
    expect(controller.getSnapshot().opened).toBe(false);
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    controller.clearDocument();
    await controller.review(ENTRY, "absent", "Vérifié", true);
    expect(mock).toHaveBeenCalledTimes(3);
    expect(controller.getSnapshot().opened).toBe(false);
  });

  it("uses explicit server eligibility and denies missing flags instead of interpreting clinical values", () => {
    expect(canAttestAbsence(INDEX, FIELD)).toBe(true);
    expect(canAttestAbsence(INDEX, { ...FIELD, can_attest_absence: false })).toBe(false);
    const legacyField = { ...FIELD } as Partial<SourceField>;
    delete legacyField.can_attest_absence;
    expect(canAttestAbsence(INDEX, legacyField as SourceField)).toBe(false);
    expect(canAttestAbsence({ ...INDEX, ledger_version: null }, FIELD)).toBe(false);
  });

  it("allows the server-eligible retained literal unknown reading without changing or discarding it", async () => {
    const entry = {
      ...ENTRY, readings: [{
        value: "non renseigné", source_excerpt: "Champ synthétique : non renseigné",
        source_document_id: "doc-a", source_chunk_id: "block-a", fact_id: "fact-a",
      }],
    };
    const before = JSON.stringify(entry);
    const { mock, controller } = await loadedController();
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    const html = renderToStaticMarkup(createElement(SourceAttestation, {
      index: INDEX, entry, field: FIELD, state: controller.getSnapshot(), controller,
    }));
    expect(html).toContain("Confirmer l’absence après vérification");
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain('type="checkbox" disabled=""');
    mock.mockResolvedValueOnce(response({
      ...INDEX, fields: [{ ...FIELD, state: "absent", reviewed: true, review_note: "Sources vérifiées" }],
    }));
    await controller.review(entry, "absent", "Sources vérifiées", true);
    expect(mock).toHaveBeenCalledTimes(3);
    expect(controller.getSnapshot().index?.fields[0].state).toBe("absent");
    expect(JSON.stringify(entry)).toBe(before);
  });

  it.each(["documented", "in_report", "interpretation", "unverified"] as const)(
    "hides absence and prevents submission for %s when the server disallows it",
    async (state) => {
      const field = { ...FIELD, state, can_attest_absence: false };
      const entry = { ...ENTRY, value: state === "documented" ? "Valeur renseignée" : null };
      const index = { ...INDEX, fields: [field] };
      const mock = fetchMock().mockResolvedValueOnce(response(index));
      const controller = new SourceReviewController(INDEX.case_id, 2);
      await controller.load();
      mock.mockResolvedValueOnce(response(DOCUMENT));
      await controller.openDocument("doc-a");
      const html = renderToStaticMarkup(createElement(SourceAttestation, {
        index, entry, field, state: controller.getSnapshot(), controller,
      }));
      expect(html).toBe("");
      await controller.review(entry, "absent", "Sources vérifiées", true);
      expect(mock).toHaveBeenCalledTimes(2);
    },
  );

  it("prevents absence submission when an older response omits eligibility", async () => {
    const field = { ...FIELD } as Partial<SourceField>;
    delete field.can_attest_absence;
    const mock = fetchMock().mockResolvedValueOnce(response({ ...INDEX, fields: [field] }));
    const controller = new SourceReviewController(INDEX.case_id, 2);
    await controller.load();
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    await controller.review(ENTRY, "absent", "Sources vérifiées", true);
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it("never offers an absence mutation when a report documents the field, even with an empty ledger value", async () => {
    const mock = fetchMock().mockResolvedValueOnce(response({
      ...INDEX, fields: [{ ...FIELD, state: "in_report", can_attest_absence: false, links: [{ document_id: "doc-a", chunk_ids: ["block-a"], reason: "Passage nommé" }] }],
    }));
    const controller = new SourceReviewController(INDEX.case_id, 2);
    await controller.load();
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    await controller.review(ENTRY, "absent", "Motif synthétique", true);
    expect(mock).toHaveBeenCalledTimes(2);
    expect(controller.getSnapshot().index?.fields[0].state).toBe("in_report");
  });

  it("retracts an existing annotation without touching facts or requiring another absence attestation", async () => {
    const mock = fetchMock().mockResolvedValueOnce(response({ ...INDEX, fields: [{ ...FIELD, state: "absent", reviewed: true, review_note: "Motif" }] }));
    const controller = new SourceReviewController(INDEX.case_id, 2);
    await controller.load();
    mock.mockResolvedValueOnce(response(INDEX));
    await controller.review(ENTRY, "unverified", "À revérifier", false);
    expect(JSON.parse(String(mock.mock.calls[1][1]?.body)).status).toBe("unverified");
    expect(controller.getSnapshot().index?.fields[0].state).toBe("unverified");
  });

  it("drops old content on a review fingerprint conflict", async () => {
    const { mock, controller } = await loadedController();
    mock.mockResolvedValueOnce(response(DOCUMENT));
    await controller.openDocument("doc-a");
    mock.mockResolvedValueOnce(new Response("", { status: 409 }));
    await controller.review(ENTRY, "absent", "Vérifié", true);
    expect(controller.getSnapshot()).toMatchObject({ phase: "error", index: null, document: null, opened: false });
  });

  it("renders an unopened attestation disabled with an explicit explanation", () => {
    const controller = new SourceReviewController(INDEX.case_id, 2);
    const html = renderToStaticMarkup(createElement(SourceAttestation, {
      index: INDEX, entry: ENTRY, field: FIELD, state: controller.getSnapshot(), controller,
    }));
    expect(html).toContain("Ouvrez d’abord un document");
    expect(html).toContain('type="checkbox" disabled=""');
    expect(html).toContain("ne débloque pas l’analyse");
    expect(html).toContain('type="button" disabled=""');
  });
});

describe("readable source UI", () => {
  it.each(Object.keys(SOURCE_STATES) as SourceState[])("renders %s with text and icon, not color alone", (state) => {
    const html = renderToStaticMarkup(createElement(SourceBadge, { state }));
    expect(html).toContain(`source-${state}`);
    expect(html).toContain(SOURCE_STATES[state].label);
    expect(html).toContain('aria-hidden="true"');
    if (state === "documented" || state === "unverified") expect(html).not.toContain("source-absent");
  });

  it("keeps the server notice and clearly identifies unpinned historical sources", () => {
    const html = renderToStaticMarkup(createElement(SourceNotice, {
      index: { ...INDEX, scope: "current_parsed_unpinned", notice: "Notice synthétique du serveur" },
    }));
    expect(html).toContain("Notice synthétique du serveur");
    expect(html).toContain("version historique");
    expect(html).toContain("n’est pas attestée");
  });

  it("never equates unavailable sources with absence", () => {
    const html = renderToStaticMarkup(createElement(SourceNotice, { index: { ...INDEX, scope: "unavailable" } }));
    expect(html).toContain("Aucune absence ne peut en être déduite");
  });

  it("renders all blocks, including the end and qualifiers; HTML and literal searches stay safe", () => {
    const html = renderToStaticMarkup(createElement(SourceBlocks, {
      document: DOCUMENT, linkedIds: ["block-a"], query: "[.*",
    }));
    expect(html).toContain("Fin du compte rendu : réserve importante, sans conclusion automatique.");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("<mark>[.*</mark>");
    expect(html).toContain('data-source-anchor="true"');
    expect(html).toContain("Page 1 · Résumé");
    expect(html).toContain("Page 2");
    expect(html.match(/data-block-id=/g)).toHaveLength(2);
  });

  it("explains loading, errors and mandatory stale refresh without empty success", () => {
    const controller = new SourceReviewController(INDEX.case_id, 2);
    expect(renderToStaticMarkup(createElement(SourceStatus, { state: controller.getSnapshot(), onReload: vi.fn() }))).toContain("Chargement");
    const html = renderToStaticMarkup(createElement(SourceStatus, {
      state: { ...controller.getSnapshot(), phase: "error", error: new SourceError(409, "Sources modifiées") },
      onReload: vi.fn(),
    }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("Recharger le dossier et les sources");
  });

  it("keeps Discussion a pure placeholder without controls or network side effects", () => {
    const mock = fetchMock();
    const html = renderToStaticMarkup(createElement(DiscussionPlaceholder));
    expect(html).toContain("La discussion autour du PPS sera disponible ici. Aucun message n’est envoyé.");
    expect(html).not.toMatch(/<(form|input|textarea|button)\b/);
    expect(mock).not.toHaveBeenCalled();
  });

  it("meets AA contrast for all source badge text in both palettes", () => {
    const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
    const light = css.slice(0, css.indexOf("@media"));
    const dark = css.slice(css.indexOf("@media"), css.indexOf("* {"));
    function ratio(foreground: string, background: string) {
      const luminance = (hex: string) => {
        const channels = hex.match(/[a-f\d]{2}/gi)!.map((part) => parseInt(part, 16) / 255)
          .map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      };
      const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
      return (values[0] + 0.05) / (values[1] + 0.05);
    }
    for (const palette of [light, dark]) {
      const color = (name: string) => palette.match(new RegExp(`--${name}:\\s*(#[a-f\\d]{6})`, "i"))![1];
      for (const state of ["missing", "uncertain", "information"]) expect(ratio(color(state), color(`${state}-bg`))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(color("ink-2"), color("surface-2"))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(color("ink"), color("information-bg"))).toBeGreaterThanOrEqual(4.5);
    }
  });
});
