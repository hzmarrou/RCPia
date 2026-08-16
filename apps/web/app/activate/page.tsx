"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { activate } from "@/lib/auth";

function ActivationForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
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
        Lien d&apos;activation incomplet. Utilisez le lien reçu de votre administrateur.
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
