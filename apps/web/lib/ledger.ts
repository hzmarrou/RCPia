import { API_BASE_URL } from "@/lib/api";

export type Highlight = "red" | "amber" | "neutral";

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
