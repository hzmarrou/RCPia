"""record the agent runtime version

Provider and model say which brain answered a question. They do not say which harness put
it, and the two are different parts of the same deployment: the runtime builds the prompt,
executes the tools, compacts the context and decides when the agent has finished.

Until now that was resolved from PATH and recorded nowhere, so every opinion in this
database was produced by an unknown version of the runtime. `UploadedDocument` already
records `parser_provider_version` for the same reason.

Nullable, and backfilled with nothing: the runs already stored were produced by a version
this migration cannot know, and inventing one would be worse than admitting it.

Revision ID: a41c7d9e6b02
Revises: e5a91c73b2d4
Create Date: 2026-08-30 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a41c7d9e6b02"
down_revision: str | None = "e5a91c73b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("runtime_version", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "runtime_version")
