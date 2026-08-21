"""Record which guideline family a document belongs to, and whether it holds a position.

Retrieval currently fuses both search legs into one ranked list and returns the best ten.
Measured on the loaded corpus, for a prostate question about active surveillance: NCCN 48
eligible passages, EAU 23, AFU 18 — and the top ten returns NCCN 8, AFU 2, EAU 0. EAU
disappears, not because it says nothing but because one global ranking makes families compete
for the same slots and the family with the most material takes most of them.

If EAU's position differed from NCCN's, the document would present a consensus that does not
exist, with real citations, and nothing would signal the omission. The clinician's requirement
is the opposite: *détecter le conflit → récupérer toutes les positions → les afficher
clairement → laisser la RCP trancher*.

Stratifying retrieval by family needs the family to be a property rather than a string
convention. It was derived by splitting `document_id` on a dot, which works by accident of the
corpus agent's naming.

`position_domains` is the one that decides correctness rather than convenience. It names the
`clinical_purpose` domains a source may hold a position in: AFU, EAU and NCCN cover treatment
choice; SIOG and ESPEN cover their own domains and never that; WHO/IARC and RECIST cover none.
Comparing a specialist reference as a peer produces a divergence that does not exist — SIOG is
not disagreeing about treatment, it is answering a different question — and a false conflict
shown to a clinician is the failure most likely to end their trust in the mechanism.

A boolean was the first shape and was already wrong for a source we hold: ESPEN holds no
position on treatment choice, but `false` reads as "holds no position", and it has plenty on
nutritional support.

All three are nullable: every release before v5 declared none of them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5a91c73b2d4"
down_revision: str | None = "d4e8b1c60f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_documents", sa.Column("source_family", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "evidence_documents", sa.Column("source_role", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "evidence_documents",
        sa.Column("position_domains", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    # Retrieval will filter by family per query, once per applicable family.
    op.create_index(
        "ix_evidence_documents_source_family", "evidence_documents", ["source_family"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_documents_source_family", table_name="evidence_documents")
    op.drop_column("evidence_documents", "position_domains")
    op.drop_column("evidence_documents", "source_role")
    op.drop_column("evidence_documents", "source_family")
