"""Add Lease.renewal_terms_text and termination_terms_text

The brief's Part A field list names "renewal and termination terms"
directly alongside parties, dates, rent, deposit and the escalation
clause - these two columns close that gap the same way every other
extracted field works: a nullable text column, populated (or left null,
never guessed) by whichever LeaseExtractor ran, individually
accept/reject/edit-able through the same per-field review flow as
everything else (see app/api/leases.py:EDITABLE_LEASE_FIELDS).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-08 00:00:04.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leases", sa.Column("renewal_terms_text", sa.String(), nullable=True))
    op.add_column("leases", sa.Column("termination_terms_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("leases", "termination_terms_text")
    op.drop_column("leases", "renewal_terms_text")
