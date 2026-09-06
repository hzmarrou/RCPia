"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  JOB_LABELS,
  STEP_LABELS,
  fetchActivity,
  readableDuration,
  stageOf,
  type JobActivity,
} from "@/lib/activity";
import type { CaseDetail, CaseSummary } from "@/lib/cases";
import { correctField, convenePanel, type Ledger, type LedgerEntry } from "@/lib/ledger";
import { groupEntries, readSex, tally } from "@/lib/ledgerView";
import { PIPELINE, pipelineOf } from "@/lib/pipeline";
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

/**
 * Where a launch has got to.
 *
 * `running` covers the whole span from the request being accepted to the queue emptying,
 * including the seconds before the first worker claims anything — the gate is shut for all
 * of it, because the case looks idle then and a second press is a second panel.
 */
type LaunchState =
  | { phase: "idle" }
  | { phase: "sending" }
  | { phase: "running" }
  | { phase: "failed"; message: string };

export default function Workstation({
  clinicalCase,
  ledger,
  cases,
}: {
  clinicalCase: CaseDetail;
  ledger: Ledger | null;
  cases: CaseSummary[];
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("correction");
  const [collapsed, setCollapsed] = useState(false);
  const [live, setLive] = useState<JobActivity[]>([]);
  const [editing, setEditing] = useState<LedgerEntry | null>(null);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The whole ledger, not only its rows: `can_run_analysis` and `issues` move with a
  // correction too. Keeping the rows and dropping the rest left the gate deciding from the
  // version the page was built with — a corrected blocking field kept the button disabled,
  // and a newly created one left it enabled until the API refused the click.
  const [ledgerState, setLedgerState] = useState<Ledger | null>(ledger);
  const [launch, setLaunch] = useState<LaunchState>({ phase: "idle" });
  // Whether the last few polls reached the server. False means the state on screen is the
  // last one confirmed, not the current one — a difference worth saying out loud.
  const [reachable, setReachable] = useState(true);
  // Bumped by "Réessayer": restarts the poll loop at once instead of waiting out the backoff.
  const [attempt, setAttempt] = useState(0);
  // Whether work has been seen running on this case, kept outside the polling effect so a
  // retry or a phase change cannot forget it.
  const sawWork = useRef(false);
  const valueInput = useRef<HTMLInputElement>(null);

  // A different dossier is a different question; nothing observed about the last one carries.
  useEffect(() => {
    sawWork.current = false;
  }, [clinicalCase.id]);

  // A server refresh brings a newer ledger than anything held here, so local state steps
  // aside for it.
  useEffect(() => {
    setLedgerState(ledger);
  }, [ledger]);

  // Poll only while something is moving, and stop as soon as it settles: an idle case should
  // not keep a request going every two seconds for as long as the tab is open.
  //
  // The launch phase restarts the loop, which is how a click gets an answer at once instead
  // of waiting out the idle interval.
  const launchPhase = launch.phase;
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    let failures = 0;

    async function tick() {
      const snapshot = await fetchActivity(clinicalCase.id);
      if (cancelled) return;
      polls += 1;

      // A failed poll says nothing about the queue, so it changes nothing on screen: the
      // bars stay as they were, a running panel is still running, and nothing is declared
      // finished. One dropped request used to read exactly like a completed panel.
      if (!snapshot.ok) {
        failures += 1;
        // One blip is not worth a warning; a second means the page is showing a picture it
        // can no longer confirm, and the clinician should be told before they act on it.
        if (failures >= 2) setReachable(false);
        timer = setTimeout(tick, failures >= 2 ? 8000 : 2500);
        return;
      }

      failures = 0;
      setReachable(true);
      setLive(snapshot.jobs);
      if (snapshot.jobs.length > 0) sawWork.current = true;

      // An empty queue means this page needs a fresh case, not that a fiche succeeded.
      // A crash can empty the queue one poll before housekeeping records the terminal case
      // status, so keep refreshing while the backend still calls it analyzing.
      //
      // Guarded by `sawWork`: an empty answer on the first poll means nothing was running,
      // not that something ended. It is a ref rather than a local so that it survives the
      // effect being torn down and rebuilt — by a retry, or by the launch phase changing.
      // Reset per run, a panel that finished while the server was unreachable could never be
      // recognised as finished afterwards, and the button stayed disabled for good.
      if (
        snapshot.jobs.length === 0 &&
        (sawWork.current || clinicalCase.status === "analyzing")
      ) {
        sawWork.current = false;
        setLaunch({ phase: "idle" });
        router.refresh();
        timer = setTimeout(tick, 2500);
        return;
      }

      // Work just asked for has not been claimed yet, and an empty answer one second after
      // the request means "not started", not "nothing to do". Keep looking at the fast
      // cadence for about half a minute before settling back.
      const awaitingLaunch = launchPhase === "running" && polls < 12;
      timer = setTimeout(tick, snapshot.jobs.length > 0 || awaitingLaunch ? 2500 : 15000);
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [clinicalCase.id, clinicalCase.status, launchPhase, router, attempt]);

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
      setLedgerState(updated);
      setEditing(null);
      router.refresh();
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
  async function startAnalysis() {
    setLaunch({ phase: "sending" });
    try {
      await convenePanel(clinicalCase.id);
      // Stays "running" until the poll sees work appear and then disappear. Clearing it on
      // the response instead would re-enable the button in the seconds before the first
      // worker claims anything, and a second press is a second panel.
      setLaunch({ phase: "running" });
      router.refresh();
    } catch (caught) {
      setLaunch({
        phase: "failed",
        message: caught instanceof Error ? caught.message : "L'analyse n'a pas pu être lancée.",
      });
    }
  }

  const rows = ledgerState?.entries ?? [];
  // `reachable` is passed on: while the server is silent the jobs held here are a remembered
  // picture, and a remembered picture must not tell the header an analysis is running now.
  const pipeline = pipelineOf(clinicalCase, ledgerState, live, reachable);
  // The sex is read from the ledger the clinician is looking at, so correcting it re-sorts
  // the table on the spot rather than after a reload.
  const sex = readSex(rows);
  const counts = tally(rows, sex);
  const groups = groupEntries(rows, sex);
  const blocking = (ledgerState?.issues ?? []).filter((i) => i.severity === "blocking").length;
  // Anything that means work is under way or on its way: the gate stays shut for all of it.
  const busy = live.length > 0 || launch.phase === "sending" || launch.phase === "running";

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
            {PIPELINE.map((name, index) => {
              const state = pipeline[index];
              return (
                <li key={name} className={state}>
                  <span className="num">
                    {state === "done" ? "✓"
                      : state === "unknown" ? "?"
                      : state === "blocked" ? "!" : index + 1}
                  </span>
                  {name}
                  {state === "unknown" ? (
                    <span className="qual">
                      {reachable ? " — non suivi" : " — état inconnu"}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ol>
          {pipeline.includes("unknown") ? (
            <p className="poste-qualifier">
              {reachable ? (
                <>
                  L&apos;état de ces étapes n&apos;est pas disponible.
                  L&apos;onglet « Fiche » indique si une proposition existe.
                </>
              ) : (
                <>
                  Le serveur ne répond pas : l&apos;état de ces étapes n&apos;est pas connu.
                  Ce qui est affiché plus bas est le dernier état confirmé.
                </>
              )}
            </p>
          ) : null}
          {pipeline.includes("blocked") ? (
            <p className="poste-qualifier" role="status">
              L&apos;analyse s&apos;est terminée sans synthèse exploitable.
              Les éventuels avis disponibles ne constituent pas une fiche RCP complète.
            </p>
          ) : null}
        </div>

        {ledgerState === null ? (
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
                  <div className="v">{counts.read}</div>
                </div>
                <div>
                  <div className="k">Manquantes</div>
                  <div className="v red">{counts.missing}</div>
                </div>
                <div>
                  <div className="k">Incertaines</div>
                  <div className="v amber">{counts.uncertain}</div>
                </div>
                <div>
                  <div className="k">Bloquantes</div>
                  <div className="v">{blocking}</div>
                </div>
              </div>
              {counts.notApplicable > 0 ? (
                // The count above is the API's, unchanged. Saying so matters: the specialists
                // are sent the ledger the API holds, so a field marked here is still a gap
                // there, and a total quietly reduced would promise otherwise.
                <p className="hint">
                  {counts.notApplicable} champ
                  {counts.notApplicable > 1 ? "s sont signalés" : " est signalé"} sans objet
                  d&apos;après le sexe inscrit au dossier. Ce repère est un confort de
                  lecture : ces champs restent comptés ci-dessus et transmis comme manquants
                  aux spécialistes.
                </p>
              ) : null}
            </section>

            {live.length > 0 ? <Progress jobs={live} reachable={reachable} /> : null}

            {!reachable ? (
              <section className="poste-section">
                <p className="error">
                  Impossible de joindre le serveur. L&apos;état affiché est le dernier connu
                  {live.length > 0 ? " et l'analyse est peut-être toujours en cours" : ""}.{" "}
                  <button className="poste-retry" onClick={() => setAttempt((n) => n + 1)}>
                    Réessayer
                  </button>
                </p>
              </section>
            ) : null}

            <section className="poste-section">
              <h2>Données extraites du dossier</h2>
              {groups.map((group) => (
                <div key={group.category} className="poste-group">
                  <div className="poste-grouphead">
                    <h3>{group.label}</h3>
                    <span className="counts">
                      {group.present} lue{group.present > 1 ? "s" : ""}
                      {group.missing > 0 ? (
                        <span className="gap"> · {group.missing} manquante
                          {group.missing > 1 ? "s" : ""}</span>
                      ) : null}
                      {group.notApplicable > 0 ? (
                        <span> · {group.notApplicable} sans objet</span>
                      ) : null}
                    </span>
                  </div>
                  <div className="poste-tablewrap">
                    <table>
                      <colgroup>
                        <col className="label" />
                        <col />
                        <col className="act" />
                      </colgroup>
                      <tbody>
                        {group.rows.map(({ entry, applicable }, index) => (
                          <tr
                            // Position, because nothing else is unique. The ledger returns
                            // some entries twice over — `platelets`, `bicarbonate` and
                            // `phosphorus` each arrive as two rows identical in category,
                            // key, value and origin. This is a display working around a
                            // defect in the data, not a fix for it: the duplication is
                            // upstream and still there.
                            key={`${entry.key}-${index}`}
                            className={
                              !applicable
                                ? "na"
                                : entry.highlight === "red"
                                  ? "miss"
                                  : entry.highlight === "amber"
                                    ? "unc"
                                    : undefined
                            }
                          >
                            <td>{entry.label}</td>
                            <td>
                              {!applicable ? (
                                // Marked, not hidden: if the recorded sex is wrong, the
                                // clinician has to be able to see what it silenced.
                                <span className="na-value">
                                  Sans objet
                                  {entry.value === null ? "" : ` — ${entry.value}`}
                                </span>
                              ) : entry.value === null ? (
                                <span className="missing-value">Information manquante</span>
                              ) : (
                                <Value text={entry.value} />
                              )}
                              {entry.note ? <div className="src">{entry.note}</div> : null}
                            </td>
                            <td>
                              <button
                                className="poste-fix"
                                onClick={() => openCorrection(entry)}
                              >
                                Corriger
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </section>

            <section className="poste-gate">
              <p>
                Les spécialistes ne liront que la version affichée ci-dessus. Corrigez
                d&apos;abord ce qui doit l&apos;être : l&apos;analyse part de vos données, pas
                de la lecture automatique.
              </p>
              <button
                onClick={startAnalysis}
                disabled={!ledgerState.can_run_analysis || busy}
              >
                {launch.phase === "sending"
                  ? "Envoi de la demande…"
                  : busy
                    ? "Analyse en cours…"
                    : "Lancer l'analyse"}
              </button>
              {launch.phase === "failed" ? <p className="error">{launch.message}</p> : null}
              {launch.phase === "running" && live.length === 0 && reachable ? (
                // Between the request landing and the first worker claiming it, the case is
                // genuinely empty. Saying so beats a screen that looks like the click missed.
                <p className="hint">
                  Demande envoyée. Les spécialistes apparaissent ici dès qu&apos;ils
                  commencent.
                </p>
              ) : null}
              {!reachable && busy ? (
                // The gate stays shut while the state is unknown: convening a second panel
                // over a first one that may well be running is the expensive mistake here,
                // and "Réessayer" above is the way out that does not risk it.
                <p className="hint">
                  État de l&apos;analyse inconnu tant que le serveur ne répond pas. Le bouton
                  reste fermé pour ne pas convoquer un second panel par-dessus le premier.
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
 * What is running: the step each job last reported, and how long it has been on it.
 *
 * **No percentage.** The worker reports four milestones and nothing between them, so any
 * position between two of them is invented — and a bar interpolated on the clock climbs at
 * the same rate whether the agent is writing or hung, which is exactly backwards at the
 * moment it matters. The indicator is indeterminate: it says work is happening, not how much
 * of it is done. The elapsed time is the honest number, and a step past its measured usual
 * duration says so in words.
 */
function Progress({ jobs, reachable }: { jobs: JobActivity[]; reachable: boolean }) {
  // The elapsed time is what moves between milestones, so this needs its own tick. A second
  // is finer than anyone watching a seventy-second step needs, and costs nothing.
  const [now, setNow] = useState(() => Date.now());
  const frozen = useRef(now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  // The moment of the last confirmed reading, kept so the clocks can stop there rather than
  // running on through a silence.
  if (reachable) frozen.current = now;

  return (
    <section className="poste-section">
      <h2>En cours{reachable ? "" : " — dernier état connu"}</h2>
      <div className={`poste-agents${reachable ? "" : " stale"}`}>
        {jobs.map((job) => {
          // Frozen at the last confirmed reading while the server is unreachable: a clock
          // that keeps counting would be counting time nobody has confirmed the job spent.
          const stage = stageOf(job, reachable ? now : frozen.current);
          const label = job.agent_role
            ? (AGENT_LABELS[job.agent_role] ?? job.agent_role)
            : (JOB_LABELS[job.job_type] ?? job.job_type);
          const waiting = job.status === "waiting_for_user";
          return (
            <div key={job.id} className="poste-agent">
              <span className={`pip${job.status === "running" ? " run" : ""}`} />
              <span className="who">
                <span className="name">{label}</span>
                <br />
                <span className="stage">
                  {waiting
                    ? "en attente d'une information"
                    : stage === null
                      ? "en attente"
                      : (STEP_LABELS[stage.step] ?? stage.step)}
                  {stage?.elapsedSeconds !== null && stage !== null ? (
                    <span className="since">
                      {" "}
                      · depuis {readableDuration(stage.elapsedSeconds)}
                    </span>
                  ) : null}
                  {stage?.overrunning === true ? (
                    // Only for a step with a measured usual duration. An unknown step has no
                    // "usually", so nothing is claimed about it.
                    <span className="slow"> — plus long que d&apos;habitude</span>
                  ) : null}
                </span>
              </span>
              <span
                className={`track${waiting || job.status === "queued" ? "" : " working"}`}
                role="progressbar"
                aria-label={`${label} : ${stage === null ? "en attente" : (STEP_LABELS[stage.step] ?? stage.step)}`}
              >
                <span className="fill" />
              </span>
            </div>
          );
        })}
      </div>
      <p className="hint">
        Chaque ligne indique l&apos;étape que l&apos;agent a réellement signalée et depuis
        combien de temps il y est. L&apos;avancement à l&apos;intérieur d&apos;une étape
        n&apos;est pas connu et n&apos;est donc pas affiché. La rédaction de l&apos;avis est
        la plus longue étape, et de loin.
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
