"""
Runs the Alembic migration chain for real, against a throwaway SQLite
file.

This test exists because the chain was broken and nothing noticed:
`alembic upgrade head` died at revision c3d4e5f6a7b8 with

    NotImplementedError: No support for ALTER of constraints in SQLite
    dialect

because SQLite cannot ALTER TABLE ADD a column carrying a FOREIGN KEY,
and left the database stranded on the previous revision. Every other
test in this suite builds its schema with Base.metadata.create_all(),
which never touches a migration - so the whole documented upgrade path
for an existing app.db was unverified. It is verified here now, on the
same engine the project defaults to.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'migrations.db'}"


def test_upgrade_head_runs_the_whole_chain_on_sqlite(db_url):
    result = _alembic(["upgrade", "head"], db_url)
    assert result.returncode == 0, result.stderr

    inspector = inspect(create_engine(db_url))
    tables = set(inspector.get_table_names())
    assert {"units", "leases", "rule_checks", "issues", "issue_photos", "work_orders"} <= tables

    # The column whose foreign key broke the chain, and the indexes that
    # a batch-mode table rebuild must not lose along the way.
    photo_columns = {c["name"] for c in inspector.get_columns("issue_photos")}
    assert "duplicate_of_id" in photo_columns
    photo_indexes = {i["name"] for i in inspector.get_indexes("issue_photos")}
    assert {"ix_issue_photos_issue_id", "ix_issue_photos_sha256"} <= photo_indexes

    lease_columns = {c["name"] for c in inspector.get_columns("leases")}
    assert {"high_severity_override_reason", "high_severity_overridden_rules"} <= lease_columns
    lease_indexes = {i["name"] for i in inspector.get_indexes("leases")}
    assert "uq_leases_one_accepted_per_unit" in lease_indexes


def test_migrated_schema_matches_the_models(db_url):
    """A migration chain that runs but drifts from models.py is only
    half a migration story: the app would work on a freshly created
    database and break on a migrated one. `alembic check` compares the
    two and fails if autogenerate would still have something to do."""
    assert _alembic(["upgrade", "head"], db_url).returncode == 0
    result = _alembic(["check"], db_url)
    assert result.returncode == 0, f"schema drift:\n{result.stdout}\n{result.stderr}"


def test_downgrade_unwinds_cleanly(db_url):
    assert _alembic(["upgrade", "head"], db_url).returncode == 0
    result = _alembic(["downgrade", "base"], db_url)
    assert result.returncode == 0, result.stderr
    assert inspect(create_engine(db_url)).get_table_names() in ([], ["alembic_version"])
