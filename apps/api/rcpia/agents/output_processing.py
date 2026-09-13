"""Lossless citation bookkeeping and bounded, nonclinical retry diagnostics."""

import hashlib
import json
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rcpia.agents.schemas import (
    AgentOutput,
    DecisionLedSynthesisOutput,
    EvidenceReference,
    TaskBundle,
    UnattestedAgreement,
)

FEEDBACK_KEY = "_output_validation_feedback"
MAX_FEEDBACK_ITEMS = 10
SAFE_LOCATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class OutputFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["rcpia.output-feedback.v1"] = "rcpia.output-feedback.v1"
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    previous_run_id: uuid.UUID
    messages: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        min_length=1, max_length=MAX_FEEDBACK_ITEMS,
    )


def complete_reference_register(
    output: AgentOutput, bundle: TaskBundle,
) -> tuple[AgentOutput, list[dict[str, str]]]:
    """Mirror already-cited sources, without inventing evidence or rewriting prose."""
    if not isinstance(output, DecisionLedSynthesisOutput):
        return output, []
    allowed = {identifier.casefold() for identifier in bundle.citable_ids()}
    if any(identifier.casefold() not in allowed for identifier in output.pps_proposal.cited_ids()):
        raise UnattestedAgreement(
            "La proposition PPS cite des identifiants absents du dossier fourni."
        )
    registered = {reference.id.casefold() for reference in output.evidence_references}
    additions: list[EvidenceReference] = []
    receipt = []
    for use in output.pps_proposal.citation_uses():
        if use.id.casefold() in registered:
            continue
        additions.append(EvidenceReference(id=use.id, supports=use.supports))
        registered.add(use.id.casefold())
        receipt.append({
            "id": use.id,
            "evidence_path": use.evidence_path,
            "supports_path": use.supports_path,
            "operation": "mirror_existing_proposal_citation",
        })
    if not additions:
        return output, []
    completed = output.model_copy(deep=True)
    completed.evidence_references.extend(additions)
    return completed, receipt


def input_fingerprint(bundle: TaskBundle) -> str:
    data = {
        "inputs": bundle.inputs.model_dump(mode="json"),
        "output_format": bundle.output_format.model_dump(mode="json", by_alias=True),
    }
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def feedback_for_failure(
    bundle: TaskBundle, run_id: uuid.UUID, messages: tuple[str, ...],
) -> dict[str, Any]:
    return OutputFeedback(
        input_fingerprint=input_fingerprint(bundle), previous_run_id=run_id,
        messages=list(messages),
    ).model_dump(mode="json")


def with_output_feedback(bundle: TaskBundle, payload: object) -> TaskBundle:
    feedback = OutputFeedback.model_validate(payload)
    if feedback.input_fingerprint != input_fingerprint(bundle):
        raise ValueError(
            "Le dossier, les avis ou le schéma ont changé depuis l'échec : "
            "demandez une nouvelle analyse plutôt que de réutiliser ce retour de validation."
        )
    result = bundle.model_copy(deep=True)
    result.constraints.append(
        "Retour technique du validateur sur la tentative précédente "
        f"({feedback.previous_run_id}). Ce retour n'est ni un fait patient ni un avis clinique. "
        "Produisez un nouvel objet JSON complet respectant le schéma. Reformulez si nécessaire "
        "sans tronquer, changer un fait, supprimer une condition clinique ou inventer une source. "
        "Ne recopiez pas ces diagnostics dans le texte clinique : "
        + json.dumps(feedback.messages, ensure_ascii=False)
    )
    return result


def validation_location(location: tuple[str | int, ...]) -> str:
    parts = []
    for part in location[:5]:
        if isinstance(part, int):
            parts.append(str(part))
        elif SAFE_LOCATION.fullmatch(part):
            parts.append(part)
        else:
            parts.append("<champ>")
    if len(location) > 5:
        parts.append("<suite>")
    return ".".join(parts) or "output"


def schema_feedback(error: ValidationError) -> tuple[str, ...]:
    """Do not put rejected patient text or arbitrary model keys into the next prompt."""
    messages = []
    for item in error.errors(include_input=False, include_url=False)[:MAX_FEEDBACK_ITEMS]:
        path = validation_location(item["loc"])
        code = item["type"] if SAFE_LOCATION.fullmatch(item["type"]) else "constraint"
        context = item.get("ctx", {})
        if code in {"too_long", "string_too_long"}:
            maximum = context.get("max_length")
            actual = context.get("actual_length")
            detail = f"maximum {maximum}"
            if isinstance(actual, int):
                detail += f", longueur reçue {actual}"
            message = f"{path}: {detail}. Reformulez sans tronquer ni perdre de condition."
        elif code in {"too_short", "string_too_short"}:
            message = f"{path}: longueur minimale {context.get('min_length')} requise."
        elif code == "missing":
            message = f"{path}: champ requis absent."
        elif code == "extra_forbidden":
            message = f"{path}: champ non prévu ; respectez strictement le schéma."
        else:
            message = (
                f"{path}: contrainte du schéma non respectée ({code}). "
                "Relisez les types, choix autorisés et règles de cohérence du schéma."
            )
        messages.append(message)
    return tuple(messages)
