"""One call from a ledger reading to every nomogram's answer.

Callers pass facts, not a record: normalisation is part of the contract, and a caller that
built its own `ClinicalRecord` could quietly disagree with this one about what `RAS` means.

**Every implemented model answers, including the ones that refuse.** A fiche showing only the
models that produced a number would silently drop "Briganti 2023 needs the mpMRI index-lesion
diameter", which is the sentence that tells an RCP what to obtain. A refusal is an answer.
"""

from rcpia.nomograms import briganti, mskcc
from rcpia.nomograms.contract import NomogramResult
from rcpia.nomograms.record import ClinicalRecord, Tristate, record_from_facts

# Prostate only. Briganti and the MSKCC prostate nomograms have nothing to say about a bladder
# tumour, and running them would be the "never calculate Briganti for bladder cancer" rule
# failing at the first hurdle.
PROSTATE = "prostate"


def evaluate_all(
    facts: dict[str, str],
    *,
    cancer_type: str,
    adenocarcinoma_confirmed: Tristate = Tristate.unknown,
) -> tuple[NomogramResult, ...]:
    """Every implemented nomogram's answer for this patient, computed or explained.

    `adenocarcinoma_confirmed` is the clinician's answer to the eligibility question, and it
    defaults to `unknown`: a caller that has not asked has not been told, and both models
    then report `NOT_POSSIBLE` naming the field rather than computing on an assumption.
    """
    if cancer_type != PROSTATE:
        return ()
    record = record_from_facts(facts, adenocarcinoma_confirmed=adenocarcinoma_confirmed)
    return evaluate_record(record)


def evaluate_record(record: ClinicalRecord) -> tuple[NomogramResult, ...]:
    """The same, for a record a caller has already normalised or built in a test."""
    return (briganti.evaluate(record), mskcc.evaluate(record))
