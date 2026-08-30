# Coverage gaps: the decision we cannot make ourselves

**Date**: 30 August 2026
**Status**: blocked on a clinical judgement. Nothing in the code can settle it.
**Audience**: whoever defines how the tumour board divides work between specialities.

---

## 1. What a coverage gap is

RCPia has two software agents write a provisional opinion on the same patient record,
separately. One is the urology-oncology agent, the other is medical oncology. Neither can see
the other's output — that isolation is enforced in the schema, and it is what makes any
agreement between them worth something.

A third agent, the synthesis, reads both and reports three things:

- **Convergence** — both agents reached the same point independently.
- **Divergence** — they disagree.
- **Coverage gap** — one raised a point and the other never mentioned it.

The third category exists because of a specific failure. The medical oncology agent wrote *"do
not substitute carboplatin for cisplatin"*. The urology agent never mentioned carboplatin at
all. The synthesis recorded this as a **convergence attributed to both agents** — a consensus
that had never happened, printed on a document a tumour board would read as agreement.

Silence was being read as assent. `coverage_gaps` is where silence goes now, and the fabricated
convergence is gone: measured across fifteen dossiers, **zero fabricated attributions in ninety
convergences**, against one in ninety-two before.

## 2. The problem

**Most silences are not gaps.**

The urology agent wrote *"if surgery proceeds, prefer a simple Bricker conduit over an
orthotopic neobladder"*. The medical oncology agent said nothing about it. That is not an
omission — the type of urinary diversion is a surgical decision, and a chemotherapy specialist
having no view on it is the division of labour working correctly.

Printing that silence costs the board attention and buries the silences that matter.

**The measurement.** Across fifteen dossiers the panel produced **34 coverage gaps**. Reading
them, roughly three quarters are of the second kind: one speciality quietly not commenting on
the other's territory. Only a handful are of the carboplatin kind, where the silence sits inside
the speaking agent's own plan.

**Why it cannot wait.** Five more specialities are ready to be wired — radiation oncology,
radiology, pathology, geriatrics, nutrition, palliative care. With two agents, any point has
exactly one other speciality that can be silent on it. **With seven, it has six.** The same rule
that produces 34 entries today would produce several hundred, and the carboplatin case would be
lost among them. Adding agents before this is settled makes the board's document worse, not
better.

---

## 3. What we tried, and why it failed

The attractive idea was to avoid asking a clinician anything.

**The proposed rule:** *an agent may address any point it can ground in evidence it is allowed
to read.* The corpus already routes guideline passages by speciality — of the eligible bladder
passages, 1,151 admit the urology agent and 1,336 admit medical oncology, with 203 admitting
only medical oncology. So the reasoning went: if an agent held no evidence on a subject, its
silence is expected and should not be printed. No clinical boundary needed; the corpus would
answer for us.

**It was tested before being built.** For each of the 34 gaps: was the silent agent handed a
passage that speaks to this point? Each agent's bundle records exactly what it read, so this is
a fact about the run rather than a judgement.

**Result: 35% suppressed, 65% still reported — and the wrong ones survived.**

The medical oncology agent's silence on urinary diversion, the clearest "not my department" case
in the whole set, came out as a **reportable gap**. Its bundle demonstrably contained passages
mentioning `bricker`, `cystectomie`, `dérivation` and `entérocystoplastie`.

**Why.** Guideline documents are not scoped by speciality. The AUA MIBC guideline covers
surgery, chemotherapy, diversion and follow-up in one document. When the medical oncology agent
retrieves passages about neoadjuvant chemotherapy, those passages discuss cystectomy constantly
— cystectomy is the operation the chemotherapy precedes. So *"could it have cited on this?"* is
almost always yes, for both agents, on almost any bladder topic.

There is also a circularity worth recording: every passage in an agent's bundle already admits
that agent by construction. Asking *"was it allowed to read this?"* of its own bundle can only
answer yes.

**Conclusion.** The rule remains sound as a *speech* rule — an agent should not assert what it
cannot ground. It does **not** answer the gap question, because grounding is available to both
agents on shared documents. The corpus cannot settle this, because the guidelines themselves do
not divide the way a tumour board divides.

---

## 4. What we need answered

The rule will be written per **(silent speciality × subject)**, and the same question is asked
of each group:

> **Is an agent of that speciality expected to hold a view on this subject, given the real remit
> of that speciality?**

- **YES** → the silence is a genuine gap; the board should seek that opinion.
- **NO** → it is the normal division of labour; do not print it.
- **IT DEPENDS** → say on what. An ambiguous rule applied to every future patient is worse than
  a rule that admits it needs a second look.

**Decide per group, never phrase by phrase.** A rule derived from thirty separate judgements
cannot be applied to the thirty-first patient, whose wording will differ. The points listed
under each group are the material for the decision, not decisions in themselves.

**No doctor has examined these dossiers.** None of this is a colleague's oversight. The question
is what a *software agent* of each speciality should be required to cover.

---

## 5. The seven groups


### 1. Traitement systémique — cytotoxiques et immunothérapie — Oncologie urologique silent

*Oncologie urologique said nothing.* 18 gap(s) across 11 dossier(s) (01, 02, 3, 4, 5, 8, 10, 11, 13, 14, 15), raised by Oncologie médicale.

> **Is an agent of oncologie urologique expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 18 point(s) concerned:

- Rejet explicite du carboplatine comme substitut néoadjuvant au cisplatine.  *(dossier 01)*
- Rejet explicite de l’immunothérapie néoadjuvante hors essai clinique.  *(dossier 01)*
- Le carboplatine ne doit pas remplacer le cisplatine en chimiothérapie néoadjuvante périopératoire.  *(dossier 02)*
- L'immunothérapie néoadjuvante hors essai clinique n'est pas retenue comme standard.  *(dossier 02)*
- Absence d'indication à un traitement anticancéreux systémique dans la situation actuelle.  *(dossier 10)*
- Gemcitabine endovésicale comme alternative à la mitomycine C, avec caractère hors AMM en France selon le passage fourni.  *(dossier 10)*
- La chimio-ablation ne devrait être proposée que dans le cadre d'un essai clinique.  *(dossier 10)*
- L'oncologie médicale détaille les critères non rénaux nécessaires pour apprécier l'éligibilité au cisplatine, notamment l'audition, la neuropathie et la fonction cardiaque ; l'oncologie urologique ne les développe pas.  *(dossier 11)*
- Le carboplatine ne doit pas être substitué au cisplatine en néoadjuvant.  *(dossier 13)*
- Une stratégie adjuvante peut être discutée après cystectomie en cas de pT3-pT4 et/ou pN+ selon l’exposition antérieure au cisplatine.  *(dossier 13)*
- Réévaluation explicite du traitement adjuvant selon le stade définitif et l'exposition néoadjuvante.  *(dossier 14)*
- Absence d'indication actuelle à une chimiothérapie ou une immunothérapie systémique anticancéreuse.  *(dossier 15)*
- La fenêtre d'une instillation postopératoire précoce après la RTUV de septembre 2025 est dépassée.  *(dossier 15)*
- La mitomycine nécessite des modalités spécifiques, notamment une alcalinisation urinaire et une durée de contact suffisante.  *(dossier 15)*
- Exclusion explicite d'une chimiothérapie ou d'une intensification systémique par nouvelle hormonothérapie.  *(dossier 3)*
- Absence de bénéfice d'un traitement systémique néoadjuvant ou adjuvant autour d'une prostatectomie en l'absence d'atteinte ganglionnaire démontrée.  *(dossier 4)*
- L'oncologie médicale exclut explicitement une hormonothérapie néoadjuvante avant prostatectomie hors essai ; l'avis d'oncologie urologique ne traite pas explicitement cette question.  *(dossier 5)*
- Préférence explicite pour le docétaxel si la résistance à la castration est confirmée et qu’aucune chimiothérapie n’a été reçue.  *(dossier 8)*


### 2. Chirurgie et dérivation urinaire — Oncologie médicale silent

*Oncologie médicale said nothing.* 7 gap(s) across 5 dossier(s) (01, 02, 11, 13, 14), raised by Oncologie urologique.

> **Is an agent of oncologie médicale expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 7 point(s) concerned:

- Choix préférentiel d’une dérivation non continente de type Bricker si une chirurgie est retenue.  *(dossier 01)*
- En cas de cystectomie, une dérivation simple de type Bricker paraît préférable à une entérocystoplastie.  *(dossier 02)*
- La cystectomie partielle n'est pas adaptée aux caractéristiques tumorales rapportées.  *(dossier 02)*
- L'oncologie urologique propose explicitement une résection endoscopique en plusieurs temps si une exérèse complète en une séance est impossible ; l'oncologie médicale ne précise pas cette modalité.  *(dossier 11)*
- La cystectomie partielle est inadaptée aux caractéristiques tumorales rapportées.  *(dossier 13)*
- Le choix d’une éventuelle dérivation urinaire doit être individualisé après clarification de l’atteinte prostatique ou urétrale et évaluation fonctionnelle.  *(dossier 13)*
- Choix préférentiel d'un conduit iléal de type Bricker plutôt qu'une néovessie, sous réserve du bilan.  *(dossier 14)*


### 3. Traitement systémique — hormonothérapie et santé osseuse — Oncologie urologique silent

*Oncologie urologique said nothing.* 5 gap(s) across 4 dossier(s) (4, 7, 8, 12), raised by Oncologie médicale.

> **Is an agent of oncologie urologique expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 5 point(s) concerned:

- Préférence éventuelle pour un antagoniste de la GnRH afin d’éviter une flambée tumorale en présence d’une lésion rachidienne menaçante.  *(dossier 12)*
- Absence d’indication systématique, sur les seules données disponibles, d’un antirésorptif à dose de prévention des événements osseux métastatiques dès la phase hormonosensible.  *(dossier 12)*
- Bilan métabolique, testostéronémie chiffrée et évaluation osseuse avant une éventuelle suppression androgénique.  *(dossier 4)*
- Dosage des phosphatases alcalines dans le bilan préalable à l’hormonothérapie.  *(dossier 7)*
- Possibilité d’un traitement radiopharmaceutique à tropisme osseux dans une séquence ultérieure.  *(dossier 8)*


### 4. Stadification, imagerie et anatomopathologie — Oncologie médicale silent

*Oncologie médicale said nothing.* 1 gap(s) across 1 dossier(s) (01), raised by Oncologie urologique.

> **Is an agent of oncologie médicale expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 1 point(s) concerned:

- Décision thérapeutique définitive explicitement retenue en attente du cTNM, de la résécabilité et de l’opérabilité.  *(dossier 01)*


### 5. Soins de support, nutrition, gériatrie — Oncologie médicale silent

*Oncologie médicale said nothing.* 1 gap(s) across 1 dossier(s) (11), raised by Oncologie urologique.

> **Is an agent of oncologie médicale expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 1 point(s) concerned:

- L'oncologie urologique demande explicitement un accompagnement au sevrage tabagique ; l'oncologie médicale ne l'aborde pas.  *(dossier 11)*


### 6. Soins de support, nutrition, gériatrie — Oncologie urologique silent

*Oncologie urologique said nothing.* 1 gap(s) across 1 dossier(s) (14), raised by Oncologie médicale.

> **Is an agent of oncologie urologique expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 1 point(s) concerned:

- Mise en place explicite de soins de support précoces avec aide au sevrage tabagique et clarification du Pallia 10 ≥ 4.  *(dossier 14)*


### 7. Radiothérapie — Oncologie urologique silent

*Oncologie urologique said nothing.* 1 gap(s) across 1 dossier(s) (7), raised by Oncologie médicale.

> **Is an agent of oncologie urologique expected to hold a view on this subject?**

> ☐ YES, report it &nbsp; ☐ NO, do not print it &nbsp; ☐ It depends: _________________________

The 1 point(s) concerned:

- Discussion explicite de l’irradiation ganglionnaire pelvienne dans le schéma de radiothérapie.  *(dossier 7)*


---

## 6. If the groups are the wrong shape

The grouping is ours, not medicine's. If a subject is drawn wrongly — too broad, too narrow, or
cutting across a real boundary — say so and we will regroup before anyone answers.

**One group is already suspect.** *Systemic therapy — cytotoxics and immunotherapy* holds 18 of
the 34 gaps, and it is not uniform. Some of it is squarely the medical oncologist's own ground:
mitomycin instillation technique, docetaxel sequencing. But some sits **inside the plan the
urology agent is itself proposing** — if that agent recommends neoadjuvant chemotherapy followed
by cystectomy, its silence on whether carboplatin may substitute is a hole in its own proposal.

A flat NO on that group would suppress the carboplatin case this whole category exists for. A
flat YES prints eighteen lines on every bladder patient. It may need splitting along a line such
as: *does this point change what the silent agent is proposing to do?*

---

## 7. What happens with the answers

Each YES becomes a rule: this subject is inside that speciality's remit, so its silence is
reportable. Each NO becomes the opposite. The filter runs before the fiche is rendered, so the
board sees only the silences worth chasing.

The rules are written per speciality and per subject, so they survive the arrival of the other
five specialities. Those will add new cells to the table; they will not invalidate the answers
given here.

**Time required:** roughly twenty minutes for seven questions. Two of them cover 25 of the 34
gaps.

---

## 8. An interactive version

The same seven questions, with the evidence folded away and the answers copyable, are published
at the artifact link held by the project owner. Either form works; the answers are what matter.
