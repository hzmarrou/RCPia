import Link from "next/link";

import { CANCER_LABELS, caseDate, casePresentation, groupCases } from "@/lib/caseView";
import type { CaseSummary } from "@/lib/cases";

export default function CaseList({
  cases,
  activeCaseId,
}: {
  cases: CaseSummary[];
  activeCaseId?: string;
}) {
  return (
    <div className="poste-caselist">
      {cases.length === 0 ? (
        <p className="case-empty">Aucun dossier pour le moment. Déposez votre premier dossier.</p>
      ) : groupCases(cases).map((group) => (
        <section key={group.key} className="case-day">
          <h2 className="poste-rail-label">{group.label}</h2>
          {group.cases.map((row) => {
            const status = casePresentation(row.status);
            const updated = caseDate(row.updated_at);
            return (
              <Link
                key={row.id}
                href={`/poste/${row.id}`}
                className="poste-caserow"
                aria-current={row.id === activeCaseId ? "page" : undefined}
              >
                <span className={`case-dot ${status.tone}`} aria-hidden="true" />
                <span className="case-identity">
                  <span className="ref">{row.reference}</span>
                  <span className="sub">
                    {CANCER_LABELS[row.cancer_type] ?? row.cancer_type} · {status.label}
                  </span>
                </span>
                {updated ? (
                  <time
                    className="when"
                    dateTime={row.updated_at}
                    title={`Mis à jour le ${updated.label} à ${updated.time} UTC`}
                    aria-label={`Mis à jour le ${updated.label} à ${updated.time} UTC`}
                  >
                    <span>{updated.short}</span>
                    <span>{updated.time} UTC</span>
                  </time>
                ) : <span className="when">Mise à jour inconnue</span>}
              </Link>
            );
          })}
        </section>
      ))}
    </div>
  );
}
