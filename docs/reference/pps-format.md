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

One example is human-written and the other machine-generated. **Which is which is not
confirmed**, and the clinician who would know has not been asked. Treat both as valid
targets for the output *format*, and neither as a quality benchmark for content.

### Inferred, not confirmed: 01 reads as human, 02 as machine

Working conclusion for Phase 8, roughly 80% confidence, from the documents alone:

- **01 contains a practice error of a kind a model does not make.** Its nutrition plan is
  *albumine en IV* for repletion, which does not work and which guidelines say not to do —
  and which is still prescribed in practice. 02 gives the oral-supplement-then-enteral
  ladder with named products and targets, which is what a guideline-trained generator
  writes.
- **01 has a dangling reference that 02 resolves.** 01's *Précisions thérapeutiques* cites
  5-FU-Cisplatine, a protocol absent from 01's own treatment list. It is item 3 in 02.
  That is a human pasting a block from a template; a model would have reconciled it.
- **01 skips the workup its own diagnosis demands** (`M1a (à confirmer)`, then straight to
  cystoprostatectomy), where 02 enumerates PET-TDM, IRM and a full IHC panel. A room that
  has decided records the decision; a generator enumerates the branches.
- **02 covers every branch it is not taking**, with RECIST thresholds, RT fractionation and
  a fallback regimen. That completeness is machine-shaped.
- 01 has dropped spaces (`directeaprès`, `grade4`), and renders the same sentence twice
  with different spacing — retyping, not one conversion pass.

Both files carry the same PDF/Word conversion damage (02 splits `Extra-` / `hospitalier`
across two tables at a page break), so that discriminates nothing.

**What Phase 8 does with this.** Take the *format* from the union of both, since each drops
sections the other keeps — 02 lost the decision précision in §2, 01 renders §6 as loose
bold lines rather than a table. Take the *register* from 01: terse and decision-first, which
is what this RCP actually records. Take content standards from neither. If the inference is
backwards, only the register call changes.

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
