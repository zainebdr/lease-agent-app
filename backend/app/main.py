from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import UPLOAD_DIR
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a local take-home; restrict in production
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
