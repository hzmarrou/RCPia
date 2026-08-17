import { API_BASE_URL } from "@/lib/api";

export type CancerType = "prostate" | "bladder";

export type CaseSummary = {
  id: string;
  reference: string;
  cancer_type: CancerType;
  clinical_question: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type CaseDocument = {
  id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  checksum_sha256: string;
  parse_status: "pending" | "parsing" | "parsed" | "failed";
  parser_provider: string | null;
  parse_warnings: string[];
  created_at: string;
};

export type CaseDetail = CaseSummary & { documents: CaseDocument[] };

export class CaseError extends Error {}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

export async function createCase(input: {
  reference: string;
  cancerType: CancerType;
  clinicalQuestion: string;
}): Promise<CaseSummary> {
  const response = await fetch(`${API_BASE_URL}/cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      reference: input.reference,
      cancer_type: input.cancerType,
      clinical_question: input.clinicalQuestion || null,
    }),
  });

  if (!response.ok) {
    throw new CaseError(await readError(response, "Création du dossier impossible"));
  }
  return (await response.json()) as CaseSummary;
}

export async function uploadDossier(caseId: string, file: File): Promise<CaseDocument> {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(`${API_BASE_URL}/cases/${caseId}/documents`, {
    method: "POST",
    credentials: "include",
    body,
  });

  if (!response.ok) {
    throw new CaseError(await readError(response, "Dépôt du dossier impossible"));
  }
  return (await response.json()) as CaseDocument;
}

/** Read a case from a server component, forwarding the session cookie. */
export async function fetchCase(
  baseUrl: string,
  cookieHeader: string,
  caseId: string,
): Promise<CaseDetail | null> {
  const response = await fetch(`${baseUrl}/cases/${caseId}`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  // 401 and 404 are both "you cannot see this"; the API deliberately does not
  // distinguish a missing case from another organization's case.
  if (response.status === 401 || response.status === 403 || response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new CaseError(`Unexpected status ${response.status} from /cases/${caseId}`);
  }
  return (await response.json()) as CaseDetail;
}

export async function fetchCases(
  baseUrl: string,
  cookieHeader: string,
): Promise<CaseSummary[] | null> {
  const response = await fetch(`${baseUrl}/cases`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  if (response.status === 401 || response.status === 403) {
    return null;
  }
  if (!response.ok) {
    throw new CaseError(`Unexpected status ${response.status} from /cases`);
  }
  return (await response.json()) as CaseSummary[];
}
