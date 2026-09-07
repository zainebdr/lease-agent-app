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
# LeaseExtractor and ImageAssessor (app/ai/base.py) are plain interfaces -
# nothing about them, or about the factory that hands them out
# (app/ai/factory.py), is tied to any one AI vendor. Swapping in a
# different provider is a new class behind the same interface plus one
# branch in the factory, not a rewrite of any calling code.
#
# This build ships one real, provider-backed implementation - Anthropic's
# Claude, via ANTHROPIC_API_KEY - because a single credential is enough
# to prove the pattern end to end without asking a reviewer to juggle
# multiple providers' keys for a take-home. OPENAI_API_KEY is read and
# kept here as the reserved slot for that next provider; it deliberately
# does NOT flip USE_REAL_LLM on its own, since no concrete class reads it
# yet - if it did, setting only an OpenAI key would silently attempt to
# build the Anthropic client with no key and crash, instead of clearly
# falling back to the mock.
#
# With no key set at all, the app falls back automatically to a
# deterministic rule-based mock for both features - no key required to
# run the whole application end to end.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # reserved: not wired to an implementation yet
USE_REAL_LLM = bool(ANTHROPIC_API_KEY)

# --- Static reference data ------------------------------------------------
RULESET_PATH = DATA_DIR / "owner_ruleset.json"
UNITS_SEED_PATH = DATA_DIR / "units.json"

# --- Uploads ----------------------------------------------------------
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
