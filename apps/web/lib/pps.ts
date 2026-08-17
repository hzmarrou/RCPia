import { API_BASE_URL } from "@/lib/api";

export type Answer = "oui" | "non" | "a_determiner";

export type Referral = {
  label: string;
  answer: Answer;
  justification: string;
  /** The recorded value the proposal was derived from. Absent means RCPia had none. */
  basis: string | null;
};

export type TreatmentStep = {
  title: string;
  items: string[];
  evidence_ids: string[];
};

export type SupportItem = {
  title: string;
  detail: string;
  trigger: string | null;
};

export type MissingItem = {
  label: string;
  severity: "blocking" | "required" | "uncertain";
  highlight: "red" | "amber";
  message: string;
};

export type EvidenceEntry = {
  id: string;
  quote: string;
  supports: string | null;
  source_document_id: string | null;
  page: number | null;
};

export type RationaleAppendix = {
  agent_role: string;
  model: string | null;
  rationale: string;
  key_facts_used: string[];
  uncertainty_notes: string[];
  recommendation_withheld: boolean;
};

export type Pps = {
  case_id: string;
  case_reference: string;
  cancer_type: string;
  generated_on: string;
  ledger_version: number;
  agent_run_ids: string[];
  banner: string;
  synthesis: {
    patient: string;
    diagnosis: string | null;
    comorbidities: string | null;
    question: string;
  };
  decision: { taken: boolean; statement: string; referrals: Referral[] };
  treatments: {
    steps: TreatmentStep[];
    precisions: string[];
    contraindications: string[];
    rejected: TreatmentStep[];
    status: string;
    withheld: boolean;
  };
  support: {
    items: SupportItem[];
    carers: string | null;
    location: string | null;
    status: string;
  };
  social: {
    rows: { label: string; value: string | null; missing: boolean }[];
    comment: string | null;
  };
  framework: {
    reference_standard: string;
    clinical_trial: string;
    validated: string;
    sent_to_gp: string;
    participants: string;
    review: string;
  };
  missing: MissingItem[];
  evidence: EvidenceEntry[];
  appendices: RationaleAppendix[];
};

/** Distinguishes "no analysis yet" from a real failure, so the page can say which. */
export type PpsResult =
  | { kind: "ok"; pps: Pps }
  | { kind: "not-ready"; detail: string }
  | { kind: "forbidden" };

export async function fetchPps(
  baseUrl: string,
  cookieHeader: string,
  caseId: string,
): Promise<PpsResult> {
  const response = await fetch(`${baseUrl}/cases/${caseId}/pps`, {
    headers: { cookie: cookieHeader },
    cache: "no-store",
  });

  if ([401, 403, 404].includes(response.status)) {
    return { kind: "forbidden" };
  }
  if (response.status === 409 || response.status === 422) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    return {
      kind: "not-ready",
      detail:
        typeof body.detail === "string"
          ? body.detail
          : "Le dossier n'est pas prêt pour une proposition.",
    };
  }
  if (!response.ok) {
    throw new Error(`Unexpected status ${response.status} from the PPS endpoint`);
  }
  return { kind: "ok", pps: (await response.json()) as Pps };
}

export function downloadUrl(caseId: string): string {
  return `${API_BASE_URL}/cases/${caseId}/pps/markdown`;
}

export const ANSWER_LABELS: Record<Answer, string> = {
  oui: "Oui",
  non: "Non",
  a_determiner: "À déterminer",
};

export const AGENT_LABELS: Record<string, string> = {
  urology_oncology: "Oncologie urologique",
  medical_oncology: "Oncologie médicale",
  radiation_oncology: "Radiothérapie",
  pathology: "Anatomopathologie",
  radiology: "Radiologie",
};
