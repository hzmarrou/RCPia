"""persist every chunk a fact spans

An imaging report can run across a heading, several findings paragraphs and a conclusion.
`source_chunk_id` names only the first one -- often the acquisition-protocol paragraph the
report opens with -- so a citation could point a reviewer at a page that does not contain
the sentence the excerpt actually quotes. This adds the full ordered set of contributing
chunks alongside it; `source_chunk_id` is unchanged and keeps its FK-enforced traceability.

Revision ID: c3f9a1e7d204
Revises: a726fa31712b
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c3f9a1e7d204'
down_revision: str | None = 'a726fa31712b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and backfilled with nothing: every fact recorded before this migration keeps
    # citing correctly through `source_chunk_id` alone, exactly as it always has. Only newly
    # extracted facts gain the fuller picture.
    op.add_column(
        'extracted_facts',
        sa.Column(
            'source_chunk_ids',
            postgresql.ARRAY(sa.Uuid()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('extracted_facts', 'source_chunk_ids')
