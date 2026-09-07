"""
Central configuration. All environment-dependent values live here so
swapping databases or AI providers never means touching business logic.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# --- Database -----------------------------------------------------------
# SQLite for local/dev/take-home use. To move to Postgres in production,
# change only this value (e.g. "postgresql://user:pass@host:5432/db") —
# no model or query code changes required.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'app.db'}")

# --- AI provider ----------------------------------------------------------
# If ANTHROPIC_API_KEY (or OPENAI_API_KEY) is set, the app uses a real LLM
# call for lease extraction and photo assessment. Otherwise it falls back
# automatically to a deterministic rule-based mock — no key required to
# run the whole application end to end. See app/ai/factory.py.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USE_REAL_LLM = bool(ANTHROPIC_API_KEY or OPENAI_API_KEY)

# --- Static reference data ------------------------------------------------
RULESET_PATH = DATA_DIR / "owner_ruleset.json"
UNITS_SEED_PATH = DATA_DIR / "units.json"

# --- Uploads ----------------------------------------------------------
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
