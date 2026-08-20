"""Let a release mark a passage as not retrievable at all.

Every other routing field says *which* patient a passage applies to. This one says the
passage must not be offered to any patient.

Corpus v3.1 uses it for the treatment recommendations in the SIOG 2010 prostate paper, which
sits in the same document as geriatric assessment guidance that still stands. A clinician
confirmed that split: keep the assessment, block the treatment.

A superseded recommendation is more dangerous than an irrelevant one. An irrelevant passage
is visibly off topic. A superseded one is on topic, reads as authoritative, and carries a
real citation and page number, so nothing downstream has any reason to question it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e8b1c60f27"
down_revision: str | None = "c9d2e71f4a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Default true: a release that says nothing about eligibility means every passage is
    # retrievable, which is how every release before v3.1 behaved.
    op.add_column(
        "evidence_chunks",
        sa.Column(
            "retrieval_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "evidence_chunks", sa.Column("retrieval_exclusion_reason", sa.String(80), nullable=True)
    )
    # The filter reads this on every query, and almost every row is true, so a partial index
    # over the exceptions is the useful shape.
    op.create_index(
        "ix_evidence_chunks_ineligible",
        "evidence_chunks",
        ["retrieval_eligible"],
        postgresql_where=sa.text("NOT retrieval_eligible"),
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_chunks_ineligible", table_name="evidence_chunks")
    op.drop_column("evidence_chunks", "retrieval_exclusion_reason")
    op.drop_column("evidence_chunks", "retrieval_eligible")
