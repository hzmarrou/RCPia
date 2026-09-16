import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SERVER_API_BASE_URL } from "@/lib/api";
import { fetchDashboardAccess, ROLE_LABELS } from "@/lib/auth";
import { fetchCases } from "@/lib/cases";
import CaseList from "@/app/_components/CaseList";

export const dynamic = "force-dynamic";

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
          Pour obtenir un accès aux dossiers, contactez l&apos;administrateur RCPia de
          votre service.
        </p>
      </section>
    );
  }

  const { user } = access;
  const isAdmin = user.role === "admin";
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
        <div className="dashboard-actions">
          {isAdmin && <Link href="/admin/invitations">Invitations (administration)</Link>}
          <Link href="/cases/new" className="primary-link">Déposer un dossier</Link>
        </div>
      </div>
      <p className="muted">
        Connecté en tant que <strong>{user.full_name ?? user.email}</strong> —{" "}
        {ROLE_LABELS[user.role] ?? user.role}
      </p>
      <div className="dashboard-cases">
        <h2>{isAdmin ? "Dossiers de l’organisation" : "Vos dossiers"}</h2>
        <p className="muted">
          {isAdmin
            ? "Vous pouvez superviser tous les dossiers de votre organisation."
            : "Vous seul et les administrateurs de votre organisation pouvez accéder aux dossiers que vous créez."}
        </p>
        {cases.length === 0 ? (
          <section className="panel" aria-labelledby="welcome-title">
            <h3 id="welcome-title">Bienvenue dans votre espace RCPia</h3>
            <p>
              {isAdmin
                ? "Aucun dossier dans votre organisation pour le moment."
                : "Vous n’avez pas encore créé de dossier. Les dossiers des autres utilisateurs ne sont pas partagés avec vous."}
            </p>
            <ol>
              <li>Déposez un dossier simulé ou désidentifié dont l’utilisation est autorisée.</li>
              <li>Relisez les données extraites et corrigez-les à partir des sources.</li>
              <li>Lancez l’analyse après la revue, puis consultez la proposition provisoire.</li>
            </ol>
            <p>Aucun dossier d’exemple n’est ajouté automatiquement.</p>
            <Link href="/cases/new" className="primary-link">Déposer mon premier dossier</Link>
          </section>
        ) : (
          <>
            <p className="muted">Du plus récent au plus ancien. Chaque dossier ouvre son poste de travail.</p>
            <CaseList cases={cases} />
          </>
        )}
      </div>
    </section>
  );
}
