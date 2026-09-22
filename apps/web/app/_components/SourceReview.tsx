"use client";

import {
  createContext, useContext, useEffect, useRef, useState, useSyncExternalStore,
  type ReactNode,
} from "react";

import type { LedgerEntry } from "@/lib/ledger";
import {
  canAttestAbsence, originalDocumentUrl, SOURCE_STATES, SourceReviewController,
  type SourceDocument, type SourceField, type SourceIndex, type SourceSnapshot, type SourceState,
} from "@/lib/sources";

type ReviewContext = {
  state: SourceSnapshot;
  controller: SourceReviewController;
  open: (entry?: LedgerEntry) => void;
};
const Context = createContext<ReviewContext | null>(null);

function useSources() {
  const context = useContext(Context);
  if (!context) throw new Error("SourceReviewProvider is required");
  return context;
}

export function SourceReviewProvider({
  caseId, ledgerVersion, onCorrect, children,
}: {
  caseId: string;
  ledgerVersion: number | null;
  onCorrect: (entry: LedgerEntry) => boolean | void;
  children: ReactNode;
}) {
  const [controller] = useState(() => new SourceReviewController(caseId, ledgerVersion));
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  const [selection, setSelection] = useState<{ entry?: LedgerEntry } | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    void controller.load();
    return () => controller.dispose();
  }, [controller]);
  useEffect(() => {
    if (selection) dialog.current?.showModal();
  }, [selection]);

  function close() {
    dialog.current?.close();
    setSelection(null);
    controller.clearDocument();
    trigger.current?.focus();
  }
  function open(entry?: LedgerEntry) {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    controller.clearDocument();
    setSelection({ entry });
    const index = state.index;
    const linked = index?.fields.find((field) => field.key === entry?.key)?.links[0]?.document_id;
    const first = linked ?? index?.documents[0]?.id;
    if (first) void controller.openDocument(first);
  }
  return (
    <Context.Provider value={{ state, controller, open }}>
      {children}
      {selection ? (
        <dialog
          ref={dialog}
          className="source-dialog"
          aria-labelledby="source-dialog-title"
          onCancel={(event) => { event.preventDefault(); close(); }}
        >
          <header className="source-dialog-header">
            <div>
              <span className="source-eyebrow">Retour aux documents</span>
              <h2 id="source-dialog-title">{selection.entry?.label ?? "Les sources du dossier"}</h2>
            </div>
            <button type="button" onClick={close} aria-label="Fermer les sources">Fermer ×</button>
          </header>
          <SourceReader
            key={`${selection.entry?.key ?? "documents"}:${state.index?.source_fingerprint ?? "loading"}`}
            state={state}
            entry={selection.entry}
            controller={controller}
            onCorrect={() => {
              if (selection.entry && onCorrect(selection.entry) !== false) close();
            }}
          />
        </dialog>
      ) : null}
    </Context.Provider>
  );
}

export function SourceBadge({ state }: { state: SourceState }) {
  const description = SOURCE_STATES[state];
  return (
    <span className={`source-badge source-${state}`}>
      <span aria-hidden="true">{description.icon}</span> {description.label}
    </span>
  );
}

export function SourceFieldReview({ entry }: { entry: LedgerEntry }) {
  const { state, open } = useSources();
  const field = state.index?.fields.find((item) => item.key === entry.key);
  return (
    <div className="source-field-review">
      {field ? <SourceBadge state={field.state} /> : (
        <span className="source-pending">
          {state.phase === "loading" ? "Relecture des sources : chargement…" : "Relecture des sources indisponible"}
        </span>
      )}
      <button type="button" className="source-link" onClick={() => open(entry)} disabled={state.phase !== "ready"}>
        {field?.links.length ? "Voir le compte rendu complet" : "Consulter les documents"}
      </button>
    </div>
  );
}

export function SourceStatus({ state, onReload }: { state: SourceSnapshot; onReload: () => void }) {
  if (state.phase === "loading") return <p role="status">Chargement des repères et des documents sources…</p>;
  if (state.phase === "error") return (
    <div className="error" role="alert">
      <p>{state.error?.message}</p>
      <button type="button" onClick={state.error?.status === 409 ? () => window.location.reload() : onReload}>
        {state.error?.status === 409 ? "Recharger le dossier et les sources" : "Réessayer"}
      </button>
    </div>
  );
  return null;
}

export function SourceNotice({ index }: { index: SourceIndex }) {
  return (
    <>
      {index.notice ? <p className="source-notice" role="note">{index.notice}</p> : null}
      {index.scope === "current_parsed_unpinned" ? (
        <p className="source-notice">Documents actuellement analysés : leur correspondance avec cette version historique du dossier n’est pas attestée.</p>
      ) : index.scope === "unavailable" ? (
        <p className="source-notice">Les sources de cette version ne sont pas disponibles. Aucune absence ne peut en être déduite.</p>
      ) : null}
    </>
  );
}

export function SourceReviewSummary() {
  const { state, controller, open } = useSources();
  return (
    <section className="source-summary" aria-label="Repères de relecture des sources">
      <div className="source-summary-title">
        <strong>Du champ au compte rendu</strong>
        <button type="button" onClick={() => open()} disabled={state.phase !== "ready"}>Consulter les documents</button>
      </div>
      <p>Ces repères décrivent la relecture des données, pas leur gravité ni l’autorisation de lancer l’analyse.</p>
      <ul className="source-legend" aria-label="Légende des repères">
        {(["absent", "in_report", "interpretation", "unverified"] as const).map((state) => (
          <li key={state}><SourceBadge state={state} /></li>
        ))}
      </ul>
      <SourceStatus state={state} onReload={() => void controller.load()} />
      {state.index ? (
        <>
          <SourceNotice index={state.index} />
          <p className="source-inventory">{state.index.documents.length} document(s) disponible(s) dans cet index. Les champs vides ne sont pas automatiquement déclarés absents.</p>
          <button type="button" className="source-link" onClick={() => void controller.load()}>Actualiser les sources</button>
        </>
      ) : null}
    </section>
  );
}

function SourceReader({
  state, entry, controller, onCorrect,
}: {
  state: SourceSnapshot;
  entry?: LedgerEntry;
  controller: SourceReviewController;
  onCorrect: () => void;
}) {
  const [query, setQuery] = useState("");
  const [match, setMatch] = useState(-1);
  const textRegion = useRef<HTMLDivElement>(null);
  const index = state.index;
  const field = index?.fields.find((item) => item.key === entry?.key);
  const selectedDocument = index?.documents.find((item) => item.id === state.documentId);
  const linked = field?.links.find((link) => link.document_id === state.documentId);
  const matching = state.document?.blocks.filter((block) => query.trim() && block.text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) ?? [];
  useEffect(() => {
    if (state.documentPhase === "ready") {
      textRegion.current?.querySelector<HTMLElement>("[data-source-anchor=true]")?.scrollIntoView({ block: "nearest" });
    }
  }, [state.documentPhase, state.documentId]);
  function jump(position: number) {
    const next = (position + matching.length) % matching.length;
    setMatch(next);
    const target = matching[next];
    if (target) {
      const block = [...(textRegion.current?.querySelectorAll<HTMLElement>("[data-block-id]") ?? [])].find((node) => node.dataset.blockId === target.id);
      block?.scrollIntoView({ block: "center" });
      block?.focus({ preventScroll: true });
    }
  }
  return (
    <div className="source-reader">
      <SourceStatus state={state} onReload={() => void controller.load()} />
      {index ? (
        <>
          {entry ? (
            <section className="source-field-summary" aria-label="Champ en cours de relecture">
              {field ? <SourceBadge state={field.state} /> : null}
              <details open>
                <summary>Valeur actuelle et origine</summary>
                <p className="source-current-value">{entry.value ?? "Aucune valeur renseignée dans ce champ."}</p>
                {entry.readings.length > 1 ? <ul>{entry.readings.map((reading, i) => <li key={i}>{reading.value ?? "Valeur non renseignée"}{reading.source_excerpt ? <blockquote>{reading.source_excerpt}</blockquote> : null}</li>)}</ul> : null}
                <p>{entry.corrected ? "Correction manuelle" : entry.origin === "machine" ? "Extraction automatique" : "Origine enregistrée dans le dossier"}</p>
                {entry.note ? <p>Motif de correction : {entry.note}</p> : null}
              </details>
              {field?.explanation ? <p>{field.explanation}</p> : null}
              <button type="button" onClick={onCorrect} disabled={state.saving}>Corriger ce champ</button>
            </section>
          ) : null}
          <SourceNotice index={index} />
          <div className="source-document-picker">
            <label htmlFor="source-document">Document à lire</label>
            <select id="source-document" value={state.documentId ?? ""} disabled={state.saving} onChange={(event) => { setQuery(""); setMatch(-1); void controller.openDocument(event.target.value); }}>
              <option value="" disabled>Choisir un document</option>
              {index.documents.map((document) => <option key={document.id} value={document.id}>{document.filename}{document.coverage === "unavailable" ? " — texte indisponible" : ""}</option>)}
            </select>
          </div>
          {!index.documents.length ? <p>Aucun document consultable dans cet index. Cela ne signifie pas que le champ est absent du dossier.</p> : null}
          {selectedDocument ? (
            <div className="source-document-meta">
              <h3>{selectedDocument.filename}</h3>
              <p>Déposé le <time dateTime={selectedDocument.uploaded_at}>{formatDeposit(selectedDocument.uploaded_at)}</time></p>
              <a href={originalDocumentUrl(index.case_id, selectedDocument.id)} target="_blank" rel="noreferrer">Ouvrir / télécharger le document original <span className="sr-only">(nouvel onglet)</span></a>
              {selectedDocument.coverage === "unavailable" ? <p>Le texte analysé n’est pas disponible pour ce document. Vous pouvez consulter l’original ; aucune absence ne peut être déduite de ce défaut de lecture.</p> : null}
            </div>
          ) : null}
          {state.documentPhase === "loading" ? <p role="status">Ouverture du document complet…</p> : null}
          {state.documentError ? (
            <div className="error" role="alert">
              <p>{state.documentError.message}</p>
              <button type="button" onClick={() => { if (state.documentId) void controller.openDocument(state.documentId); }}>Réessayer la lecture</button>
            </div>
          ) : null}
          {state.document ? (
            <>
              <div className="source-document-meta">
                <p>Texte intégral disponible après analyse du document. Consultez l’original pour sa mise en page et les éléments non textuels.</p>
                {linked ? <p className="source-linked-note">↗ Passages reliés à ce champ : {linked.reason}</p> : <p>Aucun passage relié à ce champ dans ce document. Sa présence n’est pas présumée.</p>}
              </div>
              <div className="source-search" role="search" aria-label="Rechercher dans le document complet">
                <label htmlFor="source-search">Rechercher dans le texte</label>
                <input id="source-search" type="search" value={query} onChange={(event) => { setQuery(event.target.value); setMatch(-1); }} placeholder="Un mot, une expression…" />
                {query.trim() ? (
                  <div className="source-search-actions">
                    <span role="status">{matching.length} passage(s) trouvé(s) · Le texte reste intégral.</span>
                    <button type="button" disabled={!matching.length} onClick={() => jump(match < 0 ? matching.length - 1 : match - 1)}>Précédent</button>
                    <button type="button" disabled={!matching.length} onClick={() => jump(match + 1)}>Suivant</button>
                    <button type="button" onClick={() => { setQuery(""); setMatch(-1); }}>Effacer la recherche</button>
                  </div>
                ) : null}
              </div>
              <div ref={textRegion}>
                <SourceBlocks document={state.document} linkedIds={linked?.chunk_ids ?? []} query={query.trim()} />
              </div>
              <p className="source-end">Fin du texte disponible · {state.document.blocks.length} bloc(s)</p>
              <details className="source-technical">
                <summary>Détails techniques de cette lecture</summary>
                <p>Version du dossier : {index.ledger_version ?? "non définie"} · Analyse du document : {state.document.document.parse_run}</p>
                <p>Empreinte des sources : <code>{index.source_fingerprint}</code></p>
              </details>
            </>
          ) : null}
          {entry && field ? (
            <SourceAttestation key={`${field.state}:${field.review_note ?? ""}`} index={index} entry={entry} field={field} state={state} controller={controller} />
          ) : null}
          {state.message ? <p className="source-notice" role="status">{state.message}</p> : null}
        </>
      ) : null}
    </div>
  );
}

export function SourceBlocks({ document, linkedIds, query = "" }: { document: SourceDocument; linkedIds: string[]; query?: string }) {
  return (
    <article className="source-text" aria-label="Texte complet du document">
      {!document.blocks.length ? <p>Le texte de ce document n’est pas disponible. Aucune absence ne peut en être déduite.</p> : null}
      {document.blocks.map((block, index) => (
        <section key={`${block.id}:${index}`} tabIndex={-1} data-block-id={block.id} data-source-anchor={linkedIds.includes(block.id)} className={linkedIds.includes(block.id) ? "source-block source-block-linked" : "source-block"}>
          <header className="source-block-location">
            <span>{block.page !== null ? `Page ${block.page}` : "Page non repérée"}{block.section ? ` · ${block.section}` : ""}</span>
            {linkedIds.includes(block.id) ? <strong>↗ Passage relié au champ</strong> : null}
          </header>
          <p>{highlightText(block.text, query)}</p>
        </section>
      ))}
    </article>
  );
}

export function highlightText(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLocaleLowerCase();
  const needle = query.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let found = lower.indexOf(needle);
  while (found !== -1) {
    parts.push(text.slice(cursor, found), <mark key={found}>{text.slice(found, found + query.length)}</mark>);
    cursor = found + query.length;
    found = lower.indexOf(needle, cursor);
  }
  parts.push(text.slice(cursor));
  return parts;
}

function formatDeposit(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "date indisponible" : new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
  }).format(date) + " UTC";
}

export function SourceAttestation({ index, entry, field, state, controller }: {
  index: SourceIndex; entry: LedgerEntry; field: SourceField; state: SourceSnapshot; controller: SourceReviewController;
}) {
  const [checked, setChecked] = useState(false);
  const [note, setNote] = useState("");
  const eligible = canAttestAbsence(index, field);
  if (!eligible && !field.reviewed) return null;
  return (
    <section className="source-attestation" aria-label="Annotation de relecture">
      <h3>Votre repère de relecture</h3>
      <p>Cette annotation ne renseigne pas la valeur et ne débloque pas l’analyse. Elle ne remplace pas une correction.</p>
      {field.reviewed ? <p>Annotation enregistrée : {field.review_note}</p> : null}
      {eligible && field.state !== "absent" ? (
        <>
          {!state.opened ? <p>Ouvrez d’abord un document dont le texte est disponible dans ce lecteur avant d’attester votre vérification.</p> : null}
          <label className="source-check">
            <input type="checkbox" checked={checked} disabled={!state.opened || state.saving} onChange={(event) => setChecked(event.target.checked)} />
            J’ai vérifié les sources disponibles pour ce champ
          </label>
        </>
      ) : null}
      <label htmlFor="source-review-note">Motif de votre annotation (au moins 3 caractères)</label>
      <textarea id="source-review-note" rows={3} value={note} disabled={state.saving} onChange={(event) => setNote(event.target.value)} />
      <div className="source-review-actions">
        {eligible && field.state !== "absent" ? (
          <button type="button" disabled={!state.opened || !checked || note.trim().length < 3 || state.saving} onClick={() => void controller.review(entry, "absent", note, checked)}>
            {state.saving ? "Enregistrement…" : "Confirmer l’absence après vérification"}
          </button>
        ) : null}
        {field.reviewed ? (
          <button type="button" disabled={index.ledger_version === null || note.trim().length < 3 || state.saving} onClick={() => void controller.review(entry, "unverified", note, false)}>
            Retirer l’annotation de relecture
          </button>
        ) : null}
      </div>
    </section>
  );
}
