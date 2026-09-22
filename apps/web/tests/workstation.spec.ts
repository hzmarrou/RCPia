import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import Workstation, { AnalysisFeedback } from "../app/poste/[caseId]/Workstation";
import LedgerTable from "../app/cases/[caseId]/ledger/LedgerTable";
import type { JobActivity } from "../lib/activity";
import type { CaseDetail } from "../lib/cases";
import type { Ledger, LedgerEntry } from "../lib/ledger";
import { caseSummary } from "./caseFixtures";
import { elements } from "./elements";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const CASE: CaseDetail = { ...caseSummary("inline-progress"), documents: [] };
const LEDGER: Ledger = {
  case_id: CASE.id, version: 1, entries: [], missing_fields: [], uncertain_fields: [],
  issues: [], can_run_analysis: true, updated_at: null,
};
const JOB: JobActivity = {
  id: "synthetic-job", job_type: "run_agent", status: "running",
  agent_role: "medical_oncology", milestone: "reasoning",
  milestone_at: null, steps: ["bundle", "reasoning", "checking", "recording"],
  started_at: null, created_at: "2026-09-07T12:00:00Z",
};

function workstation(status = CASE.status) {
  return renderToStaticMarkup(createElement(Workstation, {
    clinicalCase: { ...CASE, status }, ledger: LEDGER, cases: [CASE],
  }));
}

function feedback(overrides: Partial<Parameters<typeof AnalysisFeedback>[0]> = {}) {
  return createElement(AnalysisFeedback, {
    caseId: CASE.id, jobs: [], reachable: true, ficheReady: false,
    onRetry: vi.fn(), ...overrides,
  });
}

describe("analysis as a continuation of ledger review", () => {
  it("offers an accessible Discussion placeholder tab, not the previous disabled Questions tab", () => {
    const html = workstation();
    expect(html).not.toContain("Questions");
    expect(html).toMatch(/<button[^>]*id="poste-tab-discussion"[^>]*>Discussion/);
    expect(html).not.toMatch(/<button[^>]*id="poste-tab-discussion"[^>]*disabled/);
    expect(html).toContain('aria-controls="poste-panel"');
    expect(html).toContain('role="tabpanel"');
  });

  it.each([true, false])("keeps the original API launch gate %s on both clinical routes", (allowed) => {
    const entry: LedgerEntry = {
      key: "synthetic_field", label: "Champ synthétique", category: "imaging", value: null,
      status: "missing", origin: "machine", highlight: "red", unit: null, note: null,
      source_excerpt: null, source_document_id: null, source_chunk_id: null, fact_id: null,
      corrected: false, readings: [],
    };
    const ledger: Ledger = {
      ...LEDGER, entries: [entry], can_run_analysis: allowed,
      issues: [{ key: entry.key, label: entry.label, severity: "blocking", highlight: "red", message: "Contrôle synthétique bloquant" }],
    };
    const workstationHtml = renderToStaticMarkup(createElement(Workstation, { clinicalCase: CASE, ledger, cases: [CASE] }));
    const ordinaryHtml = renderToStaticMarkup(createElement(LedgerTable, { initial: ledger }));
    for (const html of [workstationHtml, ordinaryHtml]) {
      expect(html).toContain("Valeur structurée non renseignée");
      expect(html).toContain("Relecture des sources : chargement");
      expect(html).not.toMatch(/<tr class="(?:miss|highlight-red)"/);
      const button = html.match(/<button[^>]*>Lancer l&#x27;analyse<\/button>/)?.[0];
      expect(button).toBeDefined();
      expect(button?.includes('disabled=""')).toBe(!allowed);
    }
    expect(workstationHtml).toContain("Bloquante");
  });

  it("places the feedback region after the ledger and launch control", () => {
    const html = workstation();
    const ledger = html.indexOf("Données extraites du dossier");
    const launch = html.indexOf('aria-controls="poste-analysis-feedback"');
    const progress = html.indexOf('id="poste-analysis-feedback"');
    expect(ledger).toBeGreaterThan(-1);
    expect(launch).toBeGreaterThan(ledger);
    expect(progress).toBeGreaterThan(launch);
    expect(html).toContain('class="poste-pipeline"');
    expect(html).not.toContain("Voir la fiche RCP");
  });

  it.each(["ready_for_review", "analyzing", "blocked", "unrecognised"])(
    "does not offer a completed fiche for status %s just because the queue is empty",
    (status) => expect(workstation(status)).not.toContain("Voir la fiche RCP"),
  );

  it("offers the recorded fiche after the launch control without navigating automatically", () => {
    const html = workstation("completed");
    expect(html.indexOf("Voir la fiche RCP")).toBeGreaterThan(
      html.indexOf('aria-controls="poste-analysis-feedback"'),
    );
    expect(html).toContain(`href="/cases/${CASE.id}/pps"`);
    expect(html).toContain('role="status">La fiche RCP provisoire est disponible.');
  });

  it.each([
    ["medical_oncology", "Oncologie médicale"],
    ["rcp_synthesis", "RCP Synthesis"],
  ])(
    "renders reported specialist or synthesis progress in the feedback region: %s",
    (role, label) => {
      const html = renderToStaticMarkup(feedback({ jobs: [{ ...JOB, agent_role: role }] }));
      expect(html).toContain(label);
      expect(html).not.toContain(role);
      expect(html).toContain('id="poste-analysis-feedback"');
      expect(html).toContain('role="progressbar"');
      expect(html).not.toContain("aria-valuenow");
      expect(html).toContain("rédaction de l&#x27;avis");
      expect(html).not.toContain("Voir la fiche RCP");
    },
  );

  it("keeps stale progress and the retry action together without declaring success", () => {
    const onRetry = vi.fn();
    const props = {
      caseId: CASE.id, jobs: [JOB], reachable: false, ficheReady: false, onRetry,
    };
    const html = renderToStaticMarkup(createElement(AnalysisFeedback, props));
    expect(html).toContain("dernier état connu");
    expect(html).toContain('role="alert"');
    expect(html).not.toContain("Voir la fiche RCP");
    const retry = elements(AnalysisFeedback(props)).find(
      (node) => node.props.className === "poste-retry",
    );
    expect(retry?.props.onClick).toBe(onRetry);
  });
});
