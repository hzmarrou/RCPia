import type { JobActivity } from "@/lib/activity";
import type { CaseDetail } from "@/lib/cases";
import type { Ledger } from "@/lib/ledger";

/**
 * What each stage of the dossier's life may be said to be in.
 *
 * The backend records the panel lifecycle, including synthesis. Unknown is reserved for
 * lost contact or a status this client does not understand, not an ordinary finished panel.
 */
export type StageState = "done" | "now" | "pending" | "unknown" | "blocked";

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
  const finished = clinicalCase.status === "completed" && !analysing;
  const blocked = clinicalCase.status === "blocked" && !analysing;

  const statusKnown = ["draft", "parsing", "ready_for_review", "analyzing"].includes(
    clinicalCase.status,
  );
  const beyond: StageState = finished ? "done"
    : !known && extracted ? "unknown"
    : blocked ? "blocked"
    : statusKnown || !extracted ? "pending"
    : "unknown";

  return [
    uploaded ? "done" : "now",
    extracted ? "done" : uploaded ? "now" : "pending",
    analysing || finished || (known && blocked) ? "done" : extracted ? "now" : "pending",
    analysing ? "now" : beyond,
    beyond,
  ];
}
