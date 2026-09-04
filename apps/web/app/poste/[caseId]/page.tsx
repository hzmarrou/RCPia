import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { fetchCase, fetchCases } from "@/lib/cases";
import { fetchLedger } from "@/lib/ledger";
import Workstation from "./Workstation";

const SERVER_API_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? API_BASE_URL;

export const dynamic = "force-dynamic";

/**
 * The three-pane workstation: the case list stays on the left, the dossier is worked in the
 * centre, and correcting a value or reading the fiche happens on the right without leaving
 * either. It exists beside the plain pages rather than replacing them — the same case is
 * still readable at `/cases/[caseId]`, and nothing here is load-bearing yet.
 */
export default async function PostePage({
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

  // The rail and the centre come from two different endpoints and neither waits on the
  // other, so they are fetched together rather than in sequence.
  const [clinicalCase, ledger, cases] = await Promise.all([
    fetchCase(SERVER_API_BASE_URL, cookieHeader, caseId),
    fetchLedger(SERVER_API_BASE_URL, cookieHeader, caseId),
    fetchCases(SERVER_API_BASE_URL, cookieHeader),
  ]);

  if (clinicalCase === null) {
    // The API does not distinguish a missing case from another organization's, and neither
    // does this page.
    notFound();
  }

  return (
    <Workstation
      clinicalCase={clinicalCase}
      ledger={ledger}
      cases={cases ?? [clinicalCase]}
    />
  );
}
