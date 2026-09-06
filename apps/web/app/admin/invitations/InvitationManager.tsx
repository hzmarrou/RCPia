"use client";

import { useState } from "react";

import {
  createInvitation, invitationLink, revokeInvitation, ROLE_LABELS,
  type InvitationSummary, type Role,
} from "@/lib/auth";
import { caseDate } from "@/lib/caseView";

export default function InvitationManager({
  initialInvitations,
}: {
  initialInvitations: InvitationSummary[];
}) {
  const [invitations, setInvitations] = useState(initialInvitations);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("uploader");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [created, setCreated] = useState<{ invitation: InvitationSummary; link: string } | null>(null);
  const [copyNotice, setCopyNotice] = useState<{ link: string; message: string } | null>(null);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("create");
    setError(null);
    setNotice(null);
    try {
      const { token, ...invitation } = await createInvitation(email, role);
      // The token exists only in this creation result; never persist it or put it in a query.
      setCreated({ invitation, link: invitationLink(window.location.origin, token) });
      setCopyNotice(null);
      setInvitations((current) => [invitation, ...current]);
      setEmail("");
      setNotice(`Invitation créée pour ${invitation.email}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Création de l'invitation impossible.");
    } finally {
      setBusy(null);
    }
  }

  async function revoke(invitation: InvitationSummary) {
    setBusy(invitation.id);
    setError(null);
    setNotice(null);
    try {
      await revokeInvitation(invitation.id);
      setInvitations((current) => current.filter((row) => row.id !== invitation.id));
      setCreated((current) => current?.invitation.id === invitation.id ? null : current);
      setNotice(`Invitation révoquée pour ${invitation.email}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Révocation de l'invitation impossible.");
    } finally {
      setBusy(null);
    }
  }

  async function copy(link: string) {
    try {
      if (!navigator.clipboard) {
        setCopyNotice({ link, message: "Copie automatique indisponible. Sélectionnez et copiez le lien ci-dessous." });
        return;
      }
      await navigator.clipboard.writeText(link);
      setCopyNotice({ link, message: "Lien copié." });
    } catch {
      setCopyNotice({ link, message: "Copie automatique impossible. Sélectionnez et copiez le lien ci-dessous." });
    }
  }

  return (
    <>
      <form onSubmit={create} className="invitation-form">
        <h2>Créer une invitation</h2>
        <label htmlFor="invitation-email">Adresse courriel</label>
        <input
          id="invitation-email" type="email" required autoComplete="off"
          value={email} onChange={(event) => setEmail(event.target.value)} disabled={busy !== null}
        />
        <label htmlFor="invitation-role">Rôle</label>
        <select
          id="invitation-role" value={role} disabled={busy !== null}
          onChange={(event) => {
            const selected = event.target.value;
            if (selected === "admin" || selected === "uploader" || selected === "viewer") setRole(selected);
            else setError("Sélectionnez un rôle valide.");
          }}
        >
          <option value="uploader">{ROLE_LABELS.uploader}</option>
          <option value="viewer">{ROLE_LABELS.viewer}</option>
          <option value="admin">{ROLE_LABELS.admin}</option>
        </select>
        <button type="submit" disabled={busy !== null}>
          {busy === "create" ? "Création…" : "Créer l'invitation"}
        </button>
      </form>

      {error ? <p className="error" role="alert">{error}</p> : null}
      {notice ? <p className="invitation-notice" role="status">{notice}</p> : null}

      {created ? (
        <section className="invitation-created" aria-label="Lien d'activation">
          <h2>Lien pour {created.invitation.email}</h2>
          <p>
            Ce lien n&apos;est affiché qu&apos;à la création. Copiez-le avant de créer une
            autre invitation ou de quitter cette page, puis transmettez-le uniquement à son destinataire.
          </p>
          <label htmlFor="invitation-link">Lien d&apos;activation</label>
          <input
            id="invitation-link" type="text" value={created.link} readOnly
            autoComplete="off" spellCheck={false} onFocus={(event) => event.target.select()}
          />
          <button type="button" onClick={() => copy(created.link)}>Copier le lien</button>
          {copyNotice?.link === created.link ? <p role="status">{copyNotice.message}</p> : null}
        </section>
      ) : null}

      <section className="invitation-list">
        <h2>Invitations en attente</h2>
        <p className="muted">
          Les invitations utilisées, révoquées ou déjà expirées ne figurent pas dans la
          liste chargée. Un lien perdu ne peut pas être retrouvé : révoquez l&apos;invitation
          et créez-en une nouvelle.
        </p>
        {invitations.length === 0 ? (
          <p className="case-empty">Aucune invitation en attente.</p>
        ) : (
          <ul>
            {invitations.map((invitation) => {
              const expires = caseDate(invitation.expires_at);
              return (
                <li key={invitation.id}>
                  <div className="invitation-details">
                    <strong>{invitation.email}</strong>
                    <span>{ROLE_LABELS[invitation.role]}</span>
                    {expires ? (
                      <time dateTime={invitation.expires_at}>
                        Expire le {expires.short} à {expires.time} UTC
                      </time>
                    ) : <span>Date d&apos;expiration inconnue</span>}
                  </div>
                  <button
                    type="button" className="invitation-revoke" disabled={busy !== null}
                    aria-label={`Révoquer l'invitation de ${invitation.email}`}
                    onClick={() => revoke(invitation)}
                  >
                    {busy === invitation.id ? "Révocation…" : "Révoquer"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
