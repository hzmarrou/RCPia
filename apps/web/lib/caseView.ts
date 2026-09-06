import type { CaseSummary } from "@/lib/cases";

export const CANCER_LABELS: Record<string, string> = { prostate: "Prostate", bladder: "Vessie" };

type CasePresentation = { label: string; tone: "review" | "wait" | "done" | "unknown" };

const STATUS: Record<string, CasePresentation> = {
  draft: { label: "Brouillon", tone: "unknown" },
  parsing: { label: "Préparation du dossier", tone: "wait" },
  ready_for_review: { label: "À vérifier", tone: "review" },
  analyzing: { label: "Analyse en cours", tone: "wait" },
  completed: { label: "Fiche prête", tone: "done" },
  blocked: { label: "Sans synthèse exploitable", tone: "review" },
};

export function casePresentation(status: string): CasePresentation {
  return STATUS[status] ?? { label: "État inconnu", tone: "unknown" };
}

// An explicit timezone gives server-rendered lists and hydrated rails the same dates.
const dayFormat = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
});
const shortDateFormat = new Intl.DateTimeFormat("fr-FR", {
  day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC",
});
const timeFormat = new Intl.DateTimeFormat("fr-FR", {
  hour: "2-digit", minute: "2-digit", timeZone: "UTC",
});

export function caseDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return {
    day: date.toISOString().slice(0, 10),
    label: dayFormat.format(date),
    short: shortDateFormat.format(date),
    time: timeFormat.format(date),
  };
}

export function groupCases(cases: CaseSummary[]) {
  const sorted = [...cases].sort((left, right) => {
    const a = Date.parse(left.created_at);
    const b = Date.parse(right.created_at);
    if (Number.isNaN(a)) return Number.isNaN(b) ? 0 : 1;
    if (Number.isNaN(b)) return -1;
    return b - a;
  });
  const groups = new Map<string, { label: string; cases: CaseSummary[] }>();
  for (const row of sorted) {
    const date = caseDate(row.created_at);
    const key = date?.day ?? "unknown";
    let group = groups.get(key);
    if (group === undefined) {
      group = { label: date?.label ?? "Date de dépôt inconnue", cases: [] };
      groups.set(key, group);
    }
    group.cases.push(row);
  }
  return [...groups].map(([key, group]) => ({ key, ...group }));
}
