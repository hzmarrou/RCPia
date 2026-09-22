import { describe, expect, it } from "vitest";

import QueuedWorkNotice from "../app/_components/QueuedWorkNotice";
import type { JobActivity } from "../lib/activity";
import { text } from "./elements";

function job(overrides: Partial<JobActivity> = {}): JobActivity {
  return {
    id: "synthetic-job", job_type: "parse_dossier", status: "queued",
    agent_role: null, milestone: null, milestone_at: null, steps: [],
    started_at: null, created_at: "2026-09-21T08:00:00Z", ...overrides,
  };
}

describe("queued work is not presented as active processing", () => {
  it("explains a never-started upload and avoids encouraging duplicate uploads", () => {
    const result = text(QueuedWorkNotice({ jobs: [job()], reachable: true }));
    expect(result).toContain("En attente de démarrage");
    expect(result).toContain("Lecture du document");
    expect(result).toContain("pas une durée de traitement");
    expect(result).toContain("une seconde fois");
    expect(result).not.toContain("connexion Internet");
  });

  it("distinguishes a retry from work that never began", () => {
    const result = text(QueuedWorkNotice({
      jobs: [job({ started_at: "2026-09-21T08:01:00Z" })], reachable: true,
    }));
    expect(result).toContain("En attente d’une nouvelle tentative");
    expect(result).not.toContain("En attente de démarrage");
  });

  it.each(["running", "waiting_for_user"])("does not claim queue-only state for %s", (status) => {
    expect(QueuedWorkNotice({ jobs: [job(), job({ status })], reachable: true })).toBeNull();
  });

  it("does not claim current queue state from a stale server snapshot", () => {
    expect(QueuedWorkNotice({ jobs: [job()], reachable: false })).toBeNull();
    expect(QueuedWorkNotice({ jobs: [], reachable: true })).toBeNull();
  });
});
