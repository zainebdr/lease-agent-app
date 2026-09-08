"""Add IssuePhoto.original_filename and processing_status

Two more traceability/robustness gaps closed:

  - IssuePhoto never recorded the uploader's original filename -
    file_path is content-addressed (its name is the sha256, see
    app/services/issue_reporting.py:_save_photo) on purpose, so there
    was no way to show a human "your photo 'IMG_4021.jpg'" without this
    separate column.
  - A single photo's AI assessment call could fail (provider error, or a
    response that fails validation against app/ai/schemas.py's
    PhotoAssessmentPayload) with no way to tell a real assessment apart
    from the placeholder result produced when that happens - both just
    looked like a normal, if generic, assessment. Adds
    processing_status ("processed" / "failed") so that distinction is
    visible instead of silent - see
    app/services/issue_reporting.py:_assess_photo.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-08 00:00:03.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issue_photos", sa.Column("original_filename", sa.String(), nullable=True))
    op.add_column(
        "issue_photos",
        sa.Column(
            "processing_status",
            sa.Enum("processed", "failed", name="photoprocessingstatus"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("issue_photos", "processing_status")
    op.drop_column("issue_photos", "original_filename")
    # On Postgres, the ENUM type created by the add_column above survives
    # a plain drop_column and needs an explicit drop so upgrade() can run
    # again cleanly; SQLite has no native enum type, so this is a no-op
    # there (matching the sa.Enum column type used above).
    sa.Enum(name="photoprocessingstatus").drop(op.get_bind(), checkfirst=True)
