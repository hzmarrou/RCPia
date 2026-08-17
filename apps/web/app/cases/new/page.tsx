"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createCase, uploadDossier, type CancerType } from "@/lib/cases";

export default function NewCasePage() {
  const router = useRouter();
  const [reference, setReference] = useState("");
  const [cancerType, setCancerType] = useState<CancerType>("prostate");
  const [clinicalQuestion, setClinicalQuestion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      const created = await createCase({ reference, cancerType, clinicalQuestion });
      if (file) {
        await uploadDossier(created.id, file);
      }
      router.push(`/cases/${created.id}`);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Création impossible");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel">
      <h1>Nouveau dossier</h1>
      <p className="muted">
        Utilisez le numéro de dossier de la RCP comme référence. N&apos;inscrivez aucune
        donnée nominative : RCPia identifie les patients par ce numéro uniquement.
      </p>

      <form onSubmit={onSubmit}>
        <label htmlFor="reference">Référence du dossier</label>
        <input
          id="reference"
          name="reference"
          type="text"
          required
          maxLength={100}
          placeholder="Dossier 2"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
        />

        <label htmlFor="cancer_type">Localisation</label>
        <select
          id="cancer_type"
          name="cancer_type"
          value={cancerType}
          onChange={(event) => setCancerType(event.target.value as CancerType)}
        >
          <option value="prostate">Prostate</option>
          <option value="bladder">Vessie</option>
        </select>

        <label htmlFor="clinical_question">Question posée</label>
        <textarea
          id="clinical_question"
          name="clinical_question"
          rows={3}
          maxLength={4000}
          placeholder="Stratégie thérapeutique optimale pour…"
          value={clinicalQuestion}
          onChange={(event) => setClinicalQuestion(event.target.value)}
        />

        <label htmlFor="file">Dossier clinique (.docx ou .pdf)</label>
        <input
          id="file"
          name="file"
          type="file"
          accept=".pdf,.docx"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />

        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}

        <button type="submit" disabled={pending}>
          {pending ? "Envoi…" : "Créer le dossier"}
        </button>
      </form>
    </section>
  );
}
