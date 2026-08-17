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

from dataclasses import dataclass

from rcpia.cases.fact_schema import FIELDS_BY_KEY, required_fields_for
from rcpia.cases.ledger import AMBER, RED, LedgerEntry, display_label
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
