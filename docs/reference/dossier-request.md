# What RCPia needs next: more dossiers

Written 17 August 2026. The section in French can be forwarded to the clinician as-is.

## Why this matters more than more code

RCPia's extraction is **deterministic label matching**, not a language model: it reads
`Créatinine : 17.33` by recognising the label before the colon. That is the right design
here — every fact quotes the exact line it came from, and a rule that matched a line can say
precisely which line.

The cost of that design is that it only recognises labels it has seen. Today the label
catalogue in `apps/api/rcpia/cases/fact_schema.py` was derived from **one** dossier. If a
real dossier writes `Taux de PSA` where the catalogue expects `PSA total`, the field comes
back **missing** — red in the ledger, listed as a gap, absent from the agent's bundle.

That failure is silent and looks exactly like a genuinely incomplete dossier. No test can
catch it, because the test would have to already know the label. Only a real dossier can.

## The ask, in priority order

### 1. Two or three prostate dossiers — the real gap

Prostate is half of v1's clinical scope and there are **zero** real examples. Every
prostate-specific field was inferred from the bladder dossier's shape:

| Field | Label the catalogue expects |
|---|---|
| `psa_total` | `PSA totale`, `PSA total` |
| `psa_free` | `PSA libre` |
| `testosterone` | `Testostérone` |
| `iief5` | `État de l'érection (score IIEF5)` |
| `prostate_mri` | `IRM prostatique multiparamétrique` |

`psa_total` and `prostate_mri` are *required* for a prostate case, so getting either label
wrong blocks or degrades every prostate analysis. One real dossier settles all five in
minutes.

### 2. Two more bladder dossiers, from different clinicians if possible

One dossier proves the parser works on that dossier. Different people fill the same form
differently — abbreviations, section order, whether scores are written `OMS 1` or
`OMS : 1`. RCPia already handles both of those, because the first dossier happened to
contain both; the point is that nobody knows what else varies.

### 3. At least one dossier **with the real PPS the RCP produced from it**

This is the only genuine accuracy signal available. Everything so far has validated
*format* against the reference pair, never *content*. With a dossier and its real fiche,
RCPia can be run and its draft compared against what the room actually decided — where it
agreed, where it missed something, where it raised something the RCP did not.

### 4. One dossier already discussed at a previous RCP

There is a `previous_rcp_decisions` field that has never been seen populated. A case
arriving with a prior decision is a different clinical question: the agent should be
reasoning about a change of plan, not a first plan, and nothing has tested that.

### 5. Format

**PDF or Word, as originally produced** — not converted to text or Markdown. The pipeline
parses PDFs through LlamaParse, so an original exercises the real path, including the
table and layout handling that converted files hide. The existing reference dossier is
Markdown only because it arrived that way.

Simulated or de-identified patient names, as before. Drop them in `data/` (gitignored) or
send them directly.

### What is *not* needed

More dossiers of the same shape as the existing one. They would add little: the value is
in variation.

---

## Version française (à transmettre au médecin)

RCPia lit les dossiers en reconnaissant les **intitulés** des champs — par exemple
`Créatinine :` ou `PSA total :`. C'est ce qui lui permet de citer la ligne exacte d'où
vient chaque donnée. En contrepartie, il ne reconnaît que les intitulés qu'il a déjà
rencontrés, et il n'en a rencontré qu'un seul dossier jusqu'ici.

Si un dossier écrit `Taux de PSA` là où RCPia attend `PSA total`, le champ est signalé
comme **manquant** — alors qu'il est présent. Rien ne permet de détecter cela sans dossiers
réels.

Ce qui serait le plus utile, par ordre de priorité :

1. **Deux ou trois dossiers de prostate.** Aucun exemple réel à ce jour, alors que la
   prostate représente la moitié du périmètre. Les champs PSA total, PSA libre,
   testostérone, IIEF5 et IRM prostatique n'ont jamais été vérifiés sur un vrai dossier.
2. **Deux dossiers de vessie supplémentaires**, si possible remplis par des médecins
   différents, pour voir comment les intitulés varient d'une personne à l'autre.
3. **Au moins un dossier accompagné de la fiche RCP réellement produite.** C'est le seul
   moyen de comparer ce que RCPia propose à ce que la RCP a effectivement décidé.
4. **Un dossier déjà passé en RCP auparavant**, avec la décision antérieure renseignée.
5. **Au format d'origine (PDF ou Word)**, plutôt qu'en texte converti.

Noms simulés ou anonymisés, comme pour le dossier déjà transmis.

### Une question sur le dossier existant

Dans la fiche RCP de référence (`pps-example-01.md`), le support nutritionnel est prescrit
sous forme d'**albumine en intraveineux**. RCPia propose à la place la voie orale puis
entérale, l'albumine intraveineuse ne corrigeant pas la dénutrition. C'est un écart
délibéré par rapport à ce qui est écrit dans le document, et il vaut mieux le signaler que
le laisser découvrir : est-ce une préférence de service, ou une ligne à corriger ?
