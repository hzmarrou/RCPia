import Link from "next/link";
import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { SERVER_API_BASE_URL } from "@/lib/api";
import {
  AGENT_LABELS,
  AI_ORIGIN_TITLE,
  ANSWER_LABELS,
  docxUrl,
  downloadUrl,
  fetchPps,
  probatoireUrl,
  type EvidenceEntry,
  type MissingItem,
  type Referral,
  type SupportItem,
  type TreatmentStep,
} from "@/lib/pps";

import { DecisionProposal } from "./decision-proposal";

export const dynamic = "force-dynamic";

function agentLabel(role: string): string {
  return AGENT_LABELS[role] ?? role.replace(/_/g, " ");
}

function ReferralBlock({ referral }: { referral: Referral }) {
  return (
    <li>
      <strong>{referral.label}</strong> — {ANSWER_LABELS[referral.answer]}
      <p>{referral.justification}</p>
      {referral.basis ? (
        <p className="muted">Fondé sur — {referral.basis}</p>
      ) : null}
    </li>
  );
}

function SupportBlockCaveat({ caveat }: { caveat: string | null }) {
  return caveat ? <p className="muted">{caveat}</p> : null;
}

function StepBlock({ step, index }: { step: TreatmentStep; index: number }) {
  return (
    <li>
      <strong>
        {index}. {step.title}
      </strong>
      {step.items.length > 0 ? (
        <ul>
          {step.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {step.evidence_ids.length > 0 ? (
        <p className="muted">Sources : {step.evidence_ids.join(", ")}</p>
      ) : null}
    </li>
  );
}

function SupportBlock({ item }: { item: SupportItem }) {
  return (
    <li>
      <strong>{item.title}</strong>
      <p>{item.detail}</p>
      {item.trigger ? (
        <p className="muted">Déclenché par la valeur enregistrée — {item.trigger}</p>
      ) : null}
      <SupportBlockCaveat caveat={item.caveat} />
    </li>
  );
}

function MissingRow({ item }: { item: MissingItem }) {
  return (
    <tr>
      <td className={item.highlight === "red" ? "highlight-red" : "highlight-amber"}>
        {item.label}
      </td>
      <td>{item.message}</td>
    </tr>
  );
}

function EvidenceRow({ entry }: { entry: EvidenceEntry }) {
  return (
    <tr>
      <td>
        <code>{entry.id}</code>
      </td>
      <td>{entry.quote}</td>
      <td>{entry.supports ?? "—"}</td>
    </tr>
  );
}

export default async function PpsPage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  const result = await fetchPps(SERVER_API_BASE_URL, cookieHeader, caseId);
  if (result.kind === "forbidden") {
    notFound();
  }

  if (result.kind === "not-ready") {
    return (
      <section>
        <h1>Proposition provisoire</h1>
        <p className="warnings">{result.detail}</p>
        <p>
          <Link href={`/cases/${caseId}/ledger`}>Vérifier les données extraites</Link>
        </p>
      </section>
    );
  }

  const { pps } = result;

  return (
    <section className="fiche-document">
      {/* Before the title: a reader who stops after one line must know this decides
          nothing. */}
      <p className="provisional">{pps.banner}</p>

      <h1>{pps.proposal ? AI_ORIGIN_TITLE : "Fiche RCP — proposition de prise en charge"}</h1>
      {pps.superseded_by !== null ? (
        <p className="provisional">
          Données modifiées depuis cette analyse : l&apos;avis porte sur la version{" "}
          {pps.ledger_version}, la version courante est la {pps.superseded_by}. Relancez
          l&apos;analyse avant de vous appuyer sur ce document.
        </p>
      ) : null}
      {!pps.fiche && <p className="muted">
        Dossier {pps.case_reference} · généré le {pps.generated_on} · données version{" "}
        {pps.ledger_version} · générateur {pps.generator_version} · avis{" "}
        {pps.agent_run_ids.join(", ") || "—"}
      </p>}
      {/* The fiche is what goes to the RCP; the probatoire and the markdown are for
          whoever wants to check the reasoning behind it. */}
      <p className="downloads">
        <a href={docxUrl(caseId)}>Télécharger la fiche RCP (Word)</a>
        <a className="muted" href={probatoireUrl(caseId)}>
          Dossier probatoire (Word)
        </a>
        <a className="muted" href={downloadUrl(caseId)}>
          Télécharger au format Markdown
        </a>
      </p>

      <h2>1 — Synthèse et question posée</h2>
      <dl>
        <dt>Patient</dt>
        <dd>{pps.synthesis.patient}</dd>
        <dt>Diagnostic</dt>
        <dd>{pps.synthesis.diagnosis ?? "Non renseigné dans le dossier"}</dd>
        <dt>Comorbidités</dt>
        <dd>{pps.synthesis.comorbidities ?? "Non renseigné dans le dossier"}</dd>
        <dt>Question posée</dt>
        <dd>{pps.synthesis.question}</dd>
      </dl>

      {pps.proposal ? (
        <DecisionProposal pps={pps} proposal={pps.proposal} />
      ) : (
        <>
      <h2>2 — Décision RCP</h2>
      <p>{pps.decision.statement}</p>
      <ul>
        {pps.decision.referrals.map((referral) => (
          <ReferralBlock key={referral.label} referral={referral} />
        ))}
      </ul>

      <h2>3 — Propositions de traitements</h2>
      {pps.treatments.withheld ? (
        // No steps follow. An agent that declines still writes about what could be
        // considered, and showing that here would disclaim a plan and then print one.
        <p className="provisional">
          Aucune proposition thérapeutique n&apos;est formulée. L&apos;agent a estimé
          qu&apos;aucune conduite ne pouvait être proposée sans risque en l&apos;état.
          Son raisonnement complet figure en annexe.
        </p>
      ) : null}
      <ul>
        {pps.treatments.steps.map((step, index) => (
          <StepBlock key={step.title} step={step} index={index + 1} />
        ))}
      </ul>
      {pps.treatments.missing_information.length > 0 ? (
        <>
          <h3>Informations requises avant toute proposition</h3>
          <ul>
            {pps.treatments.missing_information.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      {pps.treatments.contraindications.length > 0 ? (
        <>
          <h3>Contre-indications relevées</h3>
          <ul>
            {pps.treatments.contraindications.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      {pps.treatments.precisions.length > 0 ? (
        <>
          <h3>Précisions et incertitudes</h3>
          <ul>
            {pps.treatments.precisions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="muted">{pps.treatments.status}</p>

      <h2>4 — Accompagnement et soins de support</h2>
      <ul>
        {pps.support.items.map((item) => (
          <SupportBlock key={`${item.title}-${item.trigger ?? "standing"}`} item={item} />
        ))}
      </ul>
      <p className="muted">{pps.support.status}</p>

      <h2>5 — Évaluation sociale et familiale</h2>
      <dl>
        {pps.social.rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd className={row.missing ? "highlight-red" : undefined}>{row.value}</dd>
          </div>
        ))}
      </dl>
      {pps.social.comment ? <p className="muted">{pps.social.comment}</p> : null}

      <h2>6 — Cadre de la prise en charge</h2>
      <dl>
        <dt>Référentiel</dt>
        <dd>{pps.framework.reference_standard}</dd>
        <dt>Essai clinique</dt>
        <dd>
          {ANSWER_LABELS[pps.framework.clinical_trial]}
          {pps.framework.clinical_trial_note
            ? ` — ${pps.framework.clinical_trial_note}`
            : ""}
        </dd>
        <dt>Fiche validée</dt>
        <dd>
          {pps.framework.validated ? "Oui" : "Non"} — {pps.framework.validated_note}
        </dd>
        <dt>Participants</dt>
        <dd>{pps.framework.participants}</dd>
        <dt>Réévaluation</dt>
        <dd>{pps.framework.review}</dd>
      </dl>
        </>
      )}

      {!pps.fiche && <>
      <h2>Informations manquantes et incertaines</h2>
      {pps.missing.length === 0 ? (
        <p className="muted">
          Aucune information requise ne manque pour ce type de dossier.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Champ</th>
              <th>Conséquence</th>
            </tr>
          </thead>
          <tbody>
            {pps.missing.map((item) => (
              <MissingRow key={`${item.label}-${item.severity}`} item={item} />
            ))}
          </tbody>
        </table>
      )}

      <h2>Registre des sources</h2>
      {pps.evidence.length === 0 ? (
        <p className="warnings">
          Aucune source citée : cet avis ne s&apos;appuie sur aucun extrait du dossier.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Réf.</th>
              <th>Extrait du dossier</th>
              <th>Justifie</th>
            </tr>
          </thead>
          <tbody>
            {pps.evidence.map((entry) => (
              <EvidenceRow key={entry.id} entry={entry} />
            ))}
          </tbody>
        </table>
      )}

      {pps.appendices.map((appendix) => (
        <section key={appendix.agent_role}>
          <h2>Annexe — raisonnement : {agentLabel(appendix.agent_role)}</h2>
          {appendix.model ? (
            <p className="muted">
              Produit par {appendix.model}
              {appendix.ledger_version !== null
                ? `, version ${appendix.ledger_version} du tableau de données`
                : ""}
              . Avis provisoire, non validé.
            </p>
          ) : null}
          {appendix.withheld_statement ? (
            <>
              <h3>Ce que l&apos;agent a indiqué en lieu de recommandation</h3>
              <p>{appendix.withheld_statement}</p>
            </>
          ) : null}
          <p>{appendix.rationale}</p>
          {appendix.uncertainty_notes.length > 0 ? (
            <>
              <h3>Incertitudes signalées</h3>
              <ul>
                {appendix.uncertainty_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ))}
      </>}
    </section>
  );
}
