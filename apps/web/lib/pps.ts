import { API_BASE_URL } from "@/lib/api";

export type Answer = "oui" | "non" | "a_determiner";

export const AI_ORIGIN_TITLE =
  "FICHE RCP — CREATED BY AN AI AGENT — PROPOSITION DE PRISE EN CHARGE (PPS)";

export type ConciseFiche = {
  contract: "rcpia.pps-fiche.v1";
  decision: string;
  oncogenetics: string;
  oncogeriatrics: string;
  fertility: string;
  steps: { title: string; detail: string }[];
  alternatives: { rank: number; detail: string }[];
  precisions: string;
  palliative_care: string;
  treatment_status: string;
  support: { title: string; detail: string }[];
  carers: string;
  location: string;
  support_status: string;
  social: string;
  family: string;
  discharge: string;
  comment: string;
  references: string;
  clinical_trial: string;
  review: string;
  covered_option_ranks: number[];
};

export type InstitutionalFiche = Omit<
  ConciseFiche, "contract" | "steps" | "alternatives" | "support"
> & {
  contract: "rcpia.pps-fiche.v2";
  diagnosis: string;
  comorbidities: string;
  question: string;
  steps: { title: string; items: string[] }[];
  alternatives: { rank: number; title: string; items: string[] }[];
  support: { title: string; items: string[] }[];
  discharge_destination: "domicile" | "hospitalisation" | "autre";
  reference_scope: "national" | "international" | "non_documente";
};

export type ProposedAction = {
  action: string;
  timing: string;
  responsible: string;
  conditions: string[];
  evidence_ids: string[];
};

export type ProposedAnswer = {
  answer: "oui" | "non";
  rationale: string;
  conditions: string[];
  evidence_ids: string[];
};

export type PpsProposal = {
  contract: "rcpia.pps-proposal.v1";
  decision: string;
  intent: "traitement" | "bilan_prealable" | "surveillance" | "soins_de_support";
  rationale: string;
  evidence_ids: string[];
  referrals: {
    oncogenetics: ProposedAnswer;
    oncogeriatrics: ProposedAnswer;
    fertility: ProposedAnswer;
  };
  options: {
    rank: number;
    title: string;
    rationale: string;
    conditions: string[];
    steps: ProposedAction[];
    evidence_ids: string[];
  }[];
  palliative_care: ProposedAnswer;
  supportive_care: ProposedAction[];
  support_location: string;
  support_timing: string;
  social_plan: string;
  carers_plan: string;
  discharge_plan: string;
  clinical_trial: ProposedAnswer;
  follow_up: ProposedAction;
  missing_information: string[];
  uncertainties: string[];
};

export type Referral = {
  label: string;
  answer: Answer;
  justification: string;
  /** The recorded value the observation came from. Absent means RCPia had none. */
  basis: string | null;
  /** Only an approved rule may answer; nothing is approved yet. */
  approved: boolean;
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
  /** The threshold behind a triggered item, and that it is not clinically approved. */
  caveat: string | null;
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
  ledger_version: number | null;
  rationale: string;
  key_facts_used: string[];
  uncertainty_notes: string[];
  recommendation_withheld: boolean;
  /** What the agent wrote instead of a recommendation. Reasoning, never a plan. */
  withheld_statement: string | null;
};

export type Pps = {
  case_id: string;
  case_reference: string;
  cancer_type: string;
  generated_on: string;
  /** The version the lead agent read — not necessarily the newest. */
  ledger_version: number;
  /** Set when the case has moved on since the analysis. */
  superseded_by: number | null;
  agent_run_ids: string[];
  generator_version: string;
  banner: string;
  /** Absent on historical syntheses; never backfilled with invented decisions. */
  proposal?: PpsProposal | null;
  fiche?: ConciseFiche | InstitutionalFiche | null;
  synthesis: {
    patient: string;
    diagnosis: string | null;
    comorbidities: string | null;
    question: string;
  };
  decision: { taken: boolean; statement: string; referrals: Referral[] };
  treatments: {
    /** Empty when `withheld`: a declined recommendation carries no treatment content. */
    steps: TreatmentStep[];
    precisions: string[];
    contraindications: string[];
    missing_information: string[];
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
    clinical_trial: Answer;
    clinical_trial_note: string | null;
    /** Typed, not prose: a string saying "[X] Non … [X] Oui" claimed both. */
    validated: boolean;
    validated_note: string;
    sent_to_gp: boolean;
    sent_to_gp_note: string;
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

export function docxUrl(caseId: string): string {
  return `${API_BASE_URL}/cases/${caseId}/pps/docx`;
}

// The evidence register is a second file on purpose: the board reads the fiche, and the
// reasoning behind it travels separately.
export function probatoireUrl(caseId: string): string {
  return `${API_BASE_URL}/cases/${caseId}/pps/docx/probatoire`;
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
