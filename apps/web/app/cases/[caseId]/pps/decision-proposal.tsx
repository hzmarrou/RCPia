import type {
  ConciseFiche, InstitutionalFiche, Pps, PpsProposal, ProposedAction, ProposedAnswer,
} from "@/lib/pps";

function Conditions({ items }: { items: string[] }) {
  return items.length ? (
    <ul aria-label="Conditions">
      {items.map((item, index) => <li key={index}>{item}</li>)}
    </ul>
  ) : null;
}

function Sources({ ids }: { ids: string[] }) {
  return ids.length ? <p className="muted">Sources : {ids.join(", ")}</p> : null;
}

function AnswerBlock({ label, answer }: { label: string; answer: ProposedAnswer }) {
  return (
    <div>
      <h3>{label}</h3>
      <p>{answer.answer === "oui" ? "☒ Oui  ☐ Non" : "☐ Oui  ☒ Non"}</p>
      <p>{answer.rationale}</p>
      <Conditions items={answer.conditions} />
      <Sources ids={answer.evidence_ids} />
    </div>
  );
}

function ActionBlock({ action }: { action: ProposedAction }) {
  return (
    <div>
      <p><strong>{action.action}</strong></p>
      <p>Délai : {action.timing} — Intervenants : {action.responsible}</p>
      <Conditions items={action.conditions} />
      <Sources ids={action.evidence_ids} />
    </div>
  );
}

export function DecisionProposal({ pps, proposal }: { pps: Pps; proposal: PpsProposal }) {
  if (pps.fiche) return <ConciseDecision fiche={pps.fiche} proposal={proposal} />;
  return (
    <>
      <h2>2 — Décision RCP</h2>
      <p><strong>☒ Proposition retenue : {proposal.decision}</strong></p>
      <p>{proposal.rationale}</p>
      <Sources ids={proposal.evidence_ids} />
      <AnswerBlock label="Consultation d’oncogénétique" answer={proposal.referrals.oncogenetics} />
      <AnswerBlock label="Évaluation oncogériatrique" answer={proposal.referrals.oncogeriatrics} />
      <AnswerBlock label="Préservation de la fertilité" answer={proposal.referrals.fertility} />

      <h2>3 — Propositions de traitements</h2>
      {proposal.options.map((option) => (
        <div key={option.rank}>
          <h3>
            {option.rank}. {option.rank === 1 ? "☒ Option retenue" : "☐ Alternative"} — {option.title}
          </h3>
          <p>{option.rationale}</p>
          <Conditions items={option.conditions} />
          <ol>
            {option.steps.map((step, index) => (
              <li key={index}><ActionBlock action={step} /></li>
            ))}
          </ol>
          <Sources ids={option.evidence_ids} />
        </div>
      ))}
      <AnswerBlock label="Prise en charge palliative" answer={proposal.palliative_care} />
      {pps.treatments.rejected.length ? (
        <>
          <h3>Options écartées</h3>
          <ul>
            {pps.treatments.rejected.map((step, index) => (
              <li key={index}>
                <strong>{step.title}</strong>
                {step.items.map((item, itemIndex) => <p key={itemIndex}>{item}</p>)}
                <Sources ids={step.evidence_ids} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {pps.treatments.contraindications.length ? (
        <>
          <h3>Contre-indications relevées</h3>
          <ul>{pps.treatments.contraindications.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </>
      ) : null}
      {pps.treatments.missing_information.length ? (
        <>
          <h3>Informations requises</h3>
          <ul>
            {pps.treatments.missing_information.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </>
      ) : null}
      {pps.treatments.precisions.length ? (
        <>
          <h3>Précisions et incertitudes</h3>
          <ul>{pps.treatments.precisions.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </>
      ) : null}

      <h2>4 — Accompagnement et soins de support</h2>
      <ol>
        {proposal.supportive_care.map((action, index) => (
          <li key={index}><ActionBlock action={action} /></li>
        ))}
      </ol>
      <p>Lieu : {proposal.support_location}</p>
      <p>Statut soins support : {proposal.support_timing}</p>

      <h2>5 — Évaluation sociale et familiale</h2>
      <dl>
        {pps.social.rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd className={row.missing ? "highlight-red" : undefined}>{row.value}</dd>
          </div>
        ))}
      </dl>
      <p>{proposal.social_plan}</p>

      <h2>6 — Cadre de la prise en charge</h2>
      <p><strong>Référentiel</strong> — {pps.framework.reference_standard}</p>
      <AnswerBlock label="Essai clinique" answer={proposal.clinical_trial} />
      <p>Fiche validée : {pps.framework.validated ? "☒ Oui  ☐ Non" : "☐ Oui  ☒ Non"}</p>
      <p>
        Fiche envoyée au médecin traitant :{" "}
        {pps.framework.sent_to_gp ? "☒ Oui  ☐ Non" : "☐ Oui  ☒ Non"}
      </p>
      <p>Participants : {pps.framework.participants}</p>
      <h3>Réévaluation</h3>
      <ActionBlock action={proposal.follow_up} />
    </>
  );
}

function ConciseDecision({ fiche: f, proposal: p }: {
  fiche: ConciseFiche | InstitutionalFiche; proposal: PpsProposal;
}) {
  const answer = (value: "oui" | "non", text: string) =>
    `${value === "oui" ? "[X] Oui  [ ] Non" : "[ ] Oui  [X] Non"} — ${text}`;
  const table = (rows: [string, string][]) => (
    <table><tbody>{rows.map(([label, text]) => (
      <tr key={label}><th scope="row">{label}</th><td>{text}</td></tr>
    ))}</tbody></table>
  );
  const plan = f.contract === "rcpia.pps-fiche.v2" ? (
    <ol>{[...f.steps, ...f.alternatives].map((phase, i) => (
      <li key={i}>
        <strong>{phase.title}</strong>
        <ul>{phase.items.map((item, j) => <li key={j}>{item}</li>)}</ul>
      </li>
    ))}</ol>
  ) : (
    <>
      <ol>{f.steps.map((step, i) => (
        <li key={i}><strong>{step.title}</strong><p>{step.detail}</p></li>
      ))}</ol>
      {f.alternatives.map((option) => (
        <p key={option.rank}><strong>Alternative {option.rank}</strong> — {option.detail}</p>
      ))}
    </>
  );
  const destination = f.contract === "rcpia.pps-fiche.v2"
    ? `[X] ${{ domicile: "Domicile", hospitalisation: "Hospitalisation", autre: "Autre" }[
      f.discharge_destination
    ]} — ${f.discharge}` : f.discharge;
  const references = f.contract === "rcpia.pps-fiche.v2"
    ? `${{ national: "[X] National", international: "[X] International",
      non_documente: "Référentiel non documenté" }[f.reference_scope]} — ${f.references}`
    : f.references;
  return (
    <>
      <h2>2 — Décision RCP</h2>
      {table([
        ["Précisions", f.decision],
        ["Avis oncogénétique", answer(p.referrals.oncogenetics.answer, f.oncogenetics)],
        ["Avis oncogériatrique", answer(p.referrals.oncogeriatrics.answer, f.oncogeriatrics)],
        ["Préservation fertilité", answer(p.referrals.fertility.answer, f.fertility)],
      ])}
      <h2>3 — Propositions de traitements</h2>
      {plan}
      {table([
        ["Prise en charge palliative", answer(p.palliative_care.answer, f.palliative_care)],
        ["Précisions thérapeutiques", f.precisions],
        ["Statut", f.treatment_status],
      ])}
      <h2>4 — Proposition d&apos;accompagnement / Soins de support</h2>
      <p>Proposition d&apos;accompagnement : [X] Oui</p>
      <ul>{f.support.map((step, i) => (
        <li key={i}>
          <strong>{step.title}</strong>
          {"items" in step
            ? <ul>{step.items.map((item, j) => <li key={j}>{item}</li>)}</ul>
            : <> : {step.detail}</>}
        </li>
      ))}</ul>
      {table([["Soignants", f.carers], ["Lieu", f.location], ["Statut soins support", f.support_status]])}
      <h2>5 — Évaluation sociale et familiale</h2>
      {table([
        ["Situation sociale", f.social], ["Proches / Aidants", f.family],
        ["Devenir du patient", destination], ["Commentaire", f.comment],
      ])}
      <h2>6 — Cadre de la prise en charge</h2>
      {table([
        ["Référentiel", references],
        ["Essai clinique", answer(p.clinical_trial.answer, f.clinical_trial)],
      ])}
      <p>Prochaine réévaluation : {f.review}</p>
    </>
  );
}
