import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SERVER_API_BASE_URL } from "@/lib/api";
import { fetchDashboardAccess } from "@/lib/auth";
import { fetchCases } from "@/lib/cases";
import CaseList from "@/app/_components/CaseList";

export const dynamic = "force-dynamic";

const ROLE_LABELS: Record<string, string> = {
  admin: "Administrateur",
  uploader: "Déposant",
  viewer: "Lecteur",
};

export default async function DashboardPage() {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  // Authorization is decided by the API, not re-implemented here.
  const access = await fetchDashboardAccess(SERVER_API_BASE_URL, cookieHeader);

  if (access.state === "unauthenticated") {
    redirect("/login");
  }

  if (access.state === "forbidden") {
    return (
      <section className="panel">
        <h1>Accès refusé</h1>
        <p className="error" role="alert">
          Votre compte n&apos;a pas les droits nécessaires pour déposer des dossiers.
        </p>
        <p className="muted">
          Les comptes en lecture seule peuvent consulter les résultats partagés avec eux.
          Pour obtenir des droits de dépôt, contactez l&apos;administrateur RCPia de votre
          service.
        </p>
      </section>
    );
  }

  const { user } = access;
  const cases = await fetchCases(SERVER_API_BASE_URL, cookieHeader);
  if (cases === null) {
    return (
      <section className="panel">
        <h1>Dossiers indisponibles</h1>
        <p className="error" role="alert">
          Votre session ne permet plus de consulter les dossiers.
        </p>
        <Link href="/login">Se reconnecter</Link>
      </section>
    );
  }

  return (
    <section className="dashboard">
      <div className="dashboard-head">
        <div>
          <p className="eyebrow">Poste RCPia</p>
          <h1>Tableau de bord</h1>
        </div>
        <Link href="/cases/new" className="primary-link">Déposer un dossier</Link>
      </div>
      <p className="muted">
        Connecté en tant que <strong>{user.full_name ?? user.email}</strong> —{" "}
        {ROLE_LABELS[user.role] ?? user.role}
      </p>
      <div className="dashboard-cases">
        <h2>Vos dossiers</h2>
        <p className="muted">Du plus récent au plus ancien. Chaque dossier ouvre son poste de travail.</p>
        <CaseList cases={cases} />
      </div>
    </section>
  );
}
