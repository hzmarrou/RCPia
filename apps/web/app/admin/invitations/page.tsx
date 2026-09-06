import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SERVER_API_BASE_URL } from "@/lib/api";
import { fetchInvitations } from "@/lib/auth";
import InvitationManager from "./InvitationManager";

export const dynamic = "force-dynamic";

export default async function InvitationsPage() {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore.getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
  const access = await fetchInvitations(SERVER_API_BASE_URL, cookieHeader);
  if (access.state === "unauthenticated") redirect("/login");
  if (access.state === "forbidden") {
    return (
      <section className="panel">
        <h1>Accès refusé</h1>
        <p className="error" role="alert">
          La gestion des invitations est réservée aux administrateurs de votre organisation.
        </p>
        <Link href="/dashboard">Retour au tableau de bord</Link>
      </section>
    );
  }
  return (
    <section className="invitations">
      <Link href="/dashboard">← Tableau de bord</Link>
      <p className="eyebrow">Administration</p>
      <h1>Invitations</h1>
      <p className="muted">
        Invitez un membre de votre organisation. Aucun courriel n&apos;est envoyé
        automatiquement : transmettez vous-même le lien d&apos;activation.
      </p>
      <InvitationManager initialInvitations={access.invitations} />
    </section>
  );
}
