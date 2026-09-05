import type { JobActivity } from "@/lib/activity";
import type { CaseDetail } from "@/lib/cases";
import type { Ledger } from "@/lib/ledger";

/**
 * What each stage of the dossier's life may be said to be in.
 *
 * `unknown` is the one that matters. The case record never advances past
 * `ready_for_review`: a panel can convene, run, and write four opinions, and the status the
 * API reports afterwards is the same one it reported before. So the last two stages are
 * knowable only while the work is visibly running — after that the page has no way to tell a
 * finished analysis from one that never happened, and saying "pas encore fait" would be a
 * claim nothing supports. It says it does not know instead.
 */
export type StageState = "done" | "now" | "pending" | "unknown";

export const PIPELINE = [
  "Dépôt",
  "Lecture du document",
  "Vérification par le clinicien",
  "Analyse par les spécialistes",
  "Fiche RCP",
];

/**
 * @param live  the jobs the last *successful* poll returned
 * @param known whether that poll is current. False after contact with the server is lost:
 *   the jobs on screen are then a remembered picture, and a remembered picture cannot say
 *   "an analysis is running now" — those jobs may have finished minutes ago. Everything the
 *   case record alone establishes still stands; only the claims that rested on live work
 *   fall back to unknown.
 */
export function pipelineOf(
  clinicalCase: CaseDetail,
  ledger: Ledger | null,
  live: JobActivity[],
  known = true,
): StageState[] {
  const uploaded = clinicalCase.documents.length > 0;
  const extracted = ledger !== null && ledger.version !== null;
  const analysing =
    known &&
    (live.some((job) => job.job_type === "run_agent") || clinicalCase.status === "analyzing");
  const finished = clinicalCase.status === "completed";

  // Only once the dossier is ready for analysis does "has it run?" become a question this
  // page cannot answer. Before that, not-yet is the honest answer.
  const beyond: StageState = finished ? "done" : extracted ? "unknown" : "pending";

  return [
    uploaded ? "done" : "now",
    extracted ? "done" : uploaded ? "now" : "pending",
    analysing || finished ? "done" : extracted ? "now" : "pending",
    analysing ? "now" : beyond,
    beyond,
  ];
}

