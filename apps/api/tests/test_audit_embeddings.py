"""The vector audit, which had no tests at all.

It exists because a claim was made without evidence — "the corpus is clean" — on the strength
of a check that could not see the failure it was looking for. An audit written for that reason
and then left untested is the same mistake with extra steps.
"""

import math

import pytest

from rcpia.evidence.audit_embeddings import (
    SEPARATION_REQUIRED,
    AuditError,
    cosine_distance,
)


def unit(index: int, width: int = 8) -> list[float]:
    values = [0.0] * width
    values[index] = 1.0
    return values


# --------------------------------------------------------------------------------------
# The distance itself
# --------------------------------------------------------------------------------------


def test_a_vector_compared_with_itself_is_zero() -> None:
    assert cosine_distance(unit(0), unit(0)) == pytest.approx(0.0, abs=1e-12)


def test_orthogonal_vectors_are_one_apart() -> None:
    assert cosine_distance(unit(0), unit(1)) == pytest.approx(1.0)


def test_magnitude_does_not_matter() -> None:
    """Cosine, not Euclidean: a vector and twice itself point the same way."""
    doubled = [value * 2 for value in unit(3)]
    assert cosine_distance(unit(3), doubled) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_component_is_refused(bad: float) -> None:
    """**`NaN > TOLERANCE` is False.**

    A non-finite component would sail through every threshold as a match — the same trap
    documented at length in `rerank._parse_scores` and then walked into here, in a new file, by
    the same author. A distance that cannot be compared is not a small distance.
    """
    corrupted = unit(0)
    corrupted[2] = bad
    with pytest.raises(AuditError, match="not finite"):
        cosine_distance(corrupted, unit(0))
    with pytest.raises(AuditError, match="not finite"):
        cosine_distance(unit(0), corrupted)


def test_a_zero_vector_is_refused_rather_than_scored() -> None:
    """Returning 1.0 for it would read as "completely different" — a real answer to a question
    that has none, and one that happens to look like a mismatch."""
    with pytest.raises(AuditError, match="zero magnitude"):
        cosine_distance([0.0] * 8, unit(0))


def test_the_distance_is_symmetric() -> None:
    left, right = unit(0), [0.6, 0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert cosine_distance(left, right) == pytest.approx(cosine_distance(right, left))


def test_vectors_of_different_widths_are_refused() -> None:
    """`zip(strict=True)`: comparing a 1536-vector with a 1024-vector silently over the
    shorter one would produce a plausible number from an impossible comparison."""
    with pytest.raises(ValueError):
        cosine_distance(unit(0, width=8), unit(0, width=4))


# --------------------------------------------------------------------------------------
# What the separation requirement means
# --------------------------------------------------------------------------------------


def test_the_separation_requirement_leaves_room_for_noise() -> None:
    """The audit calls a passage proven when its own re-embedding sits at least
    `SEPARATION_REQUIRED` times closer than its nearest distinct neighbour.

    Measured on the loaded corpus, that margin cannot be assumed globally: same-text noise
    reaches 0.006831 while the closest pair of distinct passages sit 0.000998 apart. There is
    no single threshold, which is why the decision is made per passage against that passage's
    own neighbour — and why a passage too close to its neighbour is reported as undecidable
    rather than folded into a pass.
    """
    assert SEPARATION_REQUIRED > 1.0

    noise, neighbour = 0.0001, 0.01
    assert noise * SEPARATION_REQUIRED < neighbour  # decidable

    noise, neighbour = 0.006831, 0.000998
    assert noise * SEPARATION_REQUIRED >= neighbour  # not decidable, and must say so


def test_a_swap_scores_further_than_noise_for_a_well_separated_passage() -> None:
    """The property the audit rests on: a vector belonging to another passage cannot land
    closer than that other passage is."""
    stored, other = unit(0), unit(1)
    noise = [1.0, 0.0005, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    assert cosine_distance(stored, noise) < 1e-6
    assert cosine_distance(stored, other) == pytest.approx(1.0)


def test_distance_never_exceeds_two_for_unit_vectors() -> None:
    """Sanity bound: cosine distance runs 0..2, so a reported 8.0 means the arithmetic is
    wrong rather than the corpus."""
    opposed = [-value for value in unit(0)]
    distance = cosine_distance(unit(0), opposed)
    assert math.isfinite(distance)
    assert 0.0 <= distance <= 2.0
