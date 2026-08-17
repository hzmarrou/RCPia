# Dossier survey — 15 real dossiers, 17 August 2026

Ran the real extraction path over the 15 unique dossiers supplied (`dossier01`–`dossier15`;
`dossier02` and `dossier2` are byte-identical). **8 prostate, 7 bladder.** Until now the
label catalogue in `apps/api/rcpia/cases/fact_schema.py` was derived from one bladder
dossier.

`dossier01.md` is the case the two reference PPS documents belong to (`Homme, 63 ans`,
carcinome épidermoïde) — the repo held the PPS pair but never the dossier itself.

## Coverage

| Dossier | Type | Fields found | Required fields reported missing |
|---|---|---|---|
| dossier01 | bladder | 60 | — |
| dossier02 | bladder | 69 | — |
| dossier3 | prostate | 64 | grade, ptnm |
| dossier4 | prostate | 62 | grade, ptnm, sex |
| dossier5–9, 12 | prostate | 62–65 | grade, ptnm, sex |
| dossier10, 11, 13–15 | bladder | 62–66 | sex |

## Three schema errors, each a silent false gap

A field reported *missing* that is visibly present reads exactly like an incomplete
dossier. Nothing in the system can detect that without real dossiers, which is why this
survey exists.

### 1. Prostate grade is Gleason and ISUP, not `Grade` — 8/8 prostate dossiers

The dossiers write `Gleason : 7 (3+4)` and `ISUP : 2`, sometimes without the space before
the colon. `grade` is *required* for prostate, so every prostate case currently reports its
grade as missing while the value sits in the text. Two new fields are needed, both required
for prostate; `grade` should stop being required for it.

### 2. Prostate dossiers carry no TNM at all — 8/8

`grep` finds no `TNM`, `stade` or `classification` in any of them. pTNM is a
post-prostatectomy staging; these cases are characterised by Gleason/ISUP, PSA and PIRADS.
Requiring `ptnm` for prostate manufactures a gap on every prostate case. It should remain
*applicable* (a prostatectomy specimen has one) but not required.

### 3. `sex` is absent from 12 of 15 dossiers

Only `Grossesse en cours (si femme)` references it. Requiring a field that five sevenths of
real bladder dossiers and seven eighths of prostate dossiers omit buries the real gaps in
noise — the exact failure `fact_schema.py`'s own docstring warns about. It should stop
being required.

**Not derived for prostate.** Inferring `Homme` from a prostate diagnosis would be wrong for
trans and non-binary patients with a prostate, and this system must not encode that. Absent
stays absent; the fertility rule already answers `à déterminer` without it.

## 86 unrecognised labels — data being dropped

Present in the dossiers, matched by no field in the catalogue. The clinically substantial
ones, by frequency:

| Count | Label | Example |
|---|---|---|
| 15 | Précisions sur les marges | `RAS` |
| 14 | Si progression ou rechute | `RAS` |
| 8 | Gleason | `7 (3+4)` |
| 8 | ISUP | `2` |
| 8 | PIN / EPC / PNI / VI | `Non`, `RAS` |
| 8 | Nombre de carottes totales / positives | `21`, `5` |
| 8 | Siège des carottes positives | `Apex droit` |
| 7 | Toucher rectal | `prostate ferme de 40g…` |
| 2 | Hormonothérapie | `Triptoréline…` |
| 2 | Sonde double JJ droite / gauche | … |

The biopsy fields (carottes totales, positives, siège) and the perineural/capsular invasion
flags (PNI, EPC, VI, PIN) are core prostate staging inputs. The rest — bilirubin fractions,
GGT, uric acid, vitamin D — are biology RCPia currently discards.

Some entries in the full list are prose artefacts (`Technique :`, `Conclusion :`,
`Latéralement :`) rather than fields, and should stay unrecognised.

## Reproducing

`scratchpad/survey.py` and `scratchpad/unknown_labels.py` run both passes against the
dossier directory without touching the database.
