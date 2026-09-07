import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import Workstation, { AnalysisFeedback } from "../app/poste/[caseId]/Workstation";
import type { JobActivity } from "../lib/activity";
import type { CaseDetail } from "../lib/cases";
import type { Ledger } from "../lib/ledger";
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

  it.each(["medical_oncology", "rcp_synthesis"])(
    "renders reported specialist or synthesis progress in the feedback region: %s",
    (role) => {
      const html = renderToStaticMarkup(feedback({ jobs: [{ ...JOB, agent_role: role }] }));
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
