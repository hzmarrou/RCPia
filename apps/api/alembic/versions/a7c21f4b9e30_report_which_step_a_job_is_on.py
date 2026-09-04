"""report which step a job is on

Revision ID: a7c21f4b9e30
Revises: c3f9a1e7d204
Create Date: 2026-09-04 14:10:22.118934
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a7c21f4b9e30'
down_revision: str | None = 'c3f9a1e7d204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and with no default: a job enqueued before this migration, or one whose
    # handler reports nothing, has no milestone, and "unknown" is the honest reading.
    op.add_column('analysis_jobs', sa.Column('milestone', sa.String(length=50), nullable=True))
    op.add_column(
        'analysis_jobs',
        sa.Column('milestone_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('analysis_jobs', 'milestone_at')
    op.drop_column('analysis_jobs', 'milestone')
