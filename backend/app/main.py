from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS, FRONTEND_DIR, UPLOAD_DIR
from app.db.base import Base, engine, SessionLocal
from app.db.seed import seed_units
from app.api import leases, units, issues


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all() only ever *creates* tables that don't exist yet - it
    # never alters a table that's already there. That's exactly right
    # for a brand-new dev database (this always leaves it matching the
    # current models.py, since there's nothing pre-existing to conflict
    # with), but it is NOT how a schema change reaches a database that
    # already exists - that's what backend/alembic/ is for. See the
    # README's "Migrations" section: an existing app.db needs a one-time
    # `alembic stamp <baseline revision>` and then `alembic upgrade
    # head` to actually receive new columns/indexes going forward.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_units(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Lease & Property Issue Agent", lifespan=lifespan)

# No CORS at all unless someone asks for it. The frontend is served from
# this same app (see the mount at the bottom of this file), so the page
# and the API share an origin and the browser never treats a call from
# one to the other as cross-origin. This used to be allow_origins=["*"],
# which - next to review endpoints that have no authentication - let any
# page the user had open in another tab read from and POST to this app.
# Set CORS_ORIGINS (comma-separated, see app/config.py) only if you are
# serving the frontend somewhere else; it never becomes a wildcard.
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(leases.router)
app.include_router(units.router)
app.include_router(issues.router)

# Serves uploaded issue photos back out (e.g. /uploads/<sha256>.jpg - see
# app/services/issue_reporting.py's content-addressed storage) so a
# reviewer can see the actual photo next to its AI assessment, not just
# the assessment text.
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok"}


# Mounted last, and at "/", so it is the fallback for anything the API
# routes above did not claim. Starlette matches registered routes before
# mounts, so /leases, /units, /issues, /uploads, /health and /docs still
# resolve to the API - tests/test_app_wiring.py exists to prove that
# rather than assume it. html=True makes "/" serve index.html.
# Guarded on exists() so the backend still starts if it is run without
# the frontend/ directory beside it (a container that ships the API
# alone, for instance).
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
