import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { SERVER_API_BASE_URL } from "@/lib/api";
import { fetchLedger } from "@/lib/ledger";
import LedgerTable from "./LedgerTable";


export const dynamic = "force-dynamic";

export default async function LedgerPage({
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

  const ledger = await fetchLedger(SERVER_API_BASE_URL, cookieHeader, caseId);
  if (ledger === null) {
    notFound();
  }

  return (
    <>
      <h1>Données du dossier</h1>
      <p className="muted">
        Données extraites automatiquement du dossier déposé. Les repères de relecture
        des sources sont distincts des contrôles bloquant l’analyse. Vérifiez et corrigez
        avant toute analyse — les agents ne verront que cette version.
        {ledger.version ? ` Version ${ledger.version}.` : ""}
      </p>

      <LedgerTable key={`${ledger.case_id}:${ledger.version}:${ledger.updated_at}`} initial={ledger} />
    </>
  );
}
