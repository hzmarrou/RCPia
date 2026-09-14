"""Context-sensitive completeness checks.

What a case needs depends on what it is. A bladder case cannot be reasoned about without
knowing whether the muscle is invaded, and cisplatin eligibility turns on renal function;
a prostate case needs a PSA. Asking every case for every field would bury the fields that
matter.

Missing information does **not** normally stop the analysis. Agents are told what is
missing and must say so in their reasoning — that is the honest behaviour for a
decision-support tool. Analysis is blocked only when a safe provisional output is
impossible without the field, which is a much smaller list.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from rcpia.cases.fact_schema import (
    FIELDS_BY_KEY,
    is_reported_absent,
    is_unknown_value,
    required_fields_for,
)
from rcpia.cases.ledger import AMBER, RED, LedgerEntry, display_label, project_entry
from rcpia.core.models import CancerType

# Fields without which no provisional proposal can be made safely. Kept deliberately
# short: everything else is a stated limitation, not a barrier.
BLOCKING_FIELDS: dict[CancerType, tuple[str, ...]] = {
    # Muscle invasion decides between conservative treatment and cystectomy; renal
    # function decides whether cisplatin can even be considered.
    CancerType.bladder: ("histology", "muscle_invasion"),
    # Gleason decides between surveillance and active treatment as squarely as muscle
    # invasion decides between conservative treatment and cystectomy. Without it a
    # prostate case cannot be risk-stratified, so no safe provisional proposal exists.
    CancerType.prostate: ("histology", "gleason"),
}


# Words that may legitimately surround a grade without changing it.
SCORE_NOISE = re.compile(r"\b(?:gleason|isup|score|grade|groupe|de|du)\b|[:=]")

# What remains after the noise must *be* the score, not merely contain one. Matching a
# digit anywhere accepted `2024` as a Gleason and `score inconnu 7` as a grade of 7.
GLEASON_SHAPE = re.compile(
    r"^(?:(?P<total>\d{1,2})\s*)?(?:\(?\s*(?P<a>\d)\s*\+\s*(?P<b>\d)\s*\)?)?"
    r"(?:\s*(?P<total2>\d{1,2}))?$"
)
ISUP_SHAPE = re.compile(r"^(?P<grade>\d{1,2})$")


def _clean(text: str) -> str:
    """Strip the words a clinician writes around a grade, leaving the grade."""
    return " ".join(SCORE_NOISE.sub(" ", text.lower()).split())


def _valid_gleason(text: str) -> bool:
    """A total in 6-10, and if the two grades are given they must sum to it."""
    match = GLEASON_SHAPE.match(_clean(text))
    if match is None:
        return False

    pair = (match.group("a"), match.group("b"))
    totals = [value for value in (match.group("total"), match.group("total2")) if value]

    if pair[0] and pair[1]:
        primary, secondary = int(pair[0]), int(pair[1])
        if not (1 <= primary <= 5 and 1 <= secondary <= 5):
            return False
        summed = primary + secondary
        if not 6 <= summed <= 10:
            return False
        # `7 (5+5)` is internally inconsistent: 5+5 is 10, not 7.
        return all(int(total) == summed for total in totals)

    # A bare total, and nothing else left over.
    return len(totals) == 1 and 6 <= int(totals[0]) <= 10


def _valid_isup(text: str) -> bool:
    """A single grade group, 1 to 5. `12` is not grade 1."""
    match = ISUP_SHAPE.match(_clean(text))
    return match is not None and 1 <= int(match.group("grade")) <= 5


# What a blocking field must look like to count as answered. A field absent from this map
# only has to be non-empty and neither an unknown placeholder nor `RAS`.
BLOCKING_VALUE_CHECKS: dict[str, "Callable[[str], bool]"] = {
    "gleason": _valid_gleason,
    "isup": _valid_isup,
}


def satisfies_blocking(key: str, value: str | None) -> bool:
    """Whether a recorded value actually answers a blocking field.

    Unknown placeholders never answer it, regardless of the recorded status or origin.
    `RAS` never does. It means the clinician looked and found nothing to report, which is
    meaningful for a symptom and meaningless for a histology or a grade — there is no such
    thing as a tumour with no histological type.

    A grade must also *be* a grade. Testing only for a digit accepted `2024` as a Gleason
    score, `score inconnu 7` as a grade of 7, and `12` as an ISUP group.
    """
    text = (value or "").strip()
    if is_unknown_value(text) or is_reported_absent(text):
        return False
    check = BLOCKING_VALUE_CHECKS.get(key)
    return check(text) if check else True


@dataclass(frozen=True)
class CompletenessIssue:
    key: str
    label: str
    severity: str  # "blocking" | "required" | "uncertain"
    highlight: str
    message: str


@dataclass(frozen=True)
class CompletenessReport:
    cancer_type: str
    issues: tuple[CompletenessIssue, ...]

    @property
    def blocking(self) -> tuple[CompletenessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "blocking")

    @property
    def can_run_analysis(self) -> bool:
        return not self.blocking

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(issue.key for issue in self.issues if issue.highlight == RED)

    @property
    def uncertain_keys(self) -> tuple[str, ...]:
        return tuple(issue.key for issue in self.issues if issue.highlight == AMBER)

    def limitation_notes(self) -> tuple[str, ...]:
        """What an agent must be told, and what the PPS must state as a limitation."""
        return tuple(issue.message for issue in self.issues)


def assess(entries: list[LedgerEntry], *, cancer_type: CancerType) -> CompletenessReport:
    entries = [project_entry(entry) for entry in entries]
    by_key = {entry.key: entry for entry in entries}
    blocking = BLOCKING_FIELDS.get(cancer_type, ())
    issues: list[CompletenessIssue] = []

    for spec in required_fields_for(cancer_type):
        entry = by_key.get(spec.key)
        if entry is None or entry.highlight == RED:
            severity = "blocking" if spec.key in blocking else "required"
            issues.append(
                CompletenessIssue(
                    key=spec.key,
                    label=display_label(spec.key),
                    severity=severity,
                    highlight=RED,
                    message=(
                        f"{display_label(spec.key)} : information manquante"
                        + (
                            " — analyse impossible sans cette donnée."
                            if severity == "blocking"
                            else "."
                        )
                    ),
                )
            )

    # A blocking field can be present, uncontradicted, and still say nothing. `RAS` means
    # the clinician looked and found nothing to report — which is information about a
    # symptom and nonsense about a histology. Real dossiers do it: dossier8 records
    # `Type histologique : RAS`, `Gleason : RAS` and `ISUP : RAS`, and every one was read
    # as a confirmed value, so the analysis ran on a prostate case with no diagnosis and
    # no grade.
    for key in blocking:
        entry = by_key.get(key)
        if entry is None or entry.highlight == RED:
            continue  # already reported as missing above
        if not satisfies_blocking(key, entry.value):
            issues.append(
                CompletenessIssue(
                    key=key,
                    label=display_label(key),
                    severity="blocking",
                    highlight=RED,
                    message=(
                        f"{display_label(key)} : la valeur enregistrée "
                        f"(« {entry.value} ») ne constitue pas une donnée exploitable — "
                        "analyse impossible sans cette donnée."
                    ),
                )
            )

    # A blocking field that was read but is contradictory is no better than absent.
    for key in blocking:
        entry = by_key.get(key)
        if entry is not None and entry.highlight == AMBER:
            issues.append(
                CompletenessIssue(
                    key=key,
                    label=display_label(key),
                    severity="blocking",
                    highlight=AMBER,
                    message=(
                        f"{display_label(key)} : valeurs contradictoires — "
                        "à trancher avant toute analyse."
                    ),
                )
            )

    for entry in entries:
        if entry.highlight != AMBER or entry.key in blocking:
            continue
        issues.append(
            CompletenessIssue(
                key=entry.key,
                label=entry.label,
                severity="uncertain",
                highlight=AMBER,
                message=f"{entry.label} : donnée incertaine ou contradictoire.",
            )
        )

    return CompletenessReport(cancer_type=str(cancer_type), issues=tuple(issues))


def known_field(key: str) -> bool:
    return key in FIELDS_BY_KEY
