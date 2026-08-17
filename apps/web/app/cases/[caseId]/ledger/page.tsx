import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { fetchLedger } from "@/lib/ledger";
import LedgerTable from "./LedgerTable";

const SERVER_API_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? API_BASE_URL;

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
        Données extraites automatiquement du dossier déposé. En rouge : information
        manquante. En orange : donnée incertaine ou contradictoire. Vérifiez et corrigez
        avant toute analyse — les agents ne verront que cette version.
        {ledger.version ? ` Version ${ledger.version}.` : ""}
      </p>

      <LedgerTable initial={ledger} />
    </>
  );
}
