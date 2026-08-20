"""The clinical fields RCPia knows how to recognise.

Derived from the reference dossiers in `docs/reference/examples/`, which are structured
French forms: a label, a colon, and a value. The catalogue names the fields, the labels
they appear under, and which cancer types care about them.

Two absences are deliberately distinguished throughout:

- **`RAS`** — the clinician looked and found nothing to report. That is information.
- **not present at all** — nobody filled the field in. That is a gap, shown in red.

Collapsing the two would paint a properly completed dossier red and hide real gaps in the
noise.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from rcpia.core.models import CancerType

# Values a clinician writes to mean "checked, nothing to report".
REPORTED_ABSENT = "RAS"
REPORTED_ABSENT_TOKENS = frozenset(
    {"ras", "r.a.s", "r.a.s.", "néant", "neant", "sans particularité", "sans particularite"}
)


class FactCategory(StrEnum):
    identity = "identity"
    social = "social"
    history = "history"
    presentation = "presentation"
    performance = "performance"
    examination = "examination"
    imaging = "imaging"
    biology = "biology"
    pathology = "pathology"
    decision = "decision"


@dataclass(frozen=True)
class FactField:
    """One recognisable field.

    `labels` are matched case- and accent-insensitively against the text before the
    colon, so "PSA totale", "psa total" and "PSA TOTALE" all resolve to `psa_total`.
    """

    key: str
    labels: tuple[str, ...]
    category: FactCategory
    unit: str | None = None
    # None means the field applies to every cancer type in scope.
    cancer_types: tuple[CancerType, ...] | None = None
    # Required fields drive the completeness check; the rest are useful but not blocking.
    required_for: tuple[CancerType, ...] = field(default_factory=tuple)

    def applies_to(self, cancer_type: CancerType) -> bool:
        return self.cancer_types is None or cancer_type in self.cancer_types

    def is_required_for(self, cancer_type: CancerType) -> bool:
        return cancer_type in self.required_for


PROSTATE = CancerType.prostate
BLADDER = CancerType.bladder
BOTH = (PROSTATE, BLADDER)


FIELDS: tuple[FactField, ...] = (
    # ---------------------------------------------------------------- identity
    FactField("dossier_reference", ("dossier",), FactCategory.identity),
    # Not required. Twelve of fifteen real dossiers omit it — prostate forms leave it
    # implicit, and most bladder forms simply do not record it. Requiring a field that
    # nearly every real dossier lacks paints a complete dossier red and buries the gaps
    # that matter, which is the failure this module's docstring warns about.
    #
    # Deliberately not inferred from the cancer type either: a prostate diagnosis does not
    # make a patient male, and encoding that assumption would misgender trans and
    # non-binary patients. Absent stays absent.
    FactField("sex", ("sexe",), FactCategory.identity),
    FactField("age", ("age", "âge"), FactCategory.identity, unit="ans", required_for=BOTH),
    FactField("rcp_organ", ("rcp urologie", "organe"), FactCategory.identity),
    # ---------------------------------------------------------------- social
    FactField("occupation", ("profession",), FactCategory.social),
    FactField("marital_status", ("statut marital",), FactCategory.social),
    FactField("children", ("nombre d'enfants", "nombre d’enfants"), FactCategory.social),
    FactField("social_cover", ("couverture sociale",), FactCategory.social),
    FactField("occupational_cancer", ("cancer reconnu professionnel",), FactCategory.social),
    # ---------------------------------------------------------------- history
    FactField(
        "medical_history", ("antécédents médicaux", "antecedents medicaux"),
        FactCategory.history, required_for=BOTH,
    ),
    FactField(
        "surgical_history", ("antécédents chirurgicaux", "antecedents chirurgicaux"),
        FactCategory.history,
    ),
    FactField(
        "family_history", ("antécédents familiaux", "antecedents familiaux"), FactCategory.history
    ),
    FactField("toxic_habits", ("habitudes toxiques",), FactCategory.history),
    FactField("allergies", ("allergies",), FactCategory.history),
    # ---------------------------------------------------------------- presentation
    FactField(
        "discovery", ("circonstances de découverte", "circonstances de decouverte"),
        FactCategory.presentation,
    ),
    FactField(
        "illness_history", ("histoire de la maladie",), FactCategory.presentation, required_for=BOTH
    ),
    FactField(
        "previous_rcp_decisions", ("décisions rcp antérieures", "decisions rcp anterieures"),
        FactCategory.decision,
    ),
    FactField("disease_phase", ("phase actuelle",), FactCategory.presentation),
    FactField("diagnosis_date", ("date de diagnostic",), FactCategory.presentation),
    # ---------------------------------------------------------------- performance
    FactField(
        "pallia10", ("état général score pallia 10", "etat general score pallia 10", "pallia 10"),
        FactCategory.performance,
    ),
    FactField("g8_score", ("score g8 (si ≥ 75 ans)", "score g8"), FactCategory.performance),
    FactField(
        "pregnancy",
        ("grossesse en cours (si femme)", "grossesse en cours"),
        FactCategory.performance,
    ),
    FactField(
        "iief5", ("état de l'érection (score iief5)", "score iief5", "iief5"),
        FactCategory.performance, cancer_types=(PROSTATE,),
    ),
    FactField(
        "ipss", ("état de la miction (score ipss)", "score ipss", "ipss"),
        FactCategory.performance,
    ),
    FactField(
        "mna", ("état nutritionnel (score mna)", "score mna", "mna"), FactCategory.performance
    ),
    FactField("adl", ("autonomie (échelle adl)", "autonomie", "adl"), FactCategory.performance),
    FactField("who_status", ("oms",), FactCategory.performance),
    # ---------------------------------------------------------------- examination
    FactField(
        "cardiac_assessment", ("evaluation cardiaque", "évaluation cardiaque"),
        FactCategory.examination,
    ),
    FactField(
        "clinical_examination",
        ("particularités de l'examen clinique", "particularites de l'examen clinique"),
        FactCategory.examination,
    ),
    # ---------------------------------------------------------------- imaging
    FactField("cystoscopy", ("cystoscopie",), FactCategory.imaging, cancer_types=(BLADDER,)),
    FactField("ct_tap", ("tdm tap",), FactCategory.imaging, required_for=BOTH),
    FactField("bone_scan", ("scintigraphie osseuse",), FactCategory.imaging),
    FactField("uro_ct", ("uroscanner",), FactCategory.imaging),
    FactField(
        "prostate_mri", ("irm prostatique multiparamétrique", "irm prostatique multiparametrique"),
        FactCategory.imaging, cancer_types=(PROSTATE,), required_for=(PROSTATE,),
    ),
    # Further imaging real dossiers carry. Recognising them is what stops the prostate MRI
    # report swallowing the study that follows it.
    FactField(
        "pet_ct", ("pet choline", "pet psma", "pet-psma", "pet scan", "tep tdm", "tep-tdm"),
        FactCategory.imaging,
    ),
    FactField("spinal_mri", ("irm médullaire", "irm medullaire"), FactCategory.imaging),
    FactField("pelvic_mri", ("irm du bassin", "irm pelvienne"), FactCategory.imaging),
    # ---------------------------------------------------------------- biology
    FactField(
        "psa_total", ("psa totale", "psa total"), FactCategory.biology, unit="ng/mL",
        cancer_types=(PROSTATE,), required_for=(PROSTATE,),
    ),
    FactField(
        "psa_free", ("psa libre",), FactCategory.biology, unit="ng/mL", cancer_types=(PROSTATE,)
    ),
    FactField(
        "testosterone", ("testostérone", "testosterone"), FactCategory.biology,
        cancer_types=(PROSTATE,),
    ),
    FactField("haemoglobin", ("hb",), FactCategory.biology, unit="g/dL", required_for=BOTH),
    FactField("platelets", ("pq",), FactCategory.biology),
    FactField("white_cells", ("gb",), FactCategory.biology),
    FactField("neutrophils", ("pnn",), FactCategory.biology),
    FactField("lymphocytes", ("lymph",), FactCategory.biology),
    FactField("urea", ("urée", "uree"), FactCategory.biology),
    FactField("creatinine", ("créatinine", "creatinine"), FactCategory.biology, required_for=BOTH),
    # Renal function decides cisplatin eligibility in bladder cancer.
    FactField(
        "egfr", ("dfg",), FactCategory.biology, unit="mL/min", required_for=(BLADDER,)
    ),
    FactField("sodium", ("na",), FactCategory.biology),
    FactField("potassium", ("k",), FactCategory.biology),
    FactField("bicarbonate", ("bicarbonates",), FactCategory.biology),
    FactField("calcium", ("ca",), FactCategory.biology),
    FactField("phosphorus", ("ph",), FactCategory.biology),
    FactField("albumin", ("alb", "albumine"), FactCategory.biology, unit="g/L"),
    FactField("protein", ("prot",), FactCategory.biology),
    FactField("crp", ("crp",), FactCategory.biology),
    FactField("prothrombin", ("tp",), FactCategory.biology),
    FactField("blood_group", ("groupage sanguin",), FactCategory.biology),
    FactField("urine_culture", ("ecbu",), FactCategory.biology),
    FactField("alat", ("alat",), FactCategory.biology),
    FactField("asat", ("asat",), FactCategory.biology),
    FactField("ldh", ("ldh",), FactCategory.biology),
    FactField("cpk", ("cpk",), FactCategory.biology),
    # ---------------------------------------------------------------- pathology
    FactField(
        "previous_pathology",
        (
            "synthèse des examens anatomopathologiques précédents",
            "synthese des examens anatomopathologiques precedents",
        ),
        FactCategory.pathology,
    ),
    FactField(
        "residual_tumour",
        ("résidu tumoral après chirurgie (si opéré)", "résidu tumoral après chirurgie"),
        FactCategory.pathology,
    ),
    FactField("margins", ("marges",), FactCategory.pathology),
    FactField(
        "new_pathology", ("nouvel examen anatomopathologique",), FactCategory.pathology
    ),
    FactField("pathology_date", ("date",), FactCategory.pathology),
    FactField("pathology_type", ("type d'examen", "type d’examen"), FactCategory.pathology),
    FactField("pathology_site", ("localisation",), FactCategory.pathology),
    FactField(
        "histology", ("type histologique",), FactCategory.pathology, required_for=BOTH
    ),
    # Applicable to both — a prostatectomy specimen is staged — but only required for
    # bladder. None of the eight real prostate dossiers records a TNM at all: those cases
    # are characterised by Gleason, ISUP, PSA and PIRADS, and requiring a pTNM manufactured
    # a missing field on every one of them.
    FactField("ptnm", ("ptnm",), FactCategory.pathology, required_for=(BLADDER,)),
    # Likewise `Grade`: prostate grades with Gleason and ISUP below, and 8/8 real prostate
    # dossiers reported their grade missing while the value sat in the text.
    FactField("grade", ("grade",), FactCategory.pathology, required_for=(BLADDER,)),
    FactField(
        "gleason", ("gleason", "score de gleason"), FactCategory.pathology,
        cancer_types=(PROSTATE,), required_for=(PROSTATE,),
    ),
    FactField(
        "isup", ("isup", "groupe isup"), FactCategory.pathology,
        cancer_types=(PROSTATE,), required_for=(PROSTATE,),
    ),
    # Biopsy burden: how much of the gland is involved, and where. Core staging inputs
    # that were being read and discarded.
    FactField(
        "cores_total", ("nombre de carottes totales",), FactCategory.pathology,
        cancer_types=(PROSTATE,),
    ),
    FactField(
        "cores_positive", ("nombre de carottes positives",), FactCategory.pathology,
        cancer_types=(PROSTATE,),
    ),
    FactField(
        "cores_site", ("siège des carottes positives", "siege des carottes positives"),
        FactCategory.pathology, cancer_types=(PROSTATE,),
    ),
    # Invasion flags, written as bare acronyms in the dossiers.
    FactField(
        "perineural_invasion", ("pni",), FactCategory.pathology, cancer_types=(PROSTATE,)
    ),
    FactField(
        "capsular_invasion", ("epc",), FactCategory.pathology, cancer_types=(PROSTATE,)
    ),
    FactField(
        "vascular_invasion", ("vi",), FactCategory.pathology, cancer_types=(PROSTATE,)
    ),
    FactField(
        "intraepithelial_neoplasia", ("pin",), FactCategory.pathology,
        cancer_types=(PROSTATE,),
    ),
    FactField(
        "margin_details",
        ("précisions sur les marges", "precisions sur les marges"),
        FactCategory.pathology,
    ),
    FactField(
        "muscle_invasion", ("infiltration du muscle",), FactCategory.pathology,
        cancer_types=(BLADDER,), required_for=(BLADDER,),
    ),
    FactField("cis", ("cis",), FactCategory.pathology, cancer_types=(BLADDER,)),
    FactField(
        "immunohistochemistry",
        ("complément immunohistochimique", "complement immunohistochimique"),
        FactCategory.pathology,
    ),
    FactField(
        "molecular_biology",
        ("biologie moléculaire", "biologie moleculaire"),
        FactCategory.pathology,
    ),
)


FIELDS_BY_KEY: dict[str, FactField] = {field_spec.key: field_spec for field_spec in FIELDS}


def fields_for(cancer_type: CancerType) -> tuple[FactField, ...]:
    return tuple(field_spec for field_spec in FIELDS if field_spec.applies_to(cancer_type))


def required_fields_for(cancer_type: CancerType) -> tuple[FactField, ...]:
    return tuple(field_spec for field_spec in FIELDS if field_spec.is_required_for(cancer_type))


def is_reported_absent(value: str) -> bool:
    """True when the clinician wrote something meaning "checked, nothing found"."""
    return value.strip().strip(".").lower() in REPORTED_ABSENT_TOKENS
