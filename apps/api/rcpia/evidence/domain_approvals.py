"""Which sources a clinician has approved to hold positions, and on what.

**A flag on a temporary object is not an approval record.** `domain_approval` first existed
only on the `Manifest` built during ingestion — not stored, not auditable, and readable by
nothing after the load finished. Anyone could have assumed something was enforcing it.

An approval is a clinical decision with a person behind it, so it is recorded the way the rest
of this project records those: explicitly, in version control, with who and when. A release
cannot assert its own approval, and neither can this module infer one.

**Empty is the honest state.** No clinician has reviewed any source's declared domains. Until
that changes, the divergence comparison has no approved authorities and must say so rather
than proceed with provisional ones — the whole point of recording the distinction.

Adding an entry means a named clinician has read that source's `position_domains` and agreed
the source holds positions there. It is not a judgement about the source's quality, only about
its scope.
"""

from dataclasses import dataclass
from datetime import date

PROVISIONAL = "provisional"
CLINICIAN_APPROVED = "clinician_approved"


@dataclass(frozen=True)
class Approval:
    """One clinician's sign-off on one source's declared domains."""

    reference_id: str
    # The domains as approved. A release later declaring different ones is not covered by this
    # record, which is why the approval names them rather than pointing at whatever arrives.
    domains: tuple[str, ...]
    clinician: str
    approved_on: date
    # The corpus release the approved declaration came from, so a changed declaration is
    # visibly outside the approval rather than silently inheriting it.
    release_id: str


# No source has been reviewed. See the module docstring: this being empty is a statement.
APPROVALS: tuple[Approval, ...] = ()

_BY_REFERENCE = {approval.reference_id: approval for approval in APPROVALS}


def approval_state(
    reference_id: str, *, domains: tuple[str, ...] | None, release_id: str
) -> str:
    """`clinician_approved` only when a record covers exactly these domains, in this release.

    Approving a source once does not approve whatever it declares later. A release that widens
    a source's domains, or arrives under a new id, falls back to `provisional` — which is the
    behaviour that makes the record worth keeping rather than a rubber stamp.
    """
    approval = _BY_REFERENCE.get(reference_id)
    if approval is None or domains is None:
        return PROVISIONAL
    if set(approval.domains) != set(domains) or approval.release_id != release_id:
        return PROVISIONAL
    return CLINICIAN_APPROVED
