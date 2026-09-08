"""Baseline schema (units, leases, rule_checks, issues, issue_photos, work_orders)

This is a baseline, not a step in the normal day-to-day flow - a brand
new dev environment still gets its tables from app/main.py's
Base.metadata.create_all() the first time the app runs (see README),
exactly as before Alembic was added to this project. This revision
exists for two things instead:

  - a database that already exists from before Alembic was added can be
    marked as being at this exact state with one command
    (`alembic stamp a1b2c3d4e5f6`, see README's "Migrations" section)
    so Alembic never tries to re-create tables that are already there;
  - `alembic upgrade head` against a genuinely empty database (no
    app.db at all yet) produces a complete, correct schema in one
    command, for anyone who wants that instead of starting the app.

Mirrors app/db/models.py exactly as it stood immediately before the
follow-up revision (b7c8d9e0f1a2) that added reviewed_by/decision_at,
rule-check history, and the stored-photo integrity columns.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-09-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("unit_id", sa.String(), primary_key=True),
        sa.Column("property_name", sa.String(), nullable=False),
        sa.Column("building_name", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("area_sqm", sa.Float(), nullable=True),
        sa.Column("parking_bay", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("available", "occupied", name="unitstatus"),
            nullable=False,
            server_default="available",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.String(), sa.ForeignKey("units.unit_id"), nullable=True),
        sa.Column("source_document_name", sa.String(), nullable=False),
        sa.Column("landlord_name", sa.String(), nullable=True),
        sa.Column("tenant_name", sa.String(), nullable=True),
        sa.Column("landlord_signed", sa.Boolean(), nullable=True),
        sa.Column("tenant_signed", sa.Boolean(), nullable=True),
        sa.Column("commencement_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("monthly_rent", sa.Float(), nullable=True),
        sa.Column("annual_rent", sa.Float(), nullable=True),
        sa.Column("deposit_amount", sa.Float(), nullable=True),
        sa.Column("escalation_clause_text", sa.String(), nullable=True),
        sa.Column("escalation_is_defined", sa.Boolean(), nullable=True),
        sa.Column("extracted_fields", sa.JSON(), nullable=True),
        sa.Column("review_status", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("draft", "accepted", "rejected", name="leasestatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_leases_unit_id", "leases", ["unit_id"])
    op.create_index("ix_leases_status", "leases", ["status"])

    op.create_table(
        "rule_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lease_id", sa.Integer(), sa.ForeignKey("leases.id"), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column(
            "result",
            sa.Enum("PASS", "FAIL", "NOT_DETERMINABLE", name="ruleresult"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("source_clause", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rule_checks_lease_id", "rule_checks", ["lease_id"])
    op.create_index("ix_rule_checks_result", "rule_checks", ["result"])

    op.create_table(
        "issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.String(), sa.ForeignKey("units.unit_id"), nullable=False),
        sa.Column("reported_by", sa.String(), nullable=True),
        sa.Column("condition_summary", sa.String(), nullable=True),
        sa.Column("contents_summary", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "in_progress", "resolved", name="issuestatus"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_issues_unit_id", "issues", ["unit_id"])
    op.create_index("ix_issues_status", "issues", ["status"])

    op.create_table(
        "issue_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issues.id"), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("condition_assessment", sa.String(), nullable=True),
        sa.Column("contents_detected", sa.JSON(), nullable=True),
        sa.Column("damage_notes", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("assessed_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_issue_photos_issue_id", "issue_photos", ["issue_id"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "issue_id", sa.Integer(), sa.ForeignKey("issues.id"), nullable=False, unique=True
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "accepted", "rejected", name="workorderstatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_work_orders_issue_id", "work_orders", ["issue_id"], unique=True)
    op.create_index("ix_work_orders_status", "work_orders", ["status"])


def downgrade() -> None:
    op.drop_table("work_orders")
    op.drop_table("issue_photos")
    op.drop_table("issues")
    op.drop_table("rule_checks")
    op.drop_table("leases")
    op.drop_table("units")
