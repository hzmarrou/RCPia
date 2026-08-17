import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { fetchCase, type CaseDocument } from "@/lib/cases";

const SERVER_API_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? API_BASE_URL;

export const dynamic = "force-dynamic";

const CANCER_LABELS: Record<string, string> = {
  prostate: "Prostate",
  bladder: "Vessie",
};

const CASE_STATUS_LABELS: Record<string, string> = {
  draft: "Brouillon",
  parsing: "Extraction en cours",
  ready_for_review: "À vérifier",
  analyzing: "Analyse en cours",
  completed: "Terminé",
  blocked: "Bloqué",
};

const PARSE_STATUS_LABELS: Record<string, string> = {
  pending: "En attente",
  parsing: "Extraction en cours",
  parsed: "Extrait",
  failed: "Échec",
};

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(Math.round(bytes / 1024), 1)} Ko`
    : `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

function DocumentRow({ document }: { document: CaseDocument }) {
  return (
    <li>
      <strong>{document.original_filename}</strong>
      <span className="muted">
        {" "}
        — {formatSize(document.size_bytes)} ·{" "}
        {PARSE_STATUS_LABELS[document.parse_status] ?? document.parse_status}
        {document.parser_provider ? ` · ${document.parser_provider}` : ""}
      </span>
      {document.parse_warnings.length > 0 ? (
        <ul className="warnings">
          {document.parse_warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export default async function CasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  const clinicalCase = await fetchCase(SERVER_API_BASE_URL, cookieHeader, caseId);
  if (clinicalCase === null) {
    // The API does not distinguish a missing case from one belonging to another
    // organization, and neither does this page.
    notFound();
  }

  return (
    <section>
      <h1>{clinicalCase.reference}</h1>
      <dl>
        <dt>Localisation</dt>
        <dd>{CANCER_LABELS[clinicalCase.cancer_type] ?? clinicalCase.cancer_type}</dd>
        <dt>Statut</dt>
        <dd>{CASE_STATUS_LABELS[clinicalCase.status] ?? clinicalCase.status}</dd>
        <dt>Question posée</dt>
        <dd>{clinicalCase.clinical_question ?? "—"}</dd>
      </dl>

      <h2>Documents déposés</h2>
      {clinicalCase.documents.length === 0 ? (
        <p className="muted">Aucun document déposé pour ce dossier.</p>
      ) : (
        <ul>
          {clinicalCase.documents.map((document) => (
            <DocumentRow key={document.id} document={document} />
          ))}
        </ul>
      )}

      <p className="muted">
        L&apos;extraction des données et la génération d&apos;une proposition provisoire
        seront disponibles ici. Toute sortie de RCPia est provisoire et doit être validée
        en RCP.
      </p>
    </section>
  );
}
