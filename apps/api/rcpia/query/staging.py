"""Turning what a dossier records into the disease setting retrieval filters on.

**This is the safety-critical half of the query builder.** A wrong therapy line returns weaker
guidance; a wrong disease setting returns guidance written for a different patient, with a real
citation and a real page number attached. Everything here is therefore deterministic, cites the
guideline it came from, and refuses to guess.

**Three rules govern it, and each one is a decision rather than an implementation detail.**

**Clinical stage, never pathological, for a pre-treatment question.** EAU §4.5 states it
plainly: *"Clinical stage should be based on digital rectal examination only; additional staging
information based on imaging or pathology should be reported separately."* Using the pathology
to choose the guidance that decides whether to operate is the circularity this project already
names about the Briganti nomograms — *"using the answer as an input"*. `cTNM` is read for
treatment-selection questions; `pTNM` only when the question is explicitly post-operative.

**Absent is not normal.** A dossier with no cTNM does not become low risk by default. The
derivation returns no setting with the fields it would have needed. The caller may search
broadly, but must not present that as a validated stage. Three of seven bladder dossiers lack an
eGFR; missing staging is the same failure in a field that matters more.

**Every band cites the guideline that drew it.** The risk stratification below is EAU's, and
the same cut points appear in NCCN and AUA — recorded in `docs/implementation/`
`safety-rules-catalogue-prostate.md` as PT9. A threshold with no source is a threshold nobody
can check.
"""

import re
import unicodedata
from dataclasses import dataclass

# TNM as it appears in a French dossier: `cT4a N1 M1a`, `pT2`, `cT1c N0 M0`, `pTa`.
_T = re.compile(
    r"\b([cp])?\s*T\s*(is|[0-4ax])([a-d])?(?=$|[\W_]|[cp]?[NM])", re.IGNORECASE,
)
_N = re.compile(r"([cp])?\s*N\s*([0-3xo])(?=$|[\W_]|[cp]?M)", re.IGNORECASE)
_M = re.compile(r"([cp])?\s*M\s*([01x])([a-c])?(?=$|[\W_])", re.IGNORECASE)
_GLEASON = re.compile(r"(\d)\s*\+\s*(\d)")
_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")
# A dossier records what it is not yet sure of. `cT4a N1 M1a (à confirmer)` is not a metastatic
# diagnosis; it is a suspicion awaiting a scan. Reading it as established routes the patient to
# metastatic guidance -- a different treatment intent entirely -- on a finding nobody confirmed.
_UNCERTAIN = re.compile(
    r"à\s*confirmer|a\s*confirmer|to\s*be\s*confirmed|suspicion|suspect|probable|"
    r"non\s*confirmé|non\s*confirme|douteux|\?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Stage:
    """A parsed TNM, retaining the independent provenance prefix of each component."""

    prefix: str | None  # T prefix; kept under the original name for compatibility.
    t: str | None  # "a", "is", "1".."4"
    t_sub: str | None  # "a".."d"
    n: str | None
    m: str | None
    # The record hedged -- "(à confirmer)", "suspicion", a bare question mark.
    uncertain: bool = False
    lower_bound: bool = False
    n_prefix: str | None = None
    m_prefix: str | None = None

    @property
    def pathological_components(self) -> tuple[str, ...]:
        prefixes = (("T", self.prefix), ("N", self.n_prefix), ("M", self.m_prefix))
        return tuple(
            name for name, prefix in prefixes if prefix == "p"
        )

    @property
    def t_value(self) -> str:
        return f"T{self.t or 'x'}{self.t_sub or ''}"

    @property
    def node_positive(self) -> bool:
        return self.n in ("1", "2", "3")

    @property
    def metastatic(self) -> bool:
        """Confirmed M1 only. An unconfirmed M1 is handled by the derivation, not hidden here."""
        return self.m == "1" and not self.uncertain and not self.lower_bound

    @property
    def metastasis_suspected(self) -> bool:
        return self.m == "1" and self.uncertain

    @property
    def muscle_invasive(self) -> bool:
        """T2 or above. `Ta`, `Tis` and `T1` are non-muscle-invasive by definition."""
        return self.t in ("2", "3", "4")


def _component_matches(text: str) -> tuple[dict[str, list[re.Match[str]]], str]:
    matches: dict[str, list[re.Match[str]]] = {}
    remainder = text
    for name, pattern in (("T", _T), ("N", _N), ("M", _M)):
        matches[name] = list(pattern.finditer(remainder))
        # A suffix already consumed by T (e.g. T2cN0) is not N's clinical prefix.
        remainder = pattern.sub(" ", remainder)
    return matches, remainder


def parse_stage(text: str | None) -> Stage | None:
    """Normalize a dedicated stage value, never search a report for stage-like tokens.

    Only complete TNM notation and a small explicit qualifier grammar are accepted.
    In particular the ``a`` of ``au minimum`` is not a substage, and OCR ``NO`` is
    retained as unresolved ``o``, not silently corrected to N0.
    """
    if not text:
        return None
    matches, remainder = _component_matches(text)
    t = matches["T"][0] if matches["T"] else None
    n = matches["N"][0] if matches["N"] else None
    m = matches["M"][0] if matches["M"] else None
    if not (t or n or m):
        return None
    remainder = "".join(
        char for char in unicodedata.normalize("NFKD", remainder.lower())
        if not unicodedata.combining(char)
    )
    remainder = " ".join(re.sub(r"[():,;./?–—-]", " ", remainder).split())
    qualifiers = {
        "", "au minimum", "au moins", "minimum", "a confirmer", "to be confirmed",
        "suspicion", "suspicion de", "suspect", "probable", "non confirme", "douteux",
    }
    if remainder not in qualifiers:
        return None
    return Stage(
        prefix=(t.group(1) or "").lower() or None if t else None,
        t=(t.group(2).lower() if t else None),
        t_sub=(t.group(3).lower() if t and t.group(3) else None),
        n=(n.group(2).lower() if n else None),
        m=(m.group(2).lower() if m else None),
        n_prefix=(n.group(1) or "").lower() or None if n else None,
        m_prefix=(m.group(1) or "").lower() or None if m else None,
        uncertain=bool(_UNCERTAIN.search(text)),
        lower_bound=remainder in {"au minimum", "au moins", "minimum"},
    )


def has_competing_stage_components(text: str) -> bool:
    """Reject competing readings without changing the inherited TNM interpretation."""
    matches, _ = _component_matches(text)
    return any(
        len(
            {
                tuple((part or "").lower() for part in match.groups())
                for match in components
            }
        ) > 1
        for components in matches.values()
    )


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(text.replace(",", "."))
    return float(match.group(1)) if match else None


def parse_gleason(text: str | None) -> int | None:
    """The *sum*, from `4+3` or from a bare total."""
    if not text:
        return None
    pair = _GLEASON.search(text)
    if pair:
        return int(pair.group(1)) + int(pair.group(2))
    total = parse_number(text)
    return int(total) if total is not None and 2 <= total <= 10 else None


def reported_disease_phase(value: str | None) -> str | None:
    """Normalize an exact structured label, never infer it from exposure or progression."""
    labels = {
        "mcrpc": "metastatic_resistant", "nmcrpc": "nonmetastatic_resistant",
        "prostate_metastatic_castration_resistant": "metastatic_resistant",
        "prostate_nonmetastatic_castration_resistant": "nonmetastatic_resistant",
        "mhspc": "metastatic_sensitive", "mcspc": "metastatic_sensitive",
        "prostate_metastatic_hormone_sensitive": "metastatic_sensitive",
    }
    return labels.get((value or "").strip().casefold())


@dataclass(frozen=True)
class SettingDecision:
    """What the setting is, why, and what it was read from.

    `basis` names the dossier fields used. That is what lets a clinician check the derivation
    rather than take it, and what lets a wrong answer be traced to a field rather than to a
    function.
    """

    setting: str | None
    reason: str
    basis: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def determined(self) -> bool:
        return self.setting is not None


def _undetermined(reason: str, missing: tuple[str, ...]) -> SettingDecision:
    return SettingDecision(setting=None, reason=reason, missing=missing)


def _pathology_requires_explicit_scope(
    stage: Stage | None, *, post_operative: bool,
) -> SettingDecision | None:
    if stage is None or post_operative or not stage.pathological_components:
        return None
    return SettingDecision(
        None, "Pathological component(s) " + ", ".join(stage.pathological_components)
        + " are recorded separately, not used for pre-treatment clinical derivation.",
        ("ctnm",), tuple("clinical " + name for name in stage.pathological_components),
    )


def derive_bladder_setting(
    *, stage: Stage | None, muscle_invasion: str | None = None, cis: str | None = None,
    post_operative: bool = False,
) -> SettingDecision:
    """Bladder: the split is muscle invasion, then nodes, then metastases."""
    if refusal := _pathology_requires_explicit_scope(stage, post_operative=post_operative):
        return refusal
    if stage is None:
        if (muscle_invasion or "").strip().casefold() in {"oui", "yes"}:
            return SettingDecision(
                None, "Muscle invasion is explicitly recorded; TNM, nodes and distant "
                "assessment remain incomplete.", ("muscle_invasion",), ("ctnm", "N", "M"),
            )
        return _undetermined(
            "No clinical TNM or explicit muscle-invasion answer is recorded.",
            ("ctnm", "muscle_invasion"),
        )
    if stage.uncertain or stage.lower_bound:
        return SettingDecision(
            None, "The recorded stage is qualified or unconfirmed; neither metastatic "
            "nor non-metastatic extent is assumed.", ("ctnm",), ("confirmed staging assessment",),
        )
    if stage.metastatic:
        return SettingDecision("bladder_metastatic", "M1 recorded.", ("ctnm",))
    if stage.m != "0":
        return SettingDecision(
            None, f"{stage.t_value} is recorded; distant assessment is missing or unassessed, "
            "not M0.", ("ctnm",), ("M",),
        )
    if stage.node_positive:
        return SettingDecision(
            "bladder_node_positive_non_metastatic", f"N{stage.n} and M0 recorded.", ("ctnm",)
        )
    if stage.n != "0":
        return SettingDecision(
            None, "M0 is recorded, but nodal assessment is missing or unresolved.",
            ("ctnm",), ("N",),
        )
    if stage.t == "4" and stage.t_sub == "b":
        return SettingDecision(
            "bladder_locally_advanced_non_metastatic", "T4b invades pelvic wall.", ("ctnm",)
        )
    if stage.muscle_invasive:
        return SettingDecision(
            "bladder_mibc_non_metastatic",
            f"{stage.t_value} is muscle-invasive, N0 M0.",
            ("ctnm",),
        )
    if stage.t not in ("a", "is", "1"):
        return _undetermined("Local extent is missing or unassessed.", ("T",))
    if (muscle_invasion or "").strip().casefold() in {"oui", "yes"}:
        return SettingDecision(
            None, "The T field and the reported muscle invasion require organ/episode "
            "attribution before combining them.", ("ctnm", "muscle_invasion"),
            ("comparable assessment context",),
        )
    # Ta, Tis, T1 -- non-muscle-invasive. The risk band needs grade, size, multifocality and
    # prior history, which the dossier form does not carry as structured fields, so the
    # risk-unspecified parent is the honest answer rather than a guessed band.
    if (cis or "").strip().lower() in ("oui", "yes", "présent", "present"):
        return SettingDecision("bladder_nmibc_cis", "CIS recorded.", ("cis",))
    return SettingDecision(
        "bladder_nmibc_unspecified_risk",
        f"{stage.t_value} is non-muscle-invasive; the risk band is not derivable from the "
        "recorded fields.",
        ("ctnm",),
    )


def derive_prostate_setting(
    *,
    stage: Stage | None,
    gleason_sum: int | None = None,
    isup: int | None = None,
    psa: float | None = None,
    on_adt: bool = False,
    psa_rising: bool = False,
    castration_status: str | None = None,
    post_operative: bool = False,
) -> SettingDecision:
    """Prostate: metastatic state first, then castration state, then the risk band.

    Risk bands are EAU's, and the low-risk definition is the one three guidelines share —
    `cT1-cT2a`, Grade Group 1, PSA < 10 (catalogue PT9).

    Legacy boolean exposure/progression hints are accepted for compatibility but cannot
    establish sensitivity or resistance. Only an explicit reported phase label can do that.
    """
    if refusal := _pathology_requires_explicit_scope(stage, post_operative=post_operative):
        return refusal
    if stage is not None and (stage.uncertain or stage.lower_bound):
        return SettingDecision(
            None,
            "The stage is qualified or unconfirmed. Metastatic and non-metastatic prostate "
            "guidance differ in intent, so neither is selected until the status is confirmed.",
            ("ctnm",),
            ("confirmed metastatic status",),
        )
    if stage is not None and stage.metastatic:
        if castration_status == "metastatic_resistant":
            return SettingDecision(
                "prostate_metastatic_castration_resistant",
                "M1 and an explicit castration-resistant disease-phase label are recorded; "
                "the label is reported, not computed from treatment or PSA mentions.",
                ("ctnm", "disease_phase"),
            )
        if castration_status == "metastatic_sensitive":
            return SettingDecision(
                "prostate_metastatic_hormone_sensitive",
                "M1 and an explicit hormone-sensitive disease-phase label are recorded.",
                ("ctnm", "disease_phase"),
            )
        return SettingDecision(
            None, "M1 is recorded; hormone sensitivity/resistance is not established by "
            "treatment exposure or progression words.", ("ctnm",), ("disease_phase",),
        )
    if stage is None:
        return _undetermined(
            "No clinical TNM or independent clinical T is recorded.",
            ("ctnm", "ct_dre"),
        )
    if stage.m != "0":
        return SettingDecision(
            None, f"{stage.t_value} is recorded; distant assessment is missing or unassessed, "
            "not M0.", ("ctnm",), ("M",),
        )
    if castration_status in {"metastatic_resistant", "metastatic_sensitive"}:
        return SettingDecision(
            None, "M0 and a metastatic disease-phase label need comparable organ/episode "
            "attribution; neither is silently substituted.", ("ctnm", "disease_phase"),
            ("comparable assessment context",),
        )
    if stage.node_positive:
        return SettingDecision(
            "prostate_node_positive_non_metastatic", f"N{stage.n} and M0 recorded.", ("ctnm",)
        )
    if stage.n != "0":
        return SettingDecision(
            None, "M0 is recorded, but nodal assessment is missing or unresolved.",
            ("ctnm",), ("N",),
        )
    if castration_status == "nonmetastatic_resistant":
        return SettingDecision(
            "prostate_nonmetastatic_castration_resistant",
            "N0 M0 and an explicit castration-resistant disease-phase label are recorded.",
            ("ctnm", "disease_phase"),
        )
    if stage.t in ("3", "4"):
        return SettingDecision(
            "prostate_locally_advanced_non_metastatic",
            f"{stage.t_value} extends beyond the capsule.",
            ("ctnm",),
        )
    if stage.t not in ("1", "2"):
        return _undetermined("Local extent is missing or unassessed.", ("T",))
    if stage.t == "2" and stage.t_sub is None:
        return SettingDecision(
            "prostate_nonmetastatic_unspecified_risk",
            "N0 M0 is recorded, but T2 substage is not specified.", ("ctnm",), ("T substage",),
        )

    # Localized. The band needs all three of stage, grade and PSA -- any one of them can move a
    # patient up, so a missing one cannot be treated as absent-and-therefore-favourable.
    #
    # **An ambiguous grade counts as missing, and that was found by a test rather than by
    # reasoning.** A bare Gleason sum of 7 leaves the grade group undetermined (3+4 is group 2,
    # 4+3 is group 3). Treating "undetermined" as "no grade escalation" let a Gleason 7 with a
    # PSA of 6 derive as *low risk* -- the most favourable band in the vocabulary, reached by
    # not knowing something. Failing up to the parent is recoverable; failing down to low risk
    # is the error that sends a patient to active surveillance.
    grade_group = isup if isup is not None else _grade_group(gleason_sum)
    missing = tuple(
        name
        for name, value in (("gleason/isup", grade_group), ("psa_total", psa))
        if value is None
    )
    if missing:
        detail = (
            " A Gleason sum of 7 does not determine the grade group: 3+4 is group 2 and 4+3 is"
            " group 3."
            if grade_group is None and gleason_sum == 7
            else ""
        )
        return SettingDecision(
            "prostate_nonmetastatic_unspecified_risk",
            "Localized disease, but the risk band cannot be derived without "
            + " and ".join(missing)
            + ". The risk-unspecified parent is used rather than a guessed band."
            + detail,
            ("ctnm",),
            missing,
        )

    high = (
        (psa is not None and psa > 20)
        or (grade_group is not None and grade_group >= 4)
        or (stage.t == "2" and stage.t_sub == "c")
    )
    if high:
        return SettingDecision(
            "prostate_localized_high_risk",
            f"PSA {psa}, grade group {grade_group}, {stage.t_value} — high risk by EAU.",
            ("ctnm", "psa_total", "isup"),
        )
    intermediate = (
        (psa is not None and psa >= 10)
        or (grade_group is not None and grade_group in (2, 3))
        or (stage.t == "2" and stage.t_sub == "b")
    )
    if intermediate:
        return SettingDecision(
            "prostate_localized_intermediate_risk",
            f"PSA {psa}, grade group {grade_group}, {stage.t_value} — intermediate risk.",
            ("ctnm", "psa_total", "isup"),
        )
    return SettingDecision(
        "prostate_localized_low_risk",
        f"PSA {psa} < 10, grade group {grade_group}, {stage.t_value} — low risk "
        "(cT1-cT2a, Grade Group 1, PSA < 10).",
        ("ctnm", "psa_total", "isup"),
    )


def _grade_group(gleason_sum: int | None) -> int | None:
    """ISUP grade group from a Gleason *sum*, where the sum alone determines it.

    Returns None for 7, and that is the point: 3+4 is grade group 2 and 4+3 is grade group 3,
    and a sum of 7 cannot tell them apart. Guessing would move a patient a whole band.
    """
    if gleason_sum is None:
        return None
    if gleason_sum <= 6:
        return 1
    if gleason_sum == 8:
        return 4
    if gleason_sum >= 9:
        return 5
    return None
