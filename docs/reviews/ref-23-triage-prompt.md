# ref-23 triage — which psycho-oncology documents belong in the retrieval corpus

You are triaging 28 French and English psycho-oncology documents for RCPia, deciding which
enter a clinical retrieval corpus and which do not.

**Read the documents. Do not decide from filenames.** The titles are misleading in both
directions: `SFFPO_Fin_de_Vie.md` is 7 KB and may be a leaflet, while
`SFFPO_Determining_optimal_measures_of_health_related_quality_of_life...` is 304 KB and may be
a psychometric validation study with no clinical guidance in it at all. Every verdict must
quote the document.

---

## What RCPia is, so you can judge relevance

RCPia prepares **provisional** documents for a urology RCP (réunion de concertation
pluridisciplinaire — a multidisciplinary tumour board) at CHU Mohammed VI, Marrakech. Several
specialists — urologist, oncologist, radiologist, pathologist — meet to agree a treatment plan
for one patient with prostate or bladder cancer. RCPia drafts a *projet personnalisé de soins*
(PPS) that those clinicians then review, correct, and sign. It never decides anything.

Specialist agents answer clinical questions by retrieving passages from this corpus and citing
them. A passage that cannot be retrieved for a real question is dead weight; worse, a passage
that *is* retrieved for a question it does not answer displaces one that does.

Scope is prostate and bladder cancer only.

---

## The test each document must pass

Relevance is unanswerable as asked — almost anything about cancer is arguably relevant. So
answer a harder, falsifiable question instead. **A document is IN only if you can produce all
three of the following, from the document itself:**

1. **A clinical question** a urology RCP would actually ask about a specific patient.
   Concrete, in the form a clinician would say it. Not "how do we support patients
   psychologically" — that is a topic, not a question.

2. **The routing that would deliver this document for that question** — expressed in the
   vocabulary below. If you cannot name a `clinical_purpose` and an `agent_role` this document
   serves, no agent will ever retrieve it.

3. **A quoted passage** from the document that answers the question. Give the section heading
   it sits under and 1–3 sentences verbatim. If the answer is not in the text, the document
   does not answer the question, whatever its title promises.

Any one of the three missing → **OUT**. Say which one failed.

### Three exclusion patterns to expect

These are guesses from titles, not conclusions. Confirm or overturn each by reading.

- **Workforce documents.** Guidance about clinician burnout, or training psycho-oncologists.
  Real and important, and about the staff rather than the patient in front of the RCP.
- **Service-organisation documents.** Surveys of how psychological care is organised across
  French hospitals, referral-pathway policy, information-transmission protocol. These describe
  how a health system should be arranged, not what to do for this patient.
- **Psychometric or methodological studies.** Papers comparing measurement instruments, without
  a recommendation a clinician could act on.

If a document you expected to be one of these turns out to carry real clinical guidance, say so
plainly — overturning this list is a useful result.

### One thing that is IN scope and easy to wrongly exclude

**End-of-life and palliative content.** RCPia's patient ledger already extracts `pallia10` — the
Pallia 10 palliative-care trigger score — and nothing currently interprets it. Guidance on when
palliative or supportive care should enter the discussion is a decision a urology RCP genuinely
makes. Do not exclude it as "not oncology".

Likewise **distress, anxiety and depression screening** where it carries thresholds or actions:
these are the psycho-oncology equivalent of G8 or MNA, and the RCP records those scores.

---

## Routing vocabulary

Use these exact values. Do not invent new ones — if nothing fits, that is itself a finding and
probably means the document is OUT.

**`clinical_purpose`** — general, screening, diagnosis, pathology, risk_stratification, staging,
modality_selection, therapy_indication, treatment_delivery, treatment_response, follow_up,
genetics, safety, supportive_care

**`agent_roles`** — urology_oncology, medical_oncology, radiation_oncology, radiology, pathology,
genetics_molecular, renal_safety, nuclear_medicine, geriatric_frailty, palliative_supportive

**`disease_setting`** — the corpus is prostate/bladder-specific, and most psycho-oncology
guidance is not. `not_specified` is the expected answer here, paired with a reason:
`general_principle` (true across settings), `spans_settings` (covers several explicitly), or
`not_determinable` (the text does not say). **A document whose only honest reason is
`not_determinable` for everything is a candidate for exclusion** — it means nothing in it is
addressed to a decidable clinical situation.

---

## The documents

All in `C:\Users\hzmarrou\OneDrive\python\learning\RCPia\data\ref-23\`, largest first.
Sizes are the markdown, not the PDF.

```
411 KB  SFFPO_Document_d_orientation_pancanadien_sur_l_organisation_et_la_prestation_des_services_de_survie_et_sur.md
304 KB  SFFPO_Determining_optimal_measures_of_health_related_quality_of_life_anxiety_and_depression.md
188 KB  SFFPO_A_pan_canadian_practice_guideline_screening_assessment_and_care_of_psychosocial_distress_depression.md
125 KB  SFFPO_Clinical_Practice_Guidelines_in_Oncology_Distress_Management.md
111 KB  SFFPO_Deuil.md
 98 KB  SFFPO_Parents_Cancer_Enfants.md
 84 KB  SFFPO_Apres_Cancer.md
 84 KB  SFFPO_Enquete_sur_l_organisation_de_la_prise_en_charge_psychologique_au_sein_des_etablissements_de_sante_a.md
 83 KB  SFFPO_Standards_of_Psychosocial_Services_for_Persons_with_Cancer_and_their_Families.md
 74 KB  SFFPO_Souffrance_Psychique.md
 67 KB  SFFPO_Anxiete.md
 50 KB  SFFPO_Epuisement_Professionnel_Soignants.md
 50 KB  SFFPO_Orientation_Psychologue_Psychiatre.md
 47 KB  SFFPO_Troubles_Psychotiques.md
 39 KB  SFFPO_Depression.md
 37 KB  SFFPO_Confusion_Mentale (1).md
 37 KB  SFFPO_Confusion_Mentale.md
 34 KB  SFFPO_Etat_des_lieux_des_organisations_de_la_prise_en_charge_psychologique_dans_les_reseaux_et_recommandat.md
 30 KB  SFFPO_Refus_de_Soins.md
 30 KB  SFFPO_Souffrance_Refractaire_Fin_Vie.md
 28 KB  SFFPO_Transmission_Informations.md
 25 KB  SFFPO_Orienter_Accompagnement_Psychologique.md
 13 KB  SFFPO_Syndrome_d_epuisement_professionnel_des_soignants.md
 13 KB  SFFPO_Prise_en_charge_psychologique_apres_cancer_presentation.md
 12 KB  SFFPO_Comment_accompagner_vers_prise_en_charge_psychologique.md
 12 KB  SFFPO_Formation_Psycho_Oncologie.md
 12 KB  SFFPO_Sedation_Soins_Psychiques.md
  7 KB  SFFPO_Fin_de_Vie.md
```

`SFFPO_Confusion_Mentale.md` and `SFFPO_Confusion_Mentale (1).md` look like the same document.
Diff them, say which is complete, and treat the other as a duplicate.

Two documents are English (`A_pan_canadian_practice_guideline...`, `Clinical_Practice_Guidelines_in_Oncology_Distress_Management`,
`Standards_of_Psychosocial_Services...`, `Determining_optimal_measures...`); the rest are French.
The corpus holds both languages, so language is not a criterion.

---

## Output

One block per document, in the order given. Then the summary tables.

```
### SFFPO_Depression.md
VERDICT: IN | OUT | UNCERTAIN
QUESTION:  "Ce patient de 78 ans sous hormonothérapie a un score de dépistage de dépression
            élevé — faut-il différer la décision thérapeutique ?"
ROUTING:   clinical_purpose=[supportive_care, safety]
           agent_roles=[palliative_supportive, geriatric_frailty]
           disease_setting=not_specified (general_principle)
QUOTE:     § "Dépistage et évaluation" — "..."
WHY:       one or two sentences. If OUT, name which of the three tests failed.
```

Rules for the verdict:

- **UNCERTAIN is allowed and encouraged** where the call is genuinely clinical rather than
  editorial — a urologist will settle it in ten minutes, and a wrong confident answer costs
  more than a flagged one. Say precisely what you would ask them.
- Do not pad the IN list. A corpus that retrieves something plausible for every question is
  worse than one that retrieves nothing for some, because the clinician cannot tell the two
  apart.
- If a large document is mostly out of scope but contains one genuinely useful chapter, mark it
  **PARTIAL**, name the sections worth keeping, and say what fraction that is. Chunk-level
  inclusion is possible; we do not have to take a document whole.

### Then

**Table 1 — verdicts.** document | verdict | primary clinical_purpose | one-line reason.

**Table 2 — what this adds that the corpus lacks.** The corpus currently holds EAU, AFU, NCCN
and SIOG urology guidance only. For each IN document, one line: what clinical question becomes
answerable that is not answerable today. If a document duplicates guidance SIOG already gives,
say so — SIOG covers geriatric assessment and frailty, which overlaps this material.

**Table 3 — the honest count.** How many IN, PARTIAL, OUT, UNCERTAIN, and your estimate of
chunks each IN document would contribute at roughly 400–800 tokens per chunk.

---

## What not to do

- Do not read only the first pages of a long document. The clinical content in French position
  papers is often after a long preamble on the working group's composition.
- Do not judge a document by its publisher's prestige.
- Do not recommend ingesting everything on the grounds that retrieval filters will sort it out.
  Filters route; they do not judge. Wrong material in the corpus is wrong material retrieved.
- Do not write code, build anything, or modify any file outside your report.
