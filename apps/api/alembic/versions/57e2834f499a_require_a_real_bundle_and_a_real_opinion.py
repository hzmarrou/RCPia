"""require a real bundle and a real opinion

The previous constraint asked whether the `inputs` key existed, which `{"inputs": null}`
satisfies, and whether `output` was not null, which `{}` satisfies. Both admitted a
successful run carrying nothing anyone could review.

This asserts the shape instead: the bundle's inputs are an object naming the case they
were built for, and the output is an object carrying the two fields that make a
recommendation reviewable — what to do, and why.

Revision ID: 57e2834f499a
Revises: df1cb14d1d8a
Create Date: 2026-08-17 18:20:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = '57e2834f499a'
down_revision: str | None = 'df1cb14d1d8a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = 'ck_agent_runs_successful_runs_are_reviewable'

WAS = (
    "status <> 'succeeded' OR ("
    "ledger_version_id IS NOT NULL AND ledger_version IS NOT NULL "
    "AND job_id IS NOT NULL "
    "AND provider IS NOT NULL AND model IS NOT NULL "
    "AND session_blob_key IS NOT NULL "
    "AND jsonb_exists(bundle, 'inputs'))"
)

NOW = (
    "status <> 'succeeded' OR ("
    "ledger_version_id IS NOT NULL AND ledger_version IS NOT NULL "
    "AND job_id IS NOT NULL "
    "AND provider IS NOT NULL AND model IS NOT NULL "
    "AND session_blob_key IS NOT NULL "
    "AND jsonb_typeof(bundle -> 'inputs') = 'object' "
    "AND length(coalesce(bundle -> 'inputs' ->> 'case_id', '')) > 0 "
    "AND jsonb_typeof(output) = 'object' "
    "AND length(coalesce(output ->> 'recommendation', '')) > 0 "
    "AND length(coalesce(output ->> 'rationale', '')) > 0)"
)


def upgrade() -> None:
    # op.f: the name is already the final one, so the naming convention must not prefix
    # it a second time.
    op.drop_constraint(op.f(CONSTRAINT), 'agent_runs', type_='check')
    op.create_check_constraint(op.f(CONSTRAINT), 'agent_runs', NOW)


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), 'agent_runs', type_='check')
    op.create_check_constraint(op.f(CONSTRAINT), 'agent_runs', WAS)
