"use client";

import { useState } from "react";

import {
  CATEGORY_LABELS,
  correctField,
  type Ledger,
  type LedgerEntry,
} from "@/lib/ledger";

function groupByCategory(entries: LedgerEntry[]): [string, LedgerEntry[]][] {
  const groups = new Map<string, LedgerEntry[]>();
  for (const entry of entries) {
    const existing = groups.get(entry.category);
    if (existing) {
      existing.push(entry);
    } else {
      groups.set(entry.category, [entry]);
    }
  }
  return [...groups.entries()];
}

function EntryRow({
  entry,
  onCorrect,
}: {
  entry: LedgerEntry;
  onCorrect: (entry: LedgerEntry) => void;
}) {
  return (
    <tr className={`highlight-${entry.highlight}`}>
      <th scope="row">
        {entry.label}
        {entry.unit ? <span className="muted"> ({entry.unit})</span> : null}
      </th>
      <td>
        {entry.readings.length > 1 ? (
          // The dossier says more than one thing. Showing only the first value would
          // hide precisely what the clinician has to settle.
          <>
            <strong>Valeurs contradictoires :</strong>
            <ul>
              {entry.readings.map((reading) => (
                <li key={reading.fact_id ?? reading.value}>
                  {reading.value}
                  {reading.source_excerpt ? (
                    <blockquote>{reading.source_excerpt}</blockquote>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            {entry.value ?? <span className="missing-value">Information manquante</span>}
            {entry.corrected ? <span className="badge">corrigé</span> : null}
            {entry.note ? <p className="muted">{entry.note}</p> : null}
            {entry.source_excerpt ? (
              <details>
                <summary className="muted">Source</summary>
                <blockquote>{entry.source_excerpt}</blockquote>
              </details>
            ) : null}
          </>
        )}
      </td>
      <td>
        <button type="button" onClick={() => onCorrect(entry)}>
          Corriger
        </button>
      </td>
    </tr>
  );
}

export default function LedgerTable({ initial }: { initial: Ledger }) {
  const [ledger, setLedger] = useState(initial);
  const [editing, setEditing] = useState<LedgerEntry | null>(null);
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function startCorrection(entry: LedgerEntry) {
    setEditing(entry);
    setValue(entry.value ?? "");
    setNote("");
    setError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setError(null);
    setPending(true);
    try {
      const updated = await correctField(ledger.case_id, {
        factType: editing.key,
        value: value.trim() === "" ? null : value,
        note,
      });
      setLedger(updated);
      setEditing(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Correction impossible");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <section aria-labelledby="completeness">
        <h2 id="completeness">Complétude</h2>
        {ledger.issues.length === 0 ? (
          <p className="muted">Toutes les données requises sont présentes.</p>
        ) : (
          <ul>
            {ledger.issues.map((issue) => (
              <li key={`${issue.key}-${issue.severity}`} className={`highlight-${issue.highlight}`}>
                {issue.message}
              </li>
            ))}
          </ul>
        )}
        {!ledger.can_run_analysis ? (
          <p className="error" role="alert">
            L&apos;analyse ne peut pas être lancée tant que les données bloquantes
            ci-dessus ne sont pas renseignées.
          </p>
        ) : null}
      </section>

      {editing ? (
        <form onSubmit={submit} className="panel">
          <h2>Corriger : {editing.label}</h2>
          <label htmlFor="value">Valeur</label>
          <input
            id="value"
            type="text"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Laisser vide si la donnée est réellement inconnue"
          />

          <label htmlFor="note">Motif de la correction</label>
          <textarea
            id="note"
            rows={2}
            required
            minLength={3}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Ex. : virgule mal lue par l'extraction"
          />

          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}

          <div className="actions">
            <button type="submit" disabled={pending}>
              {pending ? "Enregistrement…" : "Enregistrer la correction"}
            </button>
            <button type="button" onClick={() => setEditing(null)}>
              Annuler
            </button>
          </div>
          <p className="muted">
            La valeur extraite automatiquement est conservée : la correction s&apos;ajoute
            à l&apos;historique du dossier.
          </p>
        </form>
      ) : null}

      {groupByCategory(ledger.entries).map(([category, entries]) => (
        <section key={category}>
          <h2>{CATEGORY_LABELS[category] ?? category}</h2>
          <table className="ledger">
            <tbody>
              {entries.map((entry) => (
                <EntryRow key={entry.key} entry={entry} onCorrect={startCorrection} />
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}
