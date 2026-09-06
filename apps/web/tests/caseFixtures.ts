import type { CaseSummary } from "../lib/cases";

export function caseSummary(id: string, overrides: Partial<CaseSummary> = {}): CaseSummary {
  return {
    id,
    reference: `DEMO-${id}`,
    cancer_type: "prostate",
    clinical_question: null,
    status: "ready_for_review",
    created_at: "2026-09-06T09:00:00Z",
    updated_at: "2026-09-06T11:00:00Z",
    ...overrides,
  };
}
