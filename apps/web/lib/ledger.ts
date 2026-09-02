import { API_BASE_URL } from "@/lib/api";

export type Highlight = "red" | "amber" | "neutral";

export type LedgerReading = {
  value: string | null;
  source_excerpt: string | null;
  source_document_id: string | null;
  source_chunk_id: string | null;
  fact_id: string | null;
};

export type LedgerEntry = {
  key: string;
  label: string;
  category: string;
  value: string | null;
  status: string;
  origin: string;
  highlight: Highlight;
  unit: string | null;
  note: string | null;
  source_excerpt: string | null;
  source_document_id: string | null;
  source_chunk_id: string | null;
  fact_id: string | null;
  corrected: boolean;
  /** Several when the dossier contradicts itself; each with the text it came from. */
  readings: LedgerReading[];
};

export type CompletenessIssue = {
  key: string;
  label: string;
  severity: "blocking" | "required" | "uncertain";
  highlight: Highlight;
  message: string;
};

export type Ledger = {
  case_id: string;
  version: number | null;
  entries: LedgerEntry[];
  missing_fields: string[];
  uncertain_fields: string[];
  issues: CompletenessIssue[];
  can_run_analysis: boolean;
  updated_at: string | null;
};

export class LedgerError extends Error {}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

/** Read the ledger from a server component, forwarding the session cookie. */
export async function fetchLedger(
  baseUrl: string,
  cookieHeader: string,
  caseId: string,
): Promise<Ledger | null> {
  const response = await fetch(`${baseUrl}/cases/${caseId}/ledger`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  if ([401, 403, 404].includes(response.status)) {
    return null;
  }
  if (!response.ok) {
    throw new LedgerError(`Unexpected status ${response.status} from the ledger`);
  }
  return (await response.json()) as Ledger;
}

export async function correctField(
  caseId: string,
  input: { factType: string; value: string | null; note: string },
): Promise<Ledger> {
  const response = await fetch(`${API_BASE_URL}/cases/${caseId}/ledger/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      fact_type: input.factType,
      value: input.value,
      note: input.note,
    }),
  });

  if (!response.ok) {
    throw new LedgerError(await readError(response, "Correction impossible"));
  }
  return (await response.json()) as Ledger;
}

export const CATEGORY_LABELS: Record<string, string> = {
  identity: "Identité",
  social: "Situation sociale",
  history: "Antécédents",
  presentation: "Histoire de la maladie",
  performance: "État général et scores",
  examination: "Examen clinique",
  imaging: "Imagerie",
  biology: "Biologie",
  pathology: "Anatomopathologie",
  decision: "Décisions",
};

/** The three specialities a tumour board convenes for a urological case. */
export const PANEL_ROLES = [
  "urology_oncology",
  "medical_oncology",
  "radiation_oncology",
] as const;

/**
 * Convene the panel on the ledger as it now stands.
 *
 * **Called by a person, deliberately.** The ledger page tells the clinician to check and
 * correct the extraction before any analysis, and promises the agents will only see that
 * version. Convening automatically the moment extraction finished broke that promise: the
 * agents read the machine's first draft, and the review the page asked for happened after
 * the opinions already existed. The deployment therefore leaves `AUTO_PANEL_ROLES` empty
 * and this call is what starts the specialists.
 */
export async function convenePanel(caseId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/cases/${caseId}/analysis/panel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ roles: PANEL_ROLES }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail.slice(0, 200) || `L'analyse n'a pas pu être lancée (${response.status})`,
    );
  }
}
