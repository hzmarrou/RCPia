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
 * How far through its scale a job is, as a fraction, or null when it has not said.
 *
 * **Never invents a step it has not reached.** A job that has reported nothing returns null
 * so the caller can show an empty track, and a job on step two of four returns the *start*
 * of step two, not the end. The creep between steps is the caller's business; this only
 * reports what was actually claimed.
 */
export function reachedFraction(job: JobActivity): number | null {
  if (job.milestone === null || job.steps.length === 0) return null;
  const index = job.steps.indexOf(job.milestone);
  if (index < 0) return null;
  return index / job.steps.length;
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
