import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { fetchDashboardAccess } from "@/lib/auth";

// Server-side rendering runs inside the container, so it may need an internal URL.
const SERVER_API_BASE_URL = process.env.API_INTERNAL_BASE_URL ?? API_BASE_URL;

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

  return (
    <section className="panel">
      <h1>Tableau de bord</h1>
      <p>
        Connecté en tant que <strong>{user.full_name ?? user.email}</strong> —{" "}
        {ROLE_LABELS[user.role] ?? user.role}
      </p>
      <p className="muted">
        Le dépôt de dossiers et la génération de PPS provisoires seront disponibles ici.
      </p>
    </section>
  );
}
