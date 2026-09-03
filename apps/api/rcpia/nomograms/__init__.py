"""Deterministic clinical calculators, and the discipline that keeps them honest.

**The clinician's requirement, verbatim:**

    Nous voulons éviter que le modèle estime lui-même un risque sans utiliser le
    calculateur validé.

A language model asked for a lymph-node invasion probability will produce a number --
plausible, confident, and not the nomogram's. Nothing downstream could tell, because the
answer arrives in the same shape as a real one. So these are pure functions over typed inputs,
they cite the model and revision that produced every figure, and where an input is absent they
**refuse and say which one**.

What is implemented, and what deliberately is not:

- **Briganti 2012** computes. Its coefficients were reconstructed from the published graphical
  nomogram rather than released as an equation, and every result says so.
- **Briganti 2019 and 2023** select correctly by diagnostic pathway and then refuse: their
  coefficients are not implemented, and no dossier records the mpMRI and PSMA inputs they take.
  A patient on the 2023 pathway is reported as `briganti_2023 / NOT_POSSIBLE`, never as a
  quietly computed 2012 figure for a different question.
- **MSKCC pre-radical-prostatectomy** computes five endpoints, each verified against the live
  calculator on two patients across both coefficient variants.
- **MSKCC post-operative and biochemical-recurrence** are absent: they need prostatectomy
  pathology, recurrence PSA and PSA kinetics, none of which any dossier records.
"""

from rcpia.nomograms.contract import (
    NomogramResult,
    Provenance,
    Reason,
    Status,
)
from rcpia.nomograms.record import ClinicalRecord, Tristate, record_from_facts
from rcpia.nomograms.service import evaluate_all

__all__ = [
    "ClinicalRecord",
    "NomogramResult",
    "Provenance",
    "Reason",
    "Status",
    "Tristate",
    "evaluate_all",
    "record_from_facts",
]
