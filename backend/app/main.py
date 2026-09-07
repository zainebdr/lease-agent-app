from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Lease & Property Issue Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a local take-home; restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}
