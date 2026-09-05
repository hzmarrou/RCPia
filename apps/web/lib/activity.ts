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
 * Poll the work in flight.
 *
 * Returns an empty list rather than throwing when the request fails. This drives a progress
 * display that sits beside the real page: a network blip should make the bars go quiet, not
 * replace the ledger a clinician is reading with an error.
 */
export async function fetchActivity(caseId: string): Promise<JobActivity[]> {
  try {
    const response = await fetch(`${API_BASE_URL}/cases/${caseId}/activity`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!response.ok) return [];
    return (await response.json()) as JobActivity[];
  } catch {
    return [];
  }
}

/**
 * Where each reported step sits on the bar, and how long it usually lasts.
 *
 * **Measured, not assumed** — two runs on 4 Sep 2026 (61s and 75s), four more on 5 Sep
 * (69-83s), in line with the 47-67s seen on staging:
 *
 *     bundle     assembling the dossier, retrieval included   ~9%    6.5-7s
 *     reasoning  writing the opinion                          ~89%   60-76s
 *     checking   citations, then recording                    <0.5%  under 150ms each
 *
 * Drawing those four steps as equal quarters is a picture of something that does not happen:
 * it parked every real bar at 25% for a minute, which is what a run watched end to end on
 * 5 Sep showed. One step doing nine tenths of the work is not a tidy diagram, but it is the
 * work.
 */
type Stage = { at: number; to: number; typicalSeconds: number };

const AGENT_STAGES: Record<string, Stage> = {
  bundle: { at: 0, to: 0.09, typicalSeconds: 7 },
  reasoning: { at: 0.09, to: 0.99, typicalSeconds: 70 },
  checking: { at: 0.99, to: 0.996, typicalSeconds: 0.2 },
  recording: { at: 0.996, to: 1, typicalSeconds: 0.2 },
};

// How far into a step the bar may creep before the next milestone arrives. Short of the
// boundary on purpose: the bar may say "well into writing the opinion", never "done writing
// it" — that claim belongs to the worker, which has not made it yet.
const CREEP = 0.9;

export type Progress = {
  /** 0..1. Never reaches the start of a step the job has not reported. */
  fraction: number;
  /** This step has already run longer than it usually does. */
  overrunning: boolean;
};

/**
 * How far along a job is, or null when it has not said.
 *
 * **Never claims a step the worker has not reported.** A job that has reported nothing
 * returns null so the caller can show an empty track. Within the step it *has* reported the
 * bar creeps on the clock towards, but never into, the next one — because the alternative,
 * a bar that sits still for a minute, is read as a hung run.
 *
 * The scale comes from the job: `steps` is sent with it, so a renamed or added step moves
 * the scale and the value together. A step with no measured share falls back to an equal
 * slice of the scale rather than disappearing.
 */
export function progressOf(job: JobActivity, now: number): Progress | null {
  if (job.milestone === null || job.steps.length === 0) return null;
  const index = job.steps.indexOf(job.milestone);
  if (index < 0) return null;

  const measured = AGENT_STAGES[job.milestone];
  const stage: Stage = measured ?? {
    at: index / job.steps.length,
    to: (index + 1) / job.steps.length,
    typicalSeconds: 30,
  };

  const since = job.milestone_at === null ? NaN : Date.parse(job.milestone_at);
  const elapsed = Number.isNaN(since) ? 0 : Math.max(now - since, 0) / 1000;
  const share = Math.min(elapsed / stage.typicalSeconds, 1);

  return {
    fraction: stage.at + (stage.to - stage.at) * CREEP * share,
    overrunning: elapsed > stage.typicalSeconds,
  };
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
