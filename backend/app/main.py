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

# Serves uploaded issue photos back out (e.g. /uploads/issue_3_0_photo.jpg)
# so a reviewer can see the actual photo next to its AI assessment, not
# just the assessment text.
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/health")
def health():
    return {"status": "ok"}
