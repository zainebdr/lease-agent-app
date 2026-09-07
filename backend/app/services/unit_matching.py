"""
Matches free-text unit references extracted from a lease (e.g. "Apartment
1204, Tower B") against the Unit table seeded from units.json.

Kept intentionally simple (normalized substring match) since the AI
extraction layer is what's being demonstrated here, not fuzzy matching
sophistication — but isolated in its own function so it can be swapped
for a smarter matcher (e.g. embedding similarity) without touching the
extraction or rule-engine code.
"""
import re

from sqlalchemy.orm import Session

from app.db.models import Unit


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def match_unit(db: Session, unit_reference_text: str | None) -> Unit | None:
    if not unit_reference_text:
        return None

    normalized_ref = _normalize(unit_reference_text)
    units = db.query(Unit).all()

    for unit in units:
        if _normalize(unit.unit_id) in normalized_ref:
            return unit
        if _normalize(unit.label) in normalized_ref:
            return unit

    # Loose fallback: look for a bare apartment number in the reference text
    number_match = re.search(r"\b(\d{3,4})\b", unit_reference_text)
    if number_match:
        number = number_match.group(1)
        for unit in units:
            if number in unit.unit_id or number in unit.label:
                return unit

    return None
