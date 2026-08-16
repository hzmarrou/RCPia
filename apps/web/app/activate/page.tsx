"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { activate } from "@/lib/auth";

/**
 * Read the invitation token from the URL fragment.
 *
 * Invitation links use `#token=…` rather than `?token=…`: a fragment is never sent to
 * any server, so the token stays out of web-server and proxy access logs. It is cleared
 * from the address bar as soon as it has been read.
 */
function useInvitationToken(queryFallback: string | null): string {
  const [token, setToken] = useState("");

  useEffect(() => {
    const fromFragment = new URLSearchParams(window.location.hash.slice(1)).get("token");
    const found = fromFragment ?? queryFallback ?? "";
    if (found) {
      setToken(found);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [queryFallback]);

  return token;
}

function ActivationForm() {
  const router = useRouter();
  // Older links may still carry ?token=; accept them, then scrub the address bar.
  const token = useInvitationToken(useSearchParams().get("token"));
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await activate(token, password, fullName || null);
      router.push("/dashboard");
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Activation impossible");
    } finally {
      setPending(false);
    }
  }

  if (!token) {
    return (
      <p className="error" role="alert">
        Lien d&apos;activation incomplet ou déjà utilisé dans cet onglet. Ouvrez à nouveau
        le lien reçu de votre administrateur.
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit}>
      <label htmlFor="full_name">Nom complet</label>
      <input
        id="full_name"
        name="full_name"
        type="text"
        autoComplete="name"
        value={fullName}
        onChange={(event) => setFullName(event.target.value)}
      />

      <label htmlFor="password">Mot de passe (12 caractères minimum)</label>
      <input
        id="password"
        name="password"
        type="password"
        autoComplete="new-password"
        minLength={12}
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      <button type="submit" disabled={pending}>
        {pending ? "Activation…" : "Activer mon compte"}
      </button>
    </form>
  );
}

export default function ActivatePage() {
  return (
    <section className="panel">
      <h1>Activer votre compte</h1>
      <p className="muted">
        Choisissez votre mot de passe pour terminer la création de votre compte RCPia.
      </p>
      <Suspense fallback={<p>Chargement…</p>}>
        <ActivationForm />
      </Suspense>
    </section>
  );
}
