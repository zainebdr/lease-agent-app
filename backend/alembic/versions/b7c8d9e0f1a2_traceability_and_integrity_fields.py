"""Add reviewed_by/decision_at, rule-check history, photo integrity fields, and the one-accepted-lease-per-unit guard

Fixes four gaps in the traceability story:

  - Lease/WorkOrder had no record of *who* accepted or rejected them, or
    *when* - adds reviewed_by (String) + decision_at (DateTime) to both.
  - RuleCheck rows were deleted and recreated on every review call
    (see app/services/lease_extraction.py:refresh_rule_checks), so the
    history that would show "this was FAIL, then a human edited the
    deposit and it went PASS" was destroyed. Adds is_current (Boolean)
    and superseded_at (DateTime) so old rows are kept, just marked
    superseded, instead of deleted.
  - IssuePhoto stored no sha256/size/content_type, so there was no way
    to verify a stored file's integrity or notice a photo had been
    re-uploaded. Adds sha256, size_bytes, content_type.
  - Nothing stopped two leases both being 'accepted' for the same unit.
    Adds a partial unique index on leases.unit_id, scoped to
    status = 'accepted'.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leases", sa.Column("reviewed_by", sa.String(), nullable=True))
    op.add_column("leases", sa.Column("decision_at", sa.DateTime(), nullable=True))
    # Partial unique index: only rows where status = 'accepted'
    # participate, so a unit can still accumulate any number of
    # draft/rejected leases over time - only one *accepted* lease at a
    # time is exclusive. See app/db/models.py:Lease.__table_args__.
    op.create_index(
        "uq_leases_one_accepted_per_unit",
        "leases",
        ["unit_id"],
        unique=True,
        sqlite_where=sa.text("status = 'accepted'"),
        postgresql_where=sa.text("status = 'accepted'"),
    )

    op.add_column("work_orders", sa.Column("reviewed_by", sa.String(), nullable=True))
    op.add_column("work_orders", sa.Column("decision_at", sa.DateTime(), nullable=True))

    op.add_column(
        "rule_checks",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("rule_checks", sa.Column("superseded_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_rule_checks_lease_id_is_current", "rule_checks", ["lease_id", "is_current"]
    )

    op.add_column("issue_photos", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("issue_photos", sa.Column("size_bytes", sa.Integer(), nullable=True))
    op.add_column("issue_photos", sa.Column("content_type", sa.String(), nullable=True))
    op.create_index("ix_issue_photos_sha256", "issue_photos", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_issue_photos_sha256", table_name="issue_photos")
    op.drop_column("issue_photos", "content_type")
    op.drop_column("issue_photos", "size_bytes")
    op.drop_column("issue_photos", "sha256")

    op.drop_index("ix_rule_checks_lease_id_is_current", table_name="rule_checks")
    op.drop_column("rule_checks", "superseded_at")
    op.drop_column("rule_checks", "is_current")

    op.drop_column("work_orders", "decision_at")
    op.drop_column("work_orders", "reviewed_by")

    op.drop_index("uq_leases_one_accepted_per_unit", table_name="leases")
    op.drop_column("leases", "decision_at")
    op.drop_column("leases", "reviewed_by")
