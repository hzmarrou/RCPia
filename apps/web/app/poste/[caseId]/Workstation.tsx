"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  JOB_LABELS,
  STEP_LABELS,
  fetchActivity,
  progressOf,
  type JobActivity,
} from "@/lib/activity";
import type { CaseDetail, CaseSummary } from "@/lib/cases";
import { correctField, convenePanel, type Ledger, type LedgerEntry } from "@/lib/ledger";
import { AGENT_LABELS, docxUrl, downloadUrl, probatoireUrl } from "@/lib/pps";

const CANCER_LABELS: Record<string, string> = { prostate: "Prostate", bladder: "Vessie" };

const CASE_STATUS_LABELS: Record<string, string> = {
  draft: "Brouillon",
  parsing: "Extraction en cours",
  ready_for_review: "À vérifier",
  analyzing: "Analyse en cours",
  completed: "Terminé",
  blocked: "Bloqué",
};

// The pane widths a drag moves. Bounds exist so the centre keeps a floor: a rail dragged to
// the right must not squeeze the ledger table out of existence.
const PANES = {
  left: { prop: "--poste-rail", min: 190, max: 420, start: 262 },
  right: { prop: "--poste-side", min: 300, max: 720, start: 420 },
};
const CENTRE_MIN = 360;
const STORE = "rcpia.poste.panes";

type Tab = "correction" | "fiche";

function stepOf(clinicalCase: CaseDetail, ledger: Ledger | null, live: JobActivity[]): number {
  if (live.some((job) => job.job_type === "run_agent")) return 4;
  if (clinicalCase.status === "completed") return 5;
  if (clinicalCase.status === "analyzing") return 4;
  if (ledger !== null && ledger.version !== null) return 3;
  if (clinicalCase.documents.length > 0) return 2;
  return 1;
}

const PIPELINE = [
  "Dépôt",
  "Lecture du document",
  "Vérification par le clinicien",
  "Analyse par les spécialistes",
  "Fiche RCP",
];

export default function Workstation({
  clinicalCase,
  ledger,
  cases,
}: {
  clinicalCase: CaseDetail;
  ledger: Ledger | null;
  cases: CaseSummary[];
}) {
  const [tab, setTab] = useState<Tab>("correction");
  const [collapsed, setCollapsed] = useState(false);
  const [live, setLive] = useState<JobActivity[]>([]);
  const [editing, setEditing] = useState<LedgerEntry | null>(null);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<LedgerEntry[]>(ledger?.entries ?? []);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [launched, setLaunched] = useState(0);
  const valueInput = useRef<HTMLInputElement>(null);

  // Poll only while something is moving, and stop as soon as it settles: an idle case should
  // not keep a request going every two seconds for as long as the tab is open.
  //
  // `launched` restarts the loop, which is how a click gets an answer at once instead of
  // waiting out the idle interval.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;

    async function tick() {
      const jobs = await fetchActivity(clinicalCase.id);
      if (cancelled) return;
      setLive(jobs);
      polls += 1;
      // Work just asked for has not been claimed yet, and an empty answer one second after
      // the request means "not started", not "nothing to do". Keep looking at the fast
      // cadence for about half a minute before settling back.
      const awaitingLaunch = launched > 0 && polls < 12;
      timer = setTimeout(tick, jobs.length > 0 || awaitingLaunch ? 2500 : 15000);
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [clinicalCase.id, launched]);

  const openCorrection = useCallback((entry: LedgerEntry) => {
    setEditing(entry);
    setValue(entry.value ?? "");
    setReason("");
    setError(null);
    setTab("correction");
    setCollapsed(false);
    window.setTimeout(() => valueInput.current?.focus(), 0);
  }, []);

  async function save() {
    if (editing === null) return;
    setSaving(true);
    setError(null);
    try {
      // An emptied field is a deliberate "not recorded", not the empty string: the ledger
      // distinguishes the two, and sending "" would store a value nobody wrote.
      const updated = await correctField(clinicalCase.id, {
        factType: editing.key,
        value: value.trim() === "" ? null : value,
        note: reason,
      });
      setRows(updated.entries);
      setEditing(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "La correction a échoué.");
    } finally {
      setSaving(false);
    }
  }

  /**
   * Convene the panel, and say so.
   *
   * The request used to be sent and forgotten: the button did not change, the first bar was
   * up to fifteen seconds away, and a refusal disappeared into an unhandled rejection. From
   * the clinician's side that is a button that does nothing, and the second press is a
   * second panel.
   */
  async function launch() {
    setLaunching(true);
    setLaunchError(null);
    try {
      await convenePanel(clinicalCase.id);
      setLaunched((count) => count + 1);
    } catch (caught) {
      setLaunchError(
        caught instanceof Error ? caught.message : "L'analyse n'a pas pu être lancée.",
      );
    } finally {
      setLaunching(false);
    }
  }

  const step = stepOf(clinicalCase, ledger, live);
  const missing = rows.filter((entry) => entry.highlight === "red").length;
  const uncertain = rows.filter((entry) => entry.highlight === "amber").length;
  const blocking = (ledger?.issues ?? []).filter((i) => i.severity === "blocking").length;

  return (
    <div className={`poste${collapsed ? " collapsed" : ""}`}>
      <nav className="poste-rail">
        <div className="poste-rail-label">Dossiers</div>
        <div className="poste-caselist">
          {cases.map((row) => (
            <Link
              key={row.id}
              href={`/poste/${row.id}`}
              className="poste-caserow"
              aria-current={row.id === clinicalCase.id}
            >
              <span className="ref">{row.reference}</span>
              <span className="sub">
                {CANCER_LABELS[row.cancer_type] ?? row.cancer_type} ·{" "}
                {CASE_STATUS_LABELS[row.status] ?? row.status}
              </span>
            </Link>
          ))}
        </div>
      </nav>

      <Grip which="left" />

      <main className="poste-main">
        <div className="poste-head">
          <div className="poste-titleline">
            <h1>{clinicalCase.reference}</h1>
            <span className="badge">
              {CANCER_LABELS[clinicalCase.cancer_type] ?? clinicalCase.cancer_type}
            </span>
            <span className="badge">
              {CASE_STATUS_LABELS[clinicalCase.status] ?? clinicalCase.status}
            </span>
          </div>
          <ol className="poste-pipeline">
            {PIPELINE.map((name, index) => (
              <li
                key={name}
                className={
                  index + 1 < step ? "done" : index + 1 === step ? "now" : undefined
                }
              >
                <span className="num">{index + 1 < step ? "✓" : index + 1}</span>
                {name}
              </li>
            ))}
          </ol>
        </div>

        {ledger === null ? (
          <section className="poste-section">
            <p className="muted">
              Aucune donnée extraite pour ce dossier. Déposez un document, puis revenez.
            </p>
          </section>
        ) : (
          <>
            <section className="poste-section">
              <h2>Complétude</h2>
              <div className="poste-metrics">
                <div>
                  <div className="k">Données lues</div>
                  <div className="v">{rows.length}</div>
                </div>
                <div>
                  <div className="k">Manquantes</div>
                  <div className="v red">{missing}</div>
                </div>
                <div>
                  <div className="k">Incertaines</div>
                  <div className="v amber">{uncertain}</div>
                </div>
                <div>
                  <div className="k">Bloquantes</div>
                  <div className="v">{blocking}</div>
                </div>
              </div>
            </section>

            {live.length > 0 ? <Progress jobs={live} /> : null}

            <section className="poste-section">
              <h2>Données extraites du dossier</h2>
              <div className="poste-tablewrap">
                <table>
                  <colgroup>
                    <col className="label" />
                    <col />
                    <col className="act" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Donnée</th>
                      <th>Valeur</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((entry, index) => (
                      <tr
                        // Position, because nothing else is unique. The ledger returns some
                        // entries twice over — `platelets`, `bicarbonate` and `phosphorus`
                        // each arrive as two rows identical in category, key, value and
                        // origin — so a flat list has genuinely duplicate items. The grouped
                        // table upstream never collides only because it renders one category
                        // at a time. This is a display working around a defect in the data,
                        // not a fix for it: the duplication is upstream and still there.
                        key={`${entry.category}-${entry.key}-${index}`}
                        className={
                          entry.highlight === "red"
                            ? "miss"
                            : entry.highlight === "amber"
                              ? "unc"
                              : undefined
                        }
                      >
                        <td>{entry.label}</td>
                        <td>
                          {entry.value === null ? (
                            <span className="missing-value">Information manquante</span>
                          ) : (
                            <Value text={entry.value} />
                          )}
                          {entry.note ? <div className="src">{entry.note}</div> : null}
                        </td>
                        <td>
                          <button className="poste-fix" onClick={() => openCorrection(entry)}>
                            Corriger
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="poste-gate">
              <p>
                Les spécialistes ne liront que la version affichée ci-dessus. Corrigez
                d&apos;abord ce qui doit l&apos;être : l&apos;analyse part de vos données, pas
                de la lecture automatique.
              </p>
              <button
                onClick={launch}
                disabled={!ledger.can_run_analysis || live.length > 0 || launching}
              >
                {launching
                  ? "Envoi de la demande…"
                  : live.length > 0
                    ? "Analyse en cours…"
                    : "Lancer l'analyse"}
              </button>
              {launchError ? <p className="error">{launchError}</p> : null}
              {launched > 0 && !launching && live.length === 0 ? (
                // Between the request landing and the first worker claiming it, the case is
                // genuinely empty. Saying so beats a screen that looks like the click missed.
                <p className="hint">
                  Demande envoyée. Les spécialistes apparaissent ici dès qu&apos;ils
                  commencent.
                </p>
              ) : null}
            </section>
          </>
        )}
      </main>

      <Grip which="right" />

      <aside className="poste-side">
        <div className="poste-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "correction"}
            onClick={() => setTab("correction")}
          >
            Correction
          </button>
          <button role="tab" aria-selected={tab === "fiche"} onClick={() => setTab("fiche")}>
            Fiche
          </button>
          <button
            className="poste-close"
            onClick={() => setCollapsed(true)}
            title="Fermer le panneau"
          >
            ×
          </button>
        </div>

        <div className="poste-sidebody">
          {tab === "correction" ? (
            editing === null ? (
              <p className="muted">
                Choisissez « Corriger » sur une ligne pour modifier une valeur sans quitter
                le dossier.
              </p>
            ) : (
              <>
                <div className="muted">Donnée</div>
                <div className="poste-field">{editing.label}</div>

                <label htmlFor="poste-value">Valeur</label>
                <input
                  id="poste-value"
                  ref={valueInput}
                  type="text"
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                />

                <label htmlFor="poste-reason">Motif de la correction</label>
                <textarea
                  id="poste-reason"
                  rows={3}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />

                <button onClick={save} disabled={saving}>
                  {saving ? "Enregistrement…" : "Enregistrer la correction"}
                </button>
                {error ? <p className="error">{error}</p> : null}
                <p className="hint">
                  La valeur lue automatiquement est conservée. La correction s&apos;ajoute à
                  l&apos;historique du dossier, avec son motif et son auteur.
                </p>
                {editing.source_excerpt ? (
                  <blockquote>{editing.source_excerpt}</blockquote>
                ) : null}
              </>
            )
          ) : (
            <>
              <p className="provisional">
                Toute sortie de RCPia est provisoire et doit être relue, corrigée et validée
                en RCP avant tout usage clinique.
              </p>
              <p>
                <Link href={`/cases/${clinicalCase.id}/pps`}>
                  Ouvrir la fiche provisoire
                </Link>
              </p>
              <p className="poste-downloads">
                <a href={docxUrl(clinicalCase.id)}>Télécharger la fiche RCP (Word)</a>
                <a className="muted" href={probatoireUrl(clinicalCase.id)}>
                  Dossier probatoire (Word)
                </a>
                <a className="muted" href={downloadUrl(clinicalCase.id)}>
                  Format Markdown
                </a>
              </p>
            </>
          )}
        </div>
      </aside>

      {collapsed ? (
        <button className="poste-reopen" onClick={() => setCollapsed(false)}>
          Ouvrir le panneau
        </button>
      ) : null}
    </div>
  );
}

// Beyond this, a value is a narrative rather than a value.
const LONG_VALUE = 200;

/**
 * A ledger value, folded when it is a wall of text.
 *
 * Several fields hold whole passages of the dossier — a cystoscopy description, a CT report.
 * Printed in full they make single rows a thousand pixels tall and push the table wider than
 * its pane, so the one row a clinician is looking for sits below a screenful of prose.
 *
 * Folded, not truncated: the text is all there, one click away, because this table is what
 * someone checks the extraction against and a value they cannot read is a value they cannot
 * check.
 */
function Value({ text }: { text: string }) {
  if (text.length <= LONG_VALUE) return <span className="val">{text}</span>;
  return (
    <details className="poste-long">
      <summary>
        <span className="val">{text.slice(0, LONG_VALUE).trimEnd()}…</span>
        <span className="more">tout afficher</span>
      </summary>
      <span className="val">{text}</span>
    </details>
  );
}

/**
 * What is running, on the scale the API sends with each job.
 *
 * The bar sits inside the step the worker last reported and creeps on the clock towards the
 * next one without ever entering it. Two things make that necessary rather than decorative:
 * the steps are wildly uneven — writing the opinion is roughly nine tenths of an agent's
 * wall clock — and the milestone only changes four times, so a bar pinned to milestones
 * alone stands still for a minute and reads as a hung run. The shares are measured; see
 * `lib/activity.ts`.
 */
function Progress({ jobs }: { jobs: JobActivity[] }) {
  // The milestone changes four times; the clock is what moves in between, so the bar needs
  // its own tick. A second is finer than anyone watching a seventy-second step can see move,
  // and coarse enough to cost nothing.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <section className="poste-section">
      <h2>En cours</h2>
      <div className="poste-agents">
        {jobs.map((job) => {
          const progress = progressOf(job, now);
          const label = job.agent_role
            ? (AGENT_LABELS[job.agent_role] ?? job.agent_role)
            : (JOB_LABELS[job.job_type] ?? job.job_type);
          return (
            <div key={job.id} className="poste-agent">
              <span className={`pip${job.status === "running" ? " run" : ""}`} />
              <span className="who">
                <span className="name">{label}</span>
                <br />
                <span className="stage">
                  {job.status === "waiting_for_user"
                    ? "en attente d'une information"
                    : job.milestone === null
                      ? "en attente"
                      : (STEP_LABELS[job.milestone] ?? job.milestone)}
                  {progress?.overrunning ? (
                    // Said out loud rather than left to a bar stuck near its ceiling: this
                    // step is taking longer than it usually does, and the run is still alive.
                    <span className="slow"> — plus long que d&apos;habitude</span>
                  ) : null}
                </span>
              </span>
              <span className="track">
                <span
                  className="fill"
                  style={{ width: `${((progress?.fraction ?? 0) * 100).toFixed(1)}%` }}
                />
              </span>
            </div>
          );
        })}
      </div>
      <p className="hint">
        Chaque barre avance aux étapes que l&apos;agent a réellement franchies, et n&apos;en
        annonce jamais une qui n&apos;est pas atteinte. La rédaction de l&apos;avis est la
        plus longue étape, et de loin : une barre lente n&apos;est pas une panne.
      </p>
    </section>
  );
}

/** A draggable splitter. Keyboard as well as pointer: a control only a mouse can reach is
 * not a control everyone has. */
function Grip({ which }: { which: "left" | "right" }) {
  const spec = PANES[which];
  const sign = which === "left" ? 1 : -1;

  const width = () =>
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue(spec.prop)) ||
    spec.start;

  const set = useCallback(
    (px: number) => {
      const other =
        parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue(
            PANES[which === "left" ? "right" : "left"].prop,
          ),
        ) || PANES[which === "left" ? "right" : "left"].start;
      const room = window.innerWidth - other - 14 - CENTRE_MIN;
      const next = Math.round(Math.max(spec.min, Math.min(px, spec.max, room)));
      document.documentElement.style.setProperty(spec.prop, `${next}px`);
      try {
        localStorage.setItem(
          STORE,
          JSON.stringify({
            left: which === "left" ? next : other,
            right: which === "right" ? next : other,
          }),
        );
      } catch {
        // A private window, or storage switched off. The layout still works.
      }
    },
    [spec, which],
  );

  useEffect(() => {
    let saved: { left?: number; right?: number } | null = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORE) ?? "null");
    } catch {
      saved = null;
    }
    const remembered = saved?.[which];
    if (typeof remembered === "number" && Number.isFinite(remembered)) {
      document.documentElement.style.setProperty(spec.prop, `${remembered}px`);
    }
  }, [spec, which]);

  return (
    <div
      className={`poste-grip ${which}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={which === "left" ? "Largeur de la liste" : "Largeur du panneau"}
      tabIndex={0}
      onPointerDown={(event) => {
        event.preventDefault();
        const from = event.clientX;
        const initial = width();
        const target = event.currentTarget;
        target.setPointerCapture(event.pointerId);
        const move = (moved: PointerEvent) => set(initial + sign * (moved.clientX - from));
        const stop = () => {
          target.releasePointerCapture(event.pointerId);
          target.removeEventListener("pointermove", move);
          target.removeEventListener("pointerup", stop);
          target.removeEventListener("pointercancel", stop);
        };
        target.addEventListener("pointermove", move);
        target.addEventListener("pointerup", stop);
        target.addEventListener("pointercancel", stop);
      }}
      onDoubleClick={() => set(spec.start)}
      onKeyDown={(event) => {
        const stepSize = event.shiftKey ? 48 : 12;
        if (event.key === "ArrowLeft") set(width() - sign * stepSize);
        else if (event.key === "ArrowRight") set(width() + sign * stepSize);
        else return;
        event.preventDefault();
      }}
    />
  );
}
