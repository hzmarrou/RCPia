import { API_BASE_URL } from "@/lib/api";

/**
 * What is still running on a case, and which step each piece is on.
 *
 * `milestone` is null until a handler reports one, and null means "not known" rather than
 * "at the start" — a queued job has not begun. `steps` is the ordered scale that milestone
 * belongs on for this kind of work, sent with the job so nothing here hard-codes the
 * sequence: a renamed or added step moves the scale and the value together.
 */
export type JobActivity = {
  id: string;
  job_type: string;
  status: "queued" | "running" | "waiting_for_user" | string;
  agent_role: string | null;
  milestone: string | null;
  milestone_at: string | null;
  steps: string[];
  started_at: string | null;
  created_at: string;
};

/**
 * The answer to one poll: what is running, or the fact that nobody could be asked.
 *
 * The distinction is the whole point of the type. An empty list used to stand for both "the
 * queue is empty" and "the request failed", and the two mean opposite things: the first says
 * the panel finished, the second says nothing at all. Conflated, one dropped request during
 * an analysis reads as a completed panel — the bars vanish, the case is declared settled and
 * the button offers to convene a second one while the first is still running.
 */
export type ActivitySnapshot = { ok: true; jobs: JobActivity[] } | { ok: false };

/**
 * Poll the work in flight.
 *
 * Never throws: this drives a progress display sitting beside the real page, and a network
 * blip should not replace the ledger a clinician is reading with an error. It reports the
 * failure instead, and the caller decides what to keep showing.
 */
export async function fetchActivity(caseId: string): Promise<ActivitySnapshot> {
  try {
    const response = await fetch(`${API_BASE_URL}/cases/${caseId}/activity`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!response.ok) return { ok: false };
    return { ok: true, jobs: (await response.json()) as JobActivity[] };
  } catch {
    return { ok: false };
  }
}

/**
 * How long each step usually takes, measured on real runs.
 *
 * Two runs on 4 Sep 2026 (61s, 75s), four more on 5 Sep (69-83s), in line with the 47-67s
 * seen on staging:
 *
 *     bundle     assembling the dossier, retrieval included   6.5-7s
 *     reasoning  writing the opinion                          60-76s
 *     checking   citations, then recording                    under 150ms each
 *
 * Used for one thing only: saying when a step has run longer than it usually does. **Not**
 * for placing a bar. A duration cannot be turned into progress — a provider that stalls the
 * instant reasoning begins produces exactly the same elapsed seconds as one working
 * normally, and a bar drawn from the clock would keep climbing through the stall, which is
 * the moment a clinician most needs to be told something is wrong.
 */
const TYPICAL_SECONDS: Record<string, number> = {
  bundle: 7,
  reasoning: 70,
  checking: 0.2,
  recording: 0.2,
};

export type Stage = {
  /** The step the worker last reported. */
  step: string;
  /** Seconds since it reported that step, or null when it sent no timestamp. */
  elapsedSeconds: number | null;
  /**
   * Whether this step has run longer than it usually does. Null for a step with no measured
   * duration: an unknown step has no "usually".
   */
  overrunning: boolean | null;
};

/**
 * What a job last reported, and how long ago — or null when it has not reported anything.
 *
 * **There is no percentage here, on purpose.** The worker reports four milestones and
 * nothing between them, so any position between two of them would be invented. The earlier
 * version of this file interpolated on the clock, and that bar climbed towards nine tenths
 * whether the agent was writing or hung. What is known is the step and how long it has been
 * running; that is what the caller may show.
 */
export function stageOf(job: JobActivity, now: number): Stage | null {
  if (job.milestone === null || job.steps.length === 0) return null;
  if (!job.steps.includes(job.milestone)) return null;

  const since = job.milestone_at === null ? NaN : Date.parse(job.milestone_at);
  const elapsedSeconds = Number.isNaN(since) ? null : Math.max(now - since, 0) / 1000;
  const typical = TYPICAL_SECONDS[job.milestone];

  return {
    step: job.milestone,
    elapsedSeconds,
    overrunning:
      typical === undefined || elapsedSeconds === null ? null : elapsedSeconds > typical,
  };
}

/** "1 min 12 s", the way a wait is read rather than counted. */
export function readableDuration(seconds: number): string {
  const whole = Math.floor(seconds);
  if (whole < 60) return `${whole} s`;
  return `${Math.floor(whole / 60)} min ${String(whole % 60).padStart(2, "0")} s`;
}

export const STEP_LABELS: Record<string, string> = {
  bundle: "dossier assemblé",
  reasoning: "rédaction de l'avis",
  checking: "vérification des citations",
  recording: "enregistrement de l'avis",
};

export const JOB_LABELS: Record<string, string> = {
  parse_dossier: "Lecture du document",
  extract_facts: "Extraction des données",
  run_agent: "Analyse",
  generate_pps: "Fiche RCP",
};
