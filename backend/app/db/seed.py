"""
Loads data/units.json into the units table on startup, idempotently.
Existing rows are left untouched so we never clobber a status that has
already been flipped to "occupied" by an accepted lease.
"""
import json

from sqlalchemy.orm import Session

from app.config import UNITS_SEED_PATH
from app.db.models import Unit


def seed_units(db: Session) -> None:
    with open(UNITS_SEED_PATH) as f:
        data = json.load(f)

    for prop in data.get("properties", []):
        for building in prop.get("buildings", []):
            for unit in building.get("units", []):
                existing = db.get(Unit, unit["unit_id"])
                if existing:
                    continue  # don't overwrite live status
                db.add(
                    Unit(
                        unit_id=unit["unit_id"],
                        property_name=prop["name"],
                        building_name=building["name"],
                        label=unit["label"],
                        type=unit.get("type"),
                        area_sqm=unit.get("area_sqm"),
                        parking_bay=unit.get("parking_bay"),
                        status=unit.get("status", "available"),
                    )
                )
    db.commit()
