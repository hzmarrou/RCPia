import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DecisionProposal } from "../app/cases/[caseId]/pps/decision-proposal";
import {
  AI_ORIGIN_TITLE, type ConciseFiche, type InstitutionalFiche,
  type Pps, type PpsProposal, type ProposedAction,
} from "../lib/pps";

const ACTION: ProposedAction = {
  action: "Action synthétique A",
  timing: "Après le bilan requis",
  responsible: "Équipe de spécialité",
  conditions: ["Condition documentée, sans la supposer remplie."],
  evidence_ids: ["fixture-source"],
};

function fixture(): { pps: Pps; proposal: PpsProposal } {
  const yes = {
    answer: "oui" as const, rationale: "Motif de la proposition.",
    conditions: ["Confirmer la donnée manquante."], evidence_ids: ["fixture-source"],
  };
  const proposal: PpsProposal = {
    contract: "rcpia.pps-proposal.v1",
    decision: "Retenir la stratégie A sous condition.",
    intent: "bilan_prealable",
    rationale: "Comparaison des options documentées.",
    evidence_ids: ["fixture-source"],
    referrals: {
      oncogenetics: yes, oncogeriatrics: { ...yes, answer: "non" }, fertility: yes,
    },
    options: [
      {
        rank: 1, title: "Stratégie A", rationale: "Motif A",
        conditions: ["Condition A"], steps: [ACTION], evidence_ids: ["fixture-source"],
      },
      {
        rank: 2, title: "Stratégie B", rationale: "Motif B",
        conditions: ["Si A ne convient pas."], steps: [{ ...ACTION, action: "Action B" }],
        evidence_ids: ["fixture-source"],
      },
    ],
    palliative_care: { ...yes, answer: "non" },
    supportive_care: [{ ...ACTION, action: "Accompagnement proposé" }],
    support_location: "Consultation proposée", support_timing: "Dès maintenant",
    social_plan: "Organiser un entretien social sans présumer de l’entourage.",
    carers_plan: "Identifier les aidants.", discharge_plan: "Retour selon évaluation.",
    clinical_trial: { ...yes, answer: "non" },
    follow_up: { ...ACTION, action: "Réévaluation programmée" },
    missing_information: ["Document nécessaire"],
    uncertainties: ["Incertitude conservée"],
  };
  const pps: Pps = {
    case_id: "fixture", case_reference: "CASE-1", cancer_type: "bladder",
    generated_on: "2026-09-11", ledger_version: 1, superseded_by: null,
    agent_run_ids: ["fixture-run"], generator_version: "2.0", banner: "Provisoire.",
    proposal,
    synthesis: { patient: "Cas synthétique", diagnosis: null, comorbidities: null, question: "Q" },
    decision: { taken: false, statement: proposal.decision, referrals: [] },
    treatments: {
      steps: [], precisions: ["Incertitude conservée", "Divergence clinique maintenue"],
      contraindications: ["Contre-indication conservée"],
      missing_information: ["Document nécessaire"], rejected: [], status: "Proposition", withheld: false,
    },
    support: { items: [], carers: null, location: null, status: "Proposition" },
    social: { rows: [{ label: "Entourage", value: "Non documenté", missing: true }], comment: null },
    framework: {
      reference_standard: "Référentiel fourni", clinical_trial: "non", clinical_trial_note: "Motif",
      validated: false, validated_note: "Non validée", sent_to_gp: false, sent_to_gp_note: "Non envoyée",
      participants: "Spécialités consultées", review: "Après bilan",
    },
    missing: [], evidence: [], appendices: [],
  };
  return { pps, proposal };
}

describe("decision-led PPS presentation", () => {
  it("shows the preferred decision, ranked alternative and concrete sequence", () => {
    const html = renderToStaticMarkup(createElement(DecisionProposal, fixture()));
    expect(html).toContain("☒ Proposition retenue : Retenir la stratégie A sous condition.");
    expect(html).toContain("1. ☒ Option retenue");
    expect(html).toContain("2. ☐ Alternative");
    expect(html.indexOf("Stratégie A")).toBeLessThan(html.indexOf("Stratégie B"));
    expect(html).toContain("Après le bilan requis");
    expect(html).toContain("Équipe de spécialité");
    expect(html).toContain("Si A ne convient pas.");
    expect(html).not.toMatch(/à déterminer|non traité par tous|en attente de validation RCP/i);
  });

  it("uses proposed answers without claiming clinician approval or communication", () => {
    const html = renderToStaticMarkup(createElement(DecisionProposal, fixture()));
    expect(html).toContain("☒ Oui  ☐ Non");
    expect(html).toContain("☐ Oui  ☒ Non");
    expect(html).toContain("Fiche validée : ☐ Oui  ☒ Non");
    expect(html).toContain("Fiche envoyée au médecin traitant : ☐ Oui  ☒ Non");
    expect(html).not.toContain("Décision prise");
  });

  it("retains material caveats, supporting sections and source references", () => {
    const html = renderToStaticMarkup(createElement(DecisionProposal, fixture()));
    for (const text of [
      "Informations requises", "Document nécessaire", "Précisions et incertitudes",
      "Divergence clinique maintenue", "Contre-indication conservée", "Condition A",
      "Confirmer la donnée manquante.", "fixture-source", "Accompagnement proposé",
      "Réévaluation programmée", "Entourage", "Non documenté",
    ]) expect(html).toContain(text);
    expect(html.indexOf("Informations requises")).toBeGreaterThan(html.indexOf("Option retenue"));
  });

  it("escapes clinical text rather than treating it as document markup", () => {
    const props = fixture();
    props.proposal.decision = '<script>alert("fixture")</script>';
    const html = renderToStaticMarkup(createElement(DecisionProposal, props));
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("keeps one exact removable origin title", () => {
    expect(AI_ORIGIN_TITLE).toBe(
      "FICHE RCP — CREATED BY AN AI AGENT — PROPOSITION DE PRISE EN CHARGE (PPS)",
    );
  });

  it("uses concise wording on the normal page without leaking the detailed substructure", () => {
    const props = fixture();
    const fiche: ConciseFiche = {
      contract: "rcpia.pps-fiche.v1",
      decision: "Stratégie A après bilan ; B si A est contre-indiquée.",
      oncogenetics: "Évaluation proposée.", oncogeriatrics: "Non retenue.", fertility: "Consulter.",
      steps: [{ title: "Bilan", detail: "Vérifier la condition avant traitement." }],
      alternatives: [{ rank: 2, detail: "B si la condition A n'est pas satisfaite." }],
      precisions: "Condition déterminante conservée.", palliative_care: "Avis selon symptômes.",
      treatment_status: "Après bilan.", support: [{ title: "Nutrition", detail: "Évaluer les apports." }],
      carers: "Équipe soignante.", location: "Consultation.", support_status: "Dès maintenant.",
      social: "Situation documentée.", family: "Vérifier les aides.", discharge: "Selon autonomie.",
      comment: "Aides adaptées.", references: "Guides fournis.", clinical_trial: "Recherche proposée.",
      review: "Après bilan.", covered_option_ranks: [1, 2],
    };
    props.pps.fiche = fiche;
    const html = renderToStaticMarkup(createElement(DecisionProposal, props));
    expect(html).toContain(fiche.decision);
    expect(html).toContain("Condition déterminante conservée.");
    expect(html).toContain("Prochaine réévaluation : Après bilan.");
    expect(html).toContain("[X] Oui");
    expect(html).not.toMatch(/Délai :|Intervenants :|Informations requises|Fiche validée|☒|☐/);
    expect(html).not.toContain("Motif de la proposition.");
  });

  it("renders institutional phases with bullet items and conditional continuation steps", () => {
    const props = fixture();
    const f: InstitutionalFiche = {
      contract: "rcpia.pps-fiche.v2",
      diagnosis: "Diagnostic narratif.", comorbidities: "Antécédents narratifs.",
      question: "Question commune.",
      decision: "Stratégie A si le bilan le permet.",
      oncogenetics: "Avis motivé.", oncogeriatrics: "Avis motivé.", fertility: "Avis motivé.",
      steps: [{ title: "Bilan préalable", items: ["Contrôler la fonction rénale."] }],
      alternatives: [{ rank: 2, title: "B si A impossible", items: ["Recours conditionnel à B."] }],
      precisions: "Précisions brèves.", palliative_care: "Soins selon les symptômes.",
      treatment_status: "Après bilan.", support: [{ title: "Nutrition", items: ["Évaluer les apports."] }],
      carers: "Équipe proposée.", location: "Consultation.", support_status: "Dès maintenant.",
      social: "Patient marié.", family: "Organisation selon les aidants disponibles.",
      discharge_destination: "domicile", discharge: "Après stabilisation si autonomie compatible.",
      comment: "Consignes de recours.", reference_scope: "national",
      references: "Recommandations TVIM (AFU), compléments internationaux.",
      clinical_trial: "Selon critères.", review: "après bilan.",
      covered_option_ranks: [1, 2],
    };
    props.pps.fiche = f;
    const html = renderToStaticMarkup(createElement(DecisionProposal, props));
    expect(html).toContain("<strong>Bilan préalable</strong><ul><li>Contrôler");
    expect(html).toContain("<strong>B si A impossible</strong><ul><li>Recours");
    expect(html).not.toContain("Alternative 2");
    expect(html).toContain("[X] Domicile");
    expect(html).toContain("Après stabilisation si autonomie compatible.");
    expect(html).toContain("[X] National");
    expect(html).toContain("Prochaine réévaluation : après bilan.");
  });
});
