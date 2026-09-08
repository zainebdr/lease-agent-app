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
# (app/ai/factory.py), is tied to any one AI vendor. Two real,
# provider-backed implementations exist behind that interface:
#   - Anthropic's Claude   (app/ai/anthropic_lease_extractor.py, anthropic_image_assessor.py)
#   - OpenAI's GPT-4o      (app/ai/openai_lease_extractor.py, openai_image_assessor.py)
# A reviewer running this can use whichever key they already have, with
# zero code changes: set ANTHROPIC_API_KEY for Claude, OPENAI_API_KEY for
# GPT-4o, or neither for the deterministic mock. Both implementations are
# handed the exact same prompts (app/ai/anthropic_*.py's own
# ASSESSMENT_PROMPT / EXTRACTION_PROMPT - the OpenAI files import and
# reuse them rather than duplicating) and validate the parsed response
# against the exact same Pydantic schemas (app/ai/schemas.py), so which
# provider answers only changes the wire format of the API call itself,
# never the question
# asked or the shape enforced on the answer.
#
# AI_PROVIDER is resolved once, here, rather than as scattered "if key:"
# checks in the factory - that keeps the one precedence rule (if someone
# sets both keys, Anthropic wins - an arbitrary but fixed tie-break) in
# exactly one place instead of implicit in call order.
#
# With no key set at all, AI_PROVIDER is None and the app falls back
# automatically to a deterministic rule-based mock for both features - no
# key required to run the whole application end to end.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if ANTHROPIC_API_KEY:
    AI_PROVIDER = "anthropic"
elif OPENAI_API_KEY:
    AI_PROVIDER = "openai"
else:
    AI_PROVIDER = None

USE_REAL_LLM = AI_PROVIDER is not None  # convenience flag for call sites
                                         # that only care "mock or real",
                                         # not which real provider

# --- Static reference data ------------------------------------------------
RULESET_PATH = DATA_DIR / "owner_ruleset.json"
UNITS_SEED_PATH = DATA_DIR / "units.json"

# --- Uploads ----------------------------------------------------------
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# A client-supplied Content-Type header is not trustworthy on its own -
# a renamed .txt file can claim to be "image/jpeg" - so an uploaded issue
# photo is also opened and decoded with Pillow before it's hashed, stored,
# or sent to the AI assessor (see app/api/issues.py:_validate_photo_upload).
# 10MB comfortably covers a real phone photo without letting a single
# request's upload size balloon unbounded.
MAX_PHOTO_SIZE_BYTES = 10 * 1024 * 1024
