# Champs du formulaire RCP à compléter à la source

À l'attention des cliniciens qui remplissent le formulaire, **avant** que le dossier n'arrive
dans RCPia.

## Ce document ne remplace pas l'application

RCPia signale déjà les manques : après le dépôt d'un dossier, le tableau de données affiche
**en rouge** les champs absents et **en orange** les valeurs incertaines ou contradictoires,
chaque ligne pouvant être corrigée à l'écran. Lorsqu'un manque rend l'analyse impossible, la
RCP n'est pas lancée du tout tant que le champ n'est pas renseigné.

Ce document porte sur l'étape d'avant : **le formulaire papier**. Un champ rempli à la source
coûte quelques secondes ; le même champ retrouvé après coup demande de rouvrir le dossier,
parfois de rappeler un confrère, souvent le jour de la RCP.

Deux champs ne peuvent d'ailleurs pas être réclamés par l'application aujourd'hui : la
**radiothérapie antérieure** n'a pas encore de case dans le tableau de données, et les
**scores chiffrés** renseignés « RAS » s'affichent comme renseignés alors qu'ils ne le sont
pas. Pour ceux-là, la saisie initiale est le seul filet.

## Ce que nous avons mesuré

RCPia a analysé quinze dossiers réels. Les agents spécialistes ont refusé de conclure dans
**8 avis sur 42** — non pas par prudence excessive, ni par défaut technique, mais parce que
certaines données n'étaient écrites nulle part dans le dossier.

Nous avons vérifié chaque refus un par un. La quasi-totalité porte sur des champs réellement
absents du formulaire, pas sur des champs mal lus par le système. **Aucun logiciel ne peut
récupérer une donnée que personne n'a écrite** : le système refuse volontairement de la
deviner, car inventer une donnée patient serait bien plus grave que de s'abstenir.

Les champs ci-dessous sont classés par impact. Les quatre premiers coûtent quelques secondes
par dossier et débloquent le plus.

---

## 1. Priorité haute — quelques secondes, effet immédiat

### Sexe

**Absent dans 12 dossiers sur 15.**

Quatre agents l'ont réclamé explicitement. Ce n'est pas un détail administratif :

- la cystectomie radicale n'est pas la même intervention chez l'homme (cysto-prostatectomie) et
  chez la femme (pelvectomie antérieure) — étendue, morbidité, conséquences sexuelles ;
- le calcul de la clairance de la créatinine en dépend, et c'est lui qui décide de
  l'éligibilité au cisplatine ;
- il détermine quels champs du formulaire s'appliquent (toucher vaginal, grossesse).

Le système **refuse volontairement de le déduire**. Sur un dossier décrivant pourtant une
prostate et un envahissement de l'urètre prostatique, l'agent d'urologie a répondu :

> « Le sexe est absent du dossier malgré la description d'une prostate et de l'urètre
> prostatique ; l'étendue précise de la cystectomie ne peut donc pas être formulée sans
> réserve. »

Trois dossiers portaient la ligne `Sexe : Homme` ou `Sexe : Femme` — le système les a lus
correctement, sans erreur. Il suffit donc de renseigner la case.

### Antécédent d'hormonothérapie — oui / non

**Absent dans 14 dossiers sur 15.**

### Antécédent de radiothérapie — oui / non

**Absent dans les 15 dossiers.**

Ces deux questions sont **disqualifiantes** pour le nomogramme MSKCC pré-prostatectomie : le
calculateur officiel les pose avant toute autre chose, et une réponse « oui » exclut le
patient du modèle. Une case vide ne peut pas être lue comme « non » — le patient n'a peut-être
tout simplement pas été interrogé.

**Conséquence concrète, mesurée :** deux dossiers (3 et 7) disposent de **toutes** les autres
données nécessaires. Ils ne produisent aucun résultat MSKCC uniquement parce que ces deux cases
sont vides. Deux réponses oui/non, et le nomogramme fonctionne.

### Stade clinique cTNM

**Absent dans 14 dossiers sur 15.**

Réclamé dans six des huit avis suspendus. Les dossiers renseignent le **pTNM** (anatomo-
pathologie après RTUV), ce qui est utile mais différent : le pT décrit la profondeur
d'infiltration sur les copeaux, il ne dit rien du statut ganglionnaire (N) ni métastatique (M).

Or le choix entre cystectomie radicale et préservation vésicale trimodale **exige** de savoir
que la maladie est N0 M0. Sans cTNM, l'agent ne peut pas trancher et le dit.

---

## 2. Le stade clinique T au toucher rectal — prostate

**Renseigné dans 2 dossiers sur 8.**

Deux urologues ont écrit le stade directement dans la ligne d'examen :

```
Toucher rectal : T2a apex droit
Toucher rectal : induration de la base gauche classé T2b.
```

Le système lit ces deux-là sans difficulté. Les autres décrivent la prostate sans la classer
— « prostate ferme de plus de 60 g », « prostate dure à la palpation » — et le système laisse
alors le champ vide, délibérément : transformer une description en stade serait s'approprier
un jugement clinique que l'examinateur n'a pas écrit.

**Pourquoi c'est important :** c'est l'unique donnée manquante pour faire tourner le
nomogramme de Briganti sur quatre dossiers de prostate supplémentaires.

**Attention — le stade doit venir du toucher rectal.** L'EAU (§4.5) est explicite : le stade
clinique repose sur le toucher rectal, et la stadification par imagerie se rapporte séparément.

Le dossier 9 illustre exactement ce point. Son toucher rectal décrit sans classer
(« prostate dure à la palpation, de paroi postérieure irrégulière »), tandis que la rubrique
*Radiologie — IRM prostatique* rapporte **T3aN0Mx**. Ce stade est réel et utile, mais il est
**radiologique**, non issu du toucher rectal : le système ne l'utilise donc pas comme stade
clinique pour les nomogrammes, et c'est délibéré.

**Merci de préciser la source du stade** lorsqu'il est écrit — « TR : T2a » ou
« IRM : T3aN0Mx » — plutôt que de le noter isolément. Les deux se ressemblent et ne servent pas
à la même chose.

### Une précision de saisie : le zéro de `N0`

Le dossier 9 porte, dans le fichier lui-même, `T3aNOMx` — avec la **lettre O** majuscule à la
place du **chiffre zéro**. Ce n'est pas une erreur de lecture du système : la faute est dans le
document d'origine.

Un lecteur humain corrige sans y penser ; un programme qui analyse le statut ganglionnaire ne
le peut pas. Merci de saisir `N0`, `M0` avec des zéros.

---

## 3. Données de biopsie — prostate

### Nombre de carottes positives et totales

**Nombre positif : renseigné dans 4 dossiers, `RAS` dans 4, absent dans 7.**

Le pourcentage de carottes positives est une variable directe des nomogrammes de Briganti et de
MSKCC. Sans elle, aucun calcul n'est possible.

### Détail par carotte

Longueur tumorale, pourcentage d'envahissement, localisation. Réclamé par les agents dans
plusieurs dossiers, et nécessaire aux versions récentes des nomogrammes.

---

## 4. Le score G8 — et pourquoi « RAS » ne suffit pas

**`RAS` dans 14 dossiers sur 15.**

Un agent l'a formulé mieux que nous ne le ferions :

> « Score G8 chiffré ; « RAS » n'est pas interprétable comme résultat. »

`RAS` sur un score signifie, pour le système, que l'évaluation n'a pas été faite — et non que le
patient va bien. Ces patients ont entre 64 et 79 ans : la fragilité gériatrique conditionne
l'éligibilité au cisplatine, à la chirurgie lourde et à la radiochimiothérapie.

Un chiffre, ou la mention explicite « non réalisé », lève l'ambiguïté.

Même remarque pour **IPSS** (`RAS` dans 11 dossiers), **albuminémie** (`RAS` dans 12), **DFG**
(`RAS` dans 10), **MNA**, **ADL** et **IIEF-5**.

**Pourquoi l'application ne vous le signale pas encore.** RCPia sait déjà que « RAS » ne peut
pas répondre à un type histologique, un grade de Gleason ou un stade : ces champs passent au
rouge. Mais un score chiffré marqué « RAS » s'affiche aujourd'hui comme renseigné, donc sans
alerte. La distinction manquante est simple :

- « RAS » est une réponse valable pour un **symptôme** — pas d'hématurie, pas d'allergie, pas
  d'antécédent familial ;
- « RAS » n'est pas une réponse pour une **mesure** : un score est un chiffre, ou il n'a pas été
  réalisé.

Le point est signalé côté développement. En attendant, un score chiffré à la saisie évite
l'ambiguïté entièrement.

**Cas particulier : le nombre de carottes positives.** Quatre dossiers portent `RAS` sur ce
champ. Impossible de savoir s'il faut lire « aucune carotte positive » ou « nombre non
relevé » — et pour un nomogramme, ces deux lectures donnent des résultats très différents.
Merci d'écrire le chiffre, fût-il zéro.

---

## 5. Unités et dates

### Unités de la créatinine

Un dossier rapporte une créatinine de **24,75** avec un DFG à **28,2**. La valeur est ambiguë
entre mg/L et µmol/L, et c'est elle qui décide de l'éligibilité au cisplatine. L'agent a demandé
l'unité, à juste titre.

**Merci de préciser systématiquement l'unité.**

### Dates des examens d'imagerie

Aucune date de TDM ou de scintigraphie n'est actuellement exploitable. Un bilan d'extension de
six mois et un bilan de six jours ne portent pas la même décision.

### Cinétique du PSA

Les dossiers ne portent qu'une valeur ponctuelle. Les dates des dosages antérieurs et le temps
de doublement ont été réclamés à plusieurs reprises.

---

## 6. Contexte de la RCP

**Date de la RCP : absente des 15 dossiers.**
**Motif de la RCP : absent des 15 dossiers.**

Plusieurs agents ont demandé « la question posée à la RCP ». Un dossier sans question explicite
oblige l'agent à supposer ce qu'on lui demande — première prise en charge, récidive, ou
réévaluation après traitement — et ces trois situations n'appellent pas les mêmes
recommandations.

**Phase de la maladie et traitements déjà reçus.** Un dossier de la série concernait une
maladie métastatique déjà traitée ; sans champ explicite, le système ne peut pas savoir qu'il
doit s'abstenir plutôt que de raisonner comme en première intention.

---

## Récapitulatif

| Donnée | Manquante | Ce qu'elle débloque |
|---|---|---|
| Sexe | 12 / 15 | étendue chirurgicale, clairance rénale, champs applicables |
| Hormonothérapie antérieure — oui/non | 14 / 15 | nomogramme MSKCC |
| Radiothérapie antérieure — oui/non | 15 / 15 | nomogramme MSKCC |
| Stade clinique cTNM | 14 / 15 | choix cystectomie / préservation vésicale |
| Stade T au toucher rectal (prostate) | 6 / 8 | nomogrammes Briganti et MSKCC |
| Carottes positives / totales | 11 / 15 | nomogrammes |
| Score G8 chiffré | 14 / 15 | éligibilité aux traitements lourds |
| Unité de la créatinine | à préciser | éligibilité au cisplatine |
| Source du stade (TR ou IRM) | à préciser | distinguer stade clinique et radiologique |
| Date et motif de la RCP | 15 / 15 | question à laquelle l'agent doit répondre |

---

## Ce que nous ne demandons pas

Le système n'attend pas un dossier parfait. Il est conçu pour dire ce qui manque plutôt que
pour combler les vides, et un dossier incomplet produit un avis provisoire utile assorti d'une
liste de ce qu'il faut obtenir. L'application affiche ces manques en rouge et permet de les
corriger à l'écran.

Les champs ci-dessus sont ceux qui, à eux seuls, transforment un refus en recommandation — et
ceux pour lesquels la saisie initiale évite un aller-retour le jour de la RCP.
