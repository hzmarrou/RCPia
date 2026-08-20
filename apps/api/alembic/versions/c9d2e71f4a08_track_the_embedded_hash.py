"""Track the embedded hash separately from the passage hash.

`content_sha256` hashes the passage text; `embedding_sha256` hashes `contextual_text`, which
is what actually goes to the embedding model. They answer different questions, and conflating
them cost us a corpus release: v3 corrected routing metadata on 975 passages without changing
a word of their text, so their `content_sha256` was unchanged and the ingestion skipped them
entirely — leaving 481 passages carrying a `clinical_purpose` value the release had removed.

Also stores `not_specified_reason`, which distinguishes a passage that genuinely applies to
every setting from one the source was too vague to place. Through a filter those look
identical, and half the corpus carries the label.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d2e71f4a08"
down_revision: str | None = "b8c47a25e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence_chunks", sa.Column("embedding_sha256", sa.String(64), nullable=True))
    op.add_column(
        "evidence_chunks", sa.Column("not_specified_reason", sa.String(50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("evidence_chunks", "not_specified_reason")
    op.drop_column("evidence_chunks", "embedding_sha256")
