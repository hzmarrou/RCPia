import { describe, expect, it } from "vitest";

import type { JobActivity } from "../lib/activity";
import type { CaseDetail } from "../lib/cases";
import type { Ledger } from "../lib/ledger";
import { PIPELINE, pipelineOf } from "../lib/pipeline";

/**
 * What the pipeline may claim.
 *
 * Recorded lifecycle states establish completion; an empty queue alone never does.
 * Losing contact must still stop claims about work currently in flight.
 */

function medicalCase(over: Partial<CaseDetail> = {}): CaseDetail {
  return {
    id: "case-1",
    reference: "REF-1",
    cancer_type: "bladder",
    status: "ready_for_review",
    clinical_question: null,
    documents: [{ id: "doc-1" }],
    ...over,
  } as CaseDetail;
}

const LEDGER = { version: 1, entries: [], issues: [], can_run_analysis: true } as unknown as Ledger;

const RUNNING = [{ job_type: "run_agent" }] as unknown as JobActivity[];

const [DEPOT, LECTURE, VERIF, ANALYSE, FICHE] = [0, 1, 2, 3, 4];

describe("pipelineOf", () => {
  it("has a state for every stage shown", () => {
    expect(pipelineOf(medicalCase(), LEDGER, [])).toHaveLength(PIPELINE.length);
  });

  it("walks forward while the case is being prepared", () => {
    expect(pipelineOf(medicalCase({ documents: [] }), null, [])[DEPOT]).toBe("now");
    expect(pipelineOf(medicalCase(), null, [])[LECTURE]).toBe("now");
    expect(pipelineOf(medicalCase(), LEDGER, [])[VERIF]).toBe("now");
  });

  it("shows the post-review stages as pending before a panel is convened", () => {
    const stages = pipelineOf(medicalCase(), LEDGER, []);
    expect(stages[ANALYSE]).toBe("pending");
    expect(stages[FICHE]).toBe("pending");
  });

  it("says not-yet, not unknown, before there is anything to analyse", () => {
    const stages = pipelineOf(medicalCase({ documents: [] }), null, []);
    expect(stages[ANALYSE]).toBe("pending");
    expect(stages[FICHE]).toBe("pending");
  });

  it("reports the analysis as running while jobs are actually running", () => {
    const stages = pipelineOf(medicalCase(), LEDGER, RUNNING);
    expect(stages[VERIF]).toBe("done");
    expect(stages[ANALYSE]).toBe("now");
  });

  it("never marks the analysis done on a case status that does not say so", () => {
    for (const status of ["ready_for_review", "analyzing", "blocked"]) {
      expect(pipelineOf(medicalCase({ status }), LEDGER, [])[FICHE]).not.toBe("done");
    }
  });

  it("marks both final stages done after the backend records a usable synthesis", () => {
    const stages = pipelineOf(medicalCase({ status: "completed" }), LEDGER, []);
    expect(stages[ANALYSE]).toBe("done");
    expect(stages[FICHE]).toBe("done");
  });

  it("tracks a queued or retrying panel even before activity arrives", () => {
    const stages = pipelineOf(medicalCase({ status: "analyzing" }), LEDGER, []);
    expect(stages[VERIF]).toBe("done");
    expect(stages[ANALYSE]).toBe("now");
    expect(stages[FICHE]).toBe("pending");
  });

  it("shows a terminal synthesis failure instead of guessing or declaring completion", () => {
    const stages = pipelineOf(medicalCase({ status: "blocked" }), LEDGER, []);
    expect(stages[VERIF]).toBe("done");
    expect(stages[ANALYSE]).toBe("blocked");
    expect(stages[FICHE]).toBe("blocked");
  });

  it("does not call a newly running panel complete from a previous case snapshot", () => {
    const stages = pipelineOf(medicalCase({ status: "completed" }), LEDGER, RUNNING);
    expect(stages[ANALYSE]).toBe("now");
    expect(stages[FICHE]).not.toBe("done");
  });

  it("keeps unfamiliar backend statuses unknown", () => {
    const stages = pipelineOf(medicalCase({ status: "unrecognised" }), LEDGER, []);
    expect(stages[ANALYSE]).toBe("unknown");
    expect(stages[FICHE]).toBe("unknown");
  });
});

describe("pipelineOf when the server has gone quiet", () => {
  it("stops claiming an analysis is running", () => {
    // The jobs are a remembered picture once contact is lost. They may have finished minutes
    // ago, so "en cours" in the header would be a claim about the present made from the past.
    const stages = pipelineOf(medicalCase(), LEDGER, RUNNING, false);
    expect(stages[ANALYSE]).toBe("unknown");
    expect(stages[FICHE]).toBe("unknown");
  });

  it("does not walk the earlier stages back", () => {
    // Uploading and extraction are established by the case record, not by the poll, so a
    // silent server changes nothing about them.
    const stages = pipelineOf(medicalCase(), LEDGER, RUNNING, false);
    expect(stages[DEPOT]).toBe("done");
    expect(stages[LECTURE]).toBe("done");
    expect(stages[VERIF]).toBe("now");
  });

  it("still trusts a case the backend itself calls finished", () => {
    const stages = pipelineOf(medicalCase({ status: "completed" }), LEDGER, [], false);
    expect(stages[FICHE]).toBe("done");
  });

  it("does not trust a stale analyzing case status after losing contact", () => {
    const stages = pipelineOf(medicalCase({ status: "analyzing" }), LEDGER, [], false);
    expect(stages[ANALYSE]).toBe("unknown");
    expect(stages[FICHE]).toBe("unknown");
  });
});
