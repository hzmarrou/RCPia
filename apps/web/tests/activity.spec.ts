import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchActivity,
  readableDuration,
  stageOf,
  type JobActivity,
} from "../lib/activity";

/**
 * The one promise: nothing is reported that the worker did not say.
 *
 * An earlier version of this file interpolated a percentage from the clock, so a bar climbed
 * towards nine tenths whether the agent was writing or hung, and an unmeasured step could
 * place itself *behind* the step before it. What a job knows is which milestone it last
 * reported and when; that is all that is exposed.
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

describe("stageOf", () => {
  it("says nothing about a job that has reported nothing", () => {
    expect(stageOf(job({ milestone: null }), T0)).toBeNull();
  });

  it("ignores a milestone that is not on the job's own scale", () => {
    expect(stageOf(job({ milestone: "daydreaming" }), T0)).toBeNull();
  });

  it("reports the step the worker last named", () => {
    expect(stageOf(job(), T0 + 5_000)?.step).toBe("reasoning");
  });

  it("counts from the milestone, not from the start of the job", () => {
    expect(stageOf(job(), T0 + 42_000)?.elapsedSeconds).toBe(42);
  });

  it("has no elapsed time when the worker sent no timestamp", () => {
    expect(stageOf(job({ milestone_at: null }), T0 + 42_000)?.elapsedSeconds).toBeNull();
  });

  it("never reports a negative wait when the clocks disagree", () => {
    expect(stageOf(job(), T0 - 9_000)?.elapsedSeconds).toBe(0);
  });

  it("says when a measured step has run longer than it usually does", () => {
    expect(stageOf(job(), T0 + 10_000)?.overrunning).toBe(false);
    expect(stageOf(job(), T0 + 200_000)?.overrunning).toBe(true);
  });

  it("does not warn during the observed staging checking window or ordinary poll lag", () => {
    const checking = job({ milestone: "checking" });
    expect(stageOf(checking, T0 + 562)?.overrunning).toBe(false);
    expect(stageOf(checking, T0 + 2_000)).toEqual({
      step: "checking", elapsedSeconds: 2, overrunning: false,
    });
  });

  it("warns only beyond the six-second checking observation budget", () => {
    const checking = job({ milestone: "checking" });
    expect(stageOf(checking, T0 + 6_000)?.overrunning).toBe(false);
    expect(stageOf(checking, T0 + 6_001)?.overrunning).toBe(true);
    expect(stageOf(checking, T0 + 60_000)).toEqual({
      step: "checking", elapsedSeconds: 60, overrunning: true,
    });
  });

  it.each([null, "not-a-time"])(
    "keeps checking duration and overrun unknown for timestamp %s", (milestone_at) => {
      expect(stageOf(job({ milestone: "checking", milestone_at }), T0 + 60_000)).toEqual({
        step: "checking", elapsedSeconds: null, overrunning: null,
      });
    },
  );

  it.each([["bundle", 7], ["reasoning", 70], ["recording", 0.2]] as const)(
    "preserves the existing %s warning threshold", (milestone, seconds) => {
      expect(stageOf(job({ milestone }), T0 + seconds * 1000)?.overrunning).toBe(false);
      expect(stageOf(job({ milestone }), T0 + seconds * 1000 + 1)?.overrunning).toBe(true);
    },
  );

  it("claims nothing about how long an unmeasured step should take", () => {
    // No measurement, no "usually" — and so no assertion either way.
    const invented = job({ milestone: "polishing", steps: [...AGENT_STEPS, "polishing"] });
    expect(stageOf(invented, T0 + 600_000)?.overrunning).toBeNull();
    expect(stageOf(invented, T0 + 600_000)?.step).toBe("polishing");
  });

  it("exposes no fraction at all", () => {
    // Load-bearing: the caller cannot draw a proportion it was never given, which is what
    // keeps a stalled provider from looking nine tenths done.
    expect(Object.keys(stageOf(job(), T0) ?? {}).sort()).toEqual([
      "elapsedSeconds",
      "overrunning",
      "step",
    ]);
  });
});

describe("readableDuration", () => {
  it("reads a wait rather than counting it", () => {
    expect(readableDuration(9.6)).toBe("9 s");
    expect(readableDuration(59)).toBe("59 s");
    expect(readableDuration(72)).toBe("1 min 12 s");
    expect(readableDuration(605)).toBe("10 min 05 s");
  });
});

describe("fetchActivity", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports the jobs when the server answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([job()]), { status: 200 })),
    );
    const snapshot = await fetchActivity("case-1");
    expect(snapshot.ok).toBe(true);
    expect(snapshot.ok && snapshot.jobs).toHaveLength(1);
  });

  it("distinguishes an empty queue from a failed request", async () => {
    // The distinction the caller depends on. Conflated as an empty list, one dropped request
    // during an analysis reads as a finished panel: bars gone, case declared settled, and the
    // button offering to convene a second panel over the first.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );
    expect(await fetchActivity("case-1")).toEqual({ ok: true, jobs: [] });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 503 })),
    );
    expect(await fetchActivity("case-1")).toEqual({ ok: false });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    expect(await fetchActivity("case-1")).toEqual({ ok: false });
  });

  it("never throws at the caller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{{{", { status: 200 })),
    );
    await expect(fetchActivity("case-1")).resolves.toEqual({ ok: false });
  });
});
