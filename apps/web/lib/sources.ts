import { API_BASE_URL } from "@/lib/api";
import type { LedgerEntry } from "@/lib/ledger";

export type SourceState = "documented" | "absent" | "in_report" | "interpretation" | "unverified";
export type SourceField = {
  key: string;
  state: SourceState;
  label: string;
  explanation: string;
  links: { document_id: string; chunk_ids: string[]; reason: string }[];
  reviewed: boolean;
  review_note: string | null;
  can_attest_absence: boolean;
};
export type SourceIndex = {
  case_id: string;
  ledger_version: number | null;
  source_fingerprint: string;
  scope: "pinned_snapshot" | "current_parsed_unpinned" | "unavailable";
  notice: string | null;
  documents: {
    id: string;
    filename: string;
    parse_run: number;
    uploaded_at: string;
    pages: number[];
    chunks: number;
    coverage: "complete_parsed_source" | "unavailable";
  }[];
  fields: SourceField[];
};
export type SourceDocument = Pick<SourceIndex, "case_id" | "ledger_version" | "source_fingerprint"> & {
  document: { id: string; filename: string; parse_run: number; uploaded_at: string };
  blocks: { id: string; ordinal: number; text: string; page: number | null; section: string | null; block_type: string }[];
};

export const SOURCE_STATES: Record<SourceState, { label: string; icon: string }> = {
  absent: { label: "Information absente du dossier", icon: "−" },
  in_report: { label: "Information présente dans un compte rendu, à préciser", icon: "↗" },
  interpretation: { label: "Interprétation à confirmer", icon: "≈" },
  documented: { label: "Information renseignée", icon: "✓" },
  unverified: { label: "Présence dans les sources à vérifier", icon: "?" },
};

export class SourceError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function sourceRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init, credentials: "include", cache: "no-store",
    });
  } catch {
    throw new SourceError(0, "Impossible de joindre les sources. Vérifiez la connexion puis réessayez.");
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Votre session a expiré. Reconnectez-vous pour consulter les sources.",
      403: "Vous n’avez pas accès aux sources de ce dossier.",
      404: "Ces sources sont introuvables ou ne vous sont pas accessibles.",
      409: "Le dossier ou ses sources ont changé. Rechargez avant de poursuivre la lecture.",
      422: "Cette annotation n’est pas autorisée pour ce champ. Rechargez les sources et vérifiez votre motif.",
    };
    throw new SourceError(response.status, messages[response.status] ?? "La lecture des sources a échoué. Réessayez.");
  }
  try {
    return await response.json() as T;
  } catch {
    throw new SourceError(0, "La réponse des sources est illisible. Réessayez.");
  }
}

export function fetchSources(caseId: string): Promise<SourceIndex> {
  return sourceRequest(`/cases/${encodeURIComponent(caseId)}/sources`);
}

export function fetchSourceDocument(index: SourceIndex, documentId: string): Promise<SourceDocument> {
  const query = new URLSearchParams({ source_fingerprint: index.source_fingerprint });
  if (index.ledger_version !== null) query.set("ledger_version", String(index.ledger_version));
  return sourceRequest(`/cases/${encodeURIComponent(index.case_id)}/sources/${encodeURIComponent(documentId)}?${query}`);
}

export function annotateSource(index: SourceIndex, fieldKey: string, status: "absent" | "unverified", note: string): Promise<SourceIndex> {
  return sourceRequest(`/cases/${encodeURIComponent(index.case_id)}/sources/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_ledger_version: index.ledger_version,
      source_fingerprint: index.source_fingerprint,
      field_key: fieldKey, status, note: note.trim(),
    }),
  });
}

export function originalDocumentUrl(caseId: string, documentId: string): string {
  return `${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentId)}/content`;
}

/** Eligibility belongs to the server, including retained literal unknown readings. */
export function canAttestAbsence(index: SourceIndex, field: SourceField): boolean {
  return index.ledger_version !== null && field.can_attest_absence === true;
}

export type SourceSnapshot = {
  phase: "loading" | "ready" | "error";
  index: SourceIndex | null;
  error: SourceError | null;
  documentPhase: "idle" | "loading" | "ready" | "error";
  document: SourceDocument | null;
  documentId: string | null;
  documentError: SourceError | null;
  opened: boolean;
  saving: boolean;
  message: string | null;
};
const INITIAL: SourceSnapshot = {
  phase: "loading", index: null, error: null,
  documentPhase: "idle", document: null, documentId: null, documentError: null,
  opened: false, saving: false, message: null,
};

/** Request generations protect even transports that complete after a case switch or reload. */
export class SourceReviewController {
  private snapshot: SourceSnapshot = { ...INITIAL };
  private listeners = new Set<() => void>();
  private revision = 0;
  private documentRequest = 0;
  constructor(readonly caseId: string, readonly ledgerVersion: number | null) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(change: Partial<SourceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...change };
    this.listeners.forEach((listener) => listener());
  }
  dispose() { this.revision++; this.documentRequest++; }
  private checkIndex(index: SourceIndex, fingerprint?: string) {
    if (index.case_id !== this.caseId || index.ledger_version !== this.ledgerVersion
      || (fingerprint !== undefined && index.source_fingerprint !== fingerprint)) {
      throw new SourceError(409, "Le dossier ou ses sources ont changé. Rechargez le dossier pour poursuivre.");
    }
    if (!/^[a-f0-9]{64}$/i.test(index.source_fingerprint) || !Array.isArray(index.fields)
      || !Array.isArray(index.documents) || !["pinned_snapshot", "current_parsed_unpinned", "unavailable"].includes(index.scope)
      || index.fields.some((field) => !Object.hasOwn(SOURCE_STATES, field.state) || !Array.isArray(field.links))) {
      throw new SourceError(0, "La réponse des sources est incomplète. Réessayez.");
    }
  }
  private fail(cause: unknown, document = false) {
    const error = cause instanceof SourceError ? cause : new SourceError(0, "La lecture des sources a échoué. Réessayez.");
    if (error.status === 409 || !document) {
      this.documentRequest++;
      this.update({ ...INITIAL, phase: "error", error });
    } else {
      this.update({ documentPhase: "error", document: null, documentError: error, saving: false });
    }
  }
  async load() {
    const revision = ++this.revision;
    this.documentRequest++;
    this.update({ ...INITIAL });
    try {
      const index = await fetchSources(this.caseId);
      if (revision !== this.revision) return;
      this.checkIndex(index);
      this.update({ phase: "ready", index });
    } catch (cause) {
      if (revision === this.revision) this.fail(cause);
    }
  }
  clearDocument() {
    this.documentRequest++;
    this.update({ documentPhase: "idle", document: null, documentId: null, documentError: null, opened: false, message: null });
  }
  async openDocument(documentId: string) {
    const index = this.snapshot.index;
    if (!index || !index.documents.some((document) => document.id === documentId)) return;
    const revision = this.revision;
    const request = ++this.documentRequest;
    this.update({ documentPhase: "loading", document: null, documentId, documentError: null });
    try {
      const document = await fetchSourceDocument(index, documentId);
      if (revision !== this.revision || request !== this.documentRequest) return;
      if (document.case_id !== this.caseId || document.ledger_version !== index.ledger_version
        || document.source_fingerprint !== index.source_fingerprint || document.document.id !== documentId
        || document.document.parse_run !== index.documents.find((item) => item.id === documentId)?.parse_run) {
        throw new SourceError(409, "Cette lecture ne correspond plus aux sources du dossier. Rechargez les sources.");
      }
      if (!Array.isArray(document.blocks) || document.blocks.some((block) => typeof block.text !== "string")) {
        throw new SourceError(0, "Le texte de cette source est illisible. Réessayez.");
      }
      this.update({ documentPhase: "ready", document, opened: this.snapshot.opened || document.blocks.some((block) => block.text.trim().length > 0) });
    } catch (cause) {
      if (revision === this.revision && request === this.documentRequest) this.fail(cause, true);
    }
  }
  async review(entry: LedgerEntry, status: "absent" | "unverified", note: string, attested: boolean) {
    const { index, opened, saving } = this.snapshot;
    const field = index?.fields.find((item) => item.key === entry.key);
    if (!index || !field || index.ledger_version === null || note.trim().length < 3 || saving) return;
    if (status === "absent" && (!opened || !attested || !canAttestAbsence(index, field))) return;
    if (status === "unverified" && !field.reviewed) return;
    const revision = this.revision;
    this.update({ saving: true, message: null, documentError: null });
    try {
      const updated = await annotateSource(index, entry.key, status, note);
      if (revision !== this.revision) return;
      this.checkIndex(updated, index.source_fingerprint);
      this.update({ index: updated, saving: false, message: "Repère de relecture enregistré. La valeur et les conditions de lancement de l’analyse sont inchangées." });
    } catch (cause) {
      if (revision === this.revision) this.fail(cause, true);
    }
  }
}
