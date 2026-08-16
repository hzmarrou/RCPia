# PPS Output Format

Derived from the human and machine PPS examples in `examples/`. This is the format the
generator in plan Phase 8.2 and Phase 12.1 must produce.

## Language

**French.** Dossiers arrive in French, the reference PPS documents are French, and the
readers are the RCP participants. Agent output, PPS sections, and rationale appendices are
all written in French. Only code, identifiers, and developer documentation stay in English.

## Document title and header

```text
# FICHE RCP — PROPOSITION DE PRISE EN CHARGE (PPS)
```

followed by a header table with three cells: `Statut`, `Date RCP`, `Fiche validée`.

RCPia generates **provisional** documents, so it must not emit `Statut : Décision prise`
or `Fiche validée : OUI`. The generated header states that the document is a provisional
proposal awaiting RCP validation.

## The six sections

| # | Heading | Content |
|---|---|---|
| 1 | `SYNTHÈSE ET QUESTION POSÉE` | `Patient` (sex, age — no name), `Diagnostic` (histology, TNM, extension), `Comorbidités`, `Question posée` |
| 2 | `DÉCISION RCP` | decision checkbox; `Avis oncogénétique`, `Avis oncogériatrique`, `Préservation fertilité` — each Oui/Non plus a justification |
| 3 | `PROPOSITIONS DE TRAITEMENTS` | numbered treatment phases (optimization, staging work-up, chemotherapy, surgery, radiochemotherapy, palliative), then `Précisions thérapeutiques` and `Statut` |
| 4 | `PROPOSITION D'ACCOMPAGNEMENT / SOINS DE SUPPORT` | bullet blocks: transfusion, nutrition, douleur, soutien psychologique, nursing, kinésithérapie/APA, accompagnement social; then `Soignants`, `Lieu`, `Statut soins support` |
| 5 | `ÉVALUATION SOCIALE ET FAMILIALE` | `Situation sociale`, `Proches / Aidants`, `Devenir du patient`, `Commentaire` |
| 6 | `CADRE DE LA PRISE EN CHARGE` | `Référentiel`, `Essai clinique`, `Fiche RCP validée`, `Fiche envoyée médecin traitant`, participants list, RCP date and next review |

Sections are numbered with `# N — TITLE`. Structured fields are rendered as tables;
treatment plans and support care as bulleted or numbered lists. Checkbox fields use
`[X] Oui  [ ] Non`.

## Style observed in the examples

- Dense and directive. Drug protocols carry dose, route, day, cycle length, and cycle
  count (`Cisplatine 100 mg/m² J1 — cycles 28j — 4 cycles`).
- Decisions carry explicit thresholds (`Chimiothérapie initiée dès : Hb ≥ 10 g/dL,
  albumine > 28 g/L, clairance créatinine > 60 mL/min`).
- Alternatives and contraindications are stated inline, with the reason
  (`néovessie contre-indiquée — atteinte urétrale prostatique`).
- Uncertainty is marked in the diagnosis itself (`cT4a N1 M1a (à confirmer)`).
- Absent data is written `RAS` in dossiers.

## What RCPia adds

The PPS itself stays this concise. RCPia additionally produces, outside the six sections:

- a provisional-status banner,
- a missing-information report (red) and uncertain/conflicting fields (amber),
- one rationale appendix per invoked agent, maximum one A4 page each,
- the evidence register and human-review routing.

## Reference examples

- `examples/dossier-02-bladder.md` — input dossier (bladder, female, 85)
- `examples/pps-example-01.md` — PPS for dossier 01
- `examples/pps-example-02.md` — PPS for dossier 01, second variant

One example is human-written and the other machine-generated. **Which is which is not yet
confirmed** — pending the treating clinician's answer. Until then, treat both as valid
targets for the output format, and do not treat either as the quality benchmark.

## Dossier input fields to extract

From `examples/dossier-02-bladder.md`, the extraction pipeline (plan Phase 5) must handle:

- identity and social: dossier number, sex, age, profession, marital status, children,
  insurance coverage
- history: medical, surgical, family, toxic habits, allergies
- presentation: circumstances of discovery, history of the illness, prior RCP decisions
- functional and geriatric scores: Pallia 10, G8 (if ≥ 75), IIEF5, IPSS, MNA, ADL, OMS
- cardiac assessment, clinical examination findings
- disease: organ, phase, diagnosis date, cystoscopy, imaging reports (TDM TAP,
  scintigraphie osseuse, uroscanner, IRM prostatique multiparamétrique)
- biology: PSA total/libre, testostérone, NFS, urée, créatinine, DFG, ionogramme, CRP,
  TP, groupage, ECBU, liver enzymes, LDH, CPK
- pathology: prior anatomopathology synthesis, residual tumour, margins, new examination
  with date, type, localization, histological type, pTNM, grade, muscle invasion, CIS,
  immunohistochemistry, molecular biology

`RAS` means the field was explicitly reported as unremarkable/absent — which is different
from a field being missing from the dossier, and the ledger must distinguish the two.
