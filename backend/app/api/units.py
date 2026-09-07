from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Unit
from app.schemas.lease import LeaseOut
from app.schemas.issue import IssueOut

router = APIRouter(prefix="/units", tags=["units"])


@router.get("")
def list_units(db: Session = Depends(get_db)):
    units = db.query(Unit).all()
    return [
        {
            "unit_id": u.unit_id,
            "label": u.label,
            "building_name": u.building_name,
            "status": u.status,
            "type": u.type,
        }
        for u in units
    ]


@router.get("/{unit_id}")
def get_unit_detail(unit_id: str, db: Session = Depends(get_db)):
    """
    Returns a unit with its lease(s) AND its reported issues (each with
    its draft/reviewed work order) attached - this is the single screen
    the brief asks for: an owner opens a unit and sees the lease and the
    open issues raised against it in one place. The unit is the join key
    both features hang off; nothing about how Part A or Part B store
    their own data needed to change to make this join possible.
    """
    unit = db.get(Unit, unit_id)
    if not unit:
        raise HTTPException(404, "Unit not found.")

    return {
        "unit": {
            "unit_id": unit.unit_id,
            "label": unit.label,
            "building_name": unit.building_name,
            "property_name": unit.property_name,
            "type": unit.type,
            "area_sqm": unit.area_sqm,
            "status": unit.status,
        },
        "leases": [LeaseOut.model_validate(l).model_dump(mode="json") for l in unit.leases],
        "issues": [IssueOut.model_validate(i).model_dump(mode="json") for i in unit.issues],
    }
