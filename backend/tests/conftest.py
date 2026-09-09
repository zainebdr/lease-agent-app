"""
Shared pytest fixtures.

Every test gets its own fresh, in-memory SQLite database (via
dependency_overrides on get_db) - never the app's real app.db file, and
never state shared between tests. The suite always exercises the mock AI
implementations (MockLeaseExtractor / MockImageAssessor): USE_REAL_LLM is
only true when ANTHROPIC_API_KEY is set (app/config.py), and nothing here
sets it, so the suite runs deterministically with no API key, no network
call, and no cost - same reason the app itself defaults to the mock.

The FastAPI app's own lifespan (app/main.py) creates tables and seeds
units against the *real* app.db on startup - fine for actually running
the app, but not something a test run should touch. These fixtures build
their own engine/session/tables instead of triggering that lifespan, so
`pytest` never reads or writes a reviewer's real database file.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, get_db
from app.db.seed import seed_units
from app.services import issue_reporting


@pytest.fixture(autouse=True)
def isolated_upload_dir(tmp_path, monkeypatch):
    """Every test writes uploaded photos into its own temp directory.

    The suite isolated the database from the start but not the
    filesystem, so `pytest` wrote real image files into backend/uploads/
    on every run - a test suite quietly leaving artefacts in the
    repository it is testing. issue_reporting binds UPLOAD_DIR at import
    time (`from app.config import UPLOAD_DIR`), so patching the module's
    own reference is what actually redirects the writes; patching
    app.config alone would not.
    """
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(issue_reporting, "UPLOAD_DIR", upload_dir)
    return upload_dir


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        seed_units(session)  # the same units.json fixtures the real app seeds
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """A TestClient wired to the same session as db_session, so a test can
    call the API and then inspect the DB directly in one place.

    Deliberately built without the app's lifespan (see module docstring)
    - table creation and seeding are this fixture's job for the duration
    of a test, not app.main's.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
