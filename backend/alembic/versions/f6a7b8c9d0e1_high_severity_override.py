"""Add Lease.high_severity_override_reason and high_severity_overridden_rules

A lease can no longer be finalized to "accepted" while a severity="high"
rule is FAILing (app/api/leases.py:_apply_high_severity_gate). Before
this, `severity` was loaded from data/owner_ruleset.json, stored on every
rule_checks row and rendered in the UI, but read by no decision anywhere -
so a lease with a failing high-severity check accepted with a plain 200
and the ruleset was decorative.

The gate allows an explicit override rather than an unconditional block,
because an owner accepting a lease that breaks one of their own rules by
prior agreement is a real business case - and a system that simply
refuses gets worked around outside the system, where nothing is audited.
These two columns are what keep the override honest: the stated reason,
and exactly which rule ids it covered. NULL means no override was needed,
never "overridden silently".

Plain add_column here, not batch_alter_table: neither column carries a
constraint, and SQLite handles ALTER TABLE ADD of an unconstrained
nullable column fine. Batch mode is only needed where a constraint is
involved - see c3d4e5f6a7b8, where a foreign key made it mandatory.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leases", sa.Column("high_severity_override_reason", sa.String(), nullable=True))
    op.add_column("leases", sa.Column("high_severity_overridden_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leases", "high_severity_overridden_rules")
    op.drop_column("leases", "high_severity_override_reason")
