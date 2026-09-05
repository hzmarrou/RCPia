import { describe, expect, it } from "vitest";

import { progressOf, type JobActivity } from "../lib/activity";

/**
 * The bar's one promise: it never announces a step the worker has not reported.
 *
 * A progress display that runs ahead of the work is worse than none, because the next one is
 * not believed either. These pin the promise, and the shape of the measured scale that makes
 * a seventy-second step look like seventy seconds of work.
 */

const AGENT_STEPS = ["bundle", "reasoning", "checking", "recording"];
const T0 = Date.parse("2026-09-05T18:00:00Z");

function job(over: Partial<JobActivity> = {}): JobActivity {
  return {
    id: "j1",
    job_type: "run_agent",
    status: "running",
    agent_role: "urology_oncology",
    milestone: "reasoning",
    milestone_at: new Date(T0).toISOString(),
    steps: AGENT_STEPS,
    started_at: new Date(T0).toISOString(),
    created_at: new Date(T0).toISOString(),
    ...over,
  };
}

describe("progressOf", () => {
  it("says nothing about a job that has reported nothing", () => {
    expect(progressOf(job({ milestone: null }), T0)).toBeNull();
  });

  it("ignores a milestone that is not on the job's own scale", () => {
    expect(progressOf(job({ milestone: "daydreaming" }), T0)).toBeNull();
  });

  it("starts a step at that step's share of the bar", () => {
    expect(progressOf(job(), T0)?.fraction).toBeCloseTo(0.09, 5);
    expect(progressOf(job({ milestone: "bundle" }), T0)?.fraction).toBeCloseTo(0, 5);
  });

  it("creeps on the clock without ever entering the next step", () => {
    const half = progressOf(job(), T0 + 35_000)?.fraction ?? 0;
    const late = progressOf(job(), T0 + 600_000)?.fraction ?? 0;
    expect(half).toBeGreaterThan(0.09);
    expect(half).toBeLessThan(late);
    // 0.99 is where `checking` starts, and only the worker may say it got there.
    expect(late).toBeLessThan(0.99);
  });

  it("moves visibly through the long step, where an even scale would not", () => {
    // The whole point: writing the opinion is ~90% of the wall clock, and on four equal
    // quarters every real run sat at 25% for a minute.
    const early = progressOf(job(), T0 + 5_000)?.fraction ?? 0;
    const later = progressOf(job(), T0 + 50_000)?.fraction ?? 0;
    expect(later - early).toBeGreaterThan(0.4);
  });

  it("says when a step has run longer than it usually does", () => {
    expect(progressOf(job(), T0 + 10_000)?.overrunning).toBe(false);
    expect(progressOf(job(), T0 + 200_000)?.overrunning).toBe(true);
  });

  it("falls back to an equal slice for a step with no measured share", () => {
    const invented = job({ milestone: "polishing", steps: [...AGENT_STEPS, "polishing"] });
    expect(progressOf(invented, T0)?.fraction).toBeCloseTo(0.8, 5);
  });

  it("holds at the step's own start when the worker sent no timestamp", () => {
    expect(progressOf(job({ milestone_at: null }), T0 + 60_000)?.fraction).toBeCloseTo(0.09, 5);
  });
});
