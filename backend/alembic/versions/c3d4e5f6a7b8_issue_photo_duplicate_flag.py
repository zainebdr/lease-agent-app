"""Add IssuePhoto.duplicate_of_id (content-hash duplicate detection)

Doesn't change upload behavior - a photo whose sha256 matches an earlier
row is still stored (well, its metadata row is - the file itself was
already de-duplicated on disk by the content-addressed storage added in
b7c8d9e0f1a2) and assessed exactly like any other photo. This just makes
the relationship visible instead of something you'd have to notice by
comparing sha256 values by hand - see app/services/issue_reporting.py.

Points at the *earliest* row with the same hash, so a photo re-uploaded
many times always resolves back to one original, never a chain of
duplicates-of-duplicates.

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-09-08 00:00:02.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table, not a plain add_column: this column carries a
    # FOREIGN KEY, and SQLite cannot ALTER TABLE ADD a constrained
    # column - a plain op.add_column here raises
    #   NotImplementedError: No support for ALTER of constraints in
    #   SQLite dialect
    # and leaves the database stranded on the previous revision. Batch
    # mode does the copy-and-move rebuild SQLite needs; on Postgres,
    # where a plain ALTER would have worked, this compiles to the same
    # ALTER, so one code path is correct on both.
    with op.batch_alter_table("issue_photos") as batch_op:
        batch_op.add_column(sa.Column("duplicate_of_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_issue_photos_duplicate_of_id",
            "issue_photos",
            ["duplicate_of_id"],
            ["id"],
        )
    op.create_index(
        "ix_issue_photos_duplicate_of_id", "issue_photos", ["duplicate_of_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_issue_photos_duplicate_of_id", table_name="issue_photos")
    with op.batch_alter_table("issue_photos") as batch_op:
        batch_op.drop_constraint("fk_issue_photos_duplicate_of_id", type_="foreignkey")
        batch_op.drop_column("duplicate_of_id")
