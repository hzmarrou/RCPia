# Question au clinicien — application partielle des critères d'une recommandation

Vous nous avez indiqué :

> L'idéal serait de privilégier la ressource qui tient en compte le maximum des données du
> dossier RCP.

Nous comprenons le principe et il nous paraît juste : une recommandation construite sur
davantage de caractéristiques réelles du patient lui est mieux adaptée qu'une autre qui
n'en mobilise que quelques-unes.

Il nous manque une précision avant de l'implémenter, et elle change le résultat.

---

## La question

**Lorsqu'un patient remplit la plupart des critères d'une recommandation mais pas tous, cette
recommandation s'applique-t-elle encore ?**

Autrement dit : les critères d'une recommandation sont-ils cumulatifs — tous requis — ou
certains sont-ils déterminants et d'autres seulement contributifs ?

---

## Un exemple concret

**Surveillance active dans un cancer de prostate localisé.**

Supposons deux recommandations aux critères différents :

| | Critères d'éligibilité |
|---|---|
| **AFU** | ISUP 1 · PSA < 10 ng/mL · cT1c–T2a · ≤ 2 carottes positives |
| **NCCN** | ISUP 1 · densité de PSA < 0,15 · espérance de vie > 10 ans · IRM concordante |

Et un patient dont le dossier RCP indique :

```
ISUP 1                    ✔ AFU    ✔ NCCN
PSA 7 ng/mL, cT2a         ✔ AFU
4 carottes positives      ✘ AFU
espérance de vie > 10 ans          ✔ NCCN
densité de PSA / IRM      non renseignées
```

Le patient satisfait **3 critères AFU sur 4** et **2 critères NCCN sur 4**.

Par le comptage, l'AFU l'emporte et le système proposerait la surveillance active selon l'AFU.

**Mais le critère non rempli est le volume tumoral** — 4 carottes positives au lieu de 2. Ce
n'est pas un détail : c'est précisément ce qui fait sortir le patient du profil pour lequel la
surveillance active a été validée.

Le comptage donne donc la bonne source pour la mauvaise raison, et aboutit à une proposition
qui paraît solidement fondée alors qu'elle ne l'est pas.

---

## Ce que nous ne pouvons pas décider à votre place

Un critère manquant peut vouloir dire deux choses très différentes :

1. **Il est déterminant.** Sans lui la recommandation ne s'applique pas — 4 carottes positives
   sortent du cadre de la surveillance active, quel que soit le reste.
2. **Il est contributif.** Son absence affaiblit la recommandation sans l'annuler — une
   espérance de vie non documentée ne disqualifie pas à elle seule.

Un système qui compte les critères traite les deux de la même façon. Nous préférons vous le
signaler plutôt que de choisir.

---

## Trois formulations possibles, si cela aide

**a)** Tous les critères sont requis. Une recommandation dont un critère n'est pas rempli n'est
pas retenue, et le système signale qu'aucune source ne couvre entièrement ce patient.

**b)** Les critères sont hiérarchisés. Certains sont éliminatoires, d'autres pondérés. Il
faudrait alors nous indiquer lesquels, au moins pour les situations les plus fréquentes.

**c)** Le comptage suffit comme première approche, et l'onglet feedback que vous demandez sert
justement à vérifier au cas par cas si le raisonnement tient.

---

## Une remarque, pour être complets

Nous disposons par ailleurs d'une réponse antérieure à la même question :

> détecter le conflit → récupérer toutes les positions → les afficher clairement → laisser la
> RCP trancher et enregistrer son choix

Elle n'est pas identique à celle-ci : la première ne tranche pas, la seconde privilégie une
source. Nous ne savons pas laquelle fait foi, et cela change ce que produit le document.
Pourriez-vous nous le confirmer ?

Les deux sont implémentables. La différence porte sur ce que la RCP lit : deux positions
présentées côte à côte, ou une proposition privilégiée avec son raisonnement.
