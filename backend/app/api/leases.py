from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Lease, Unit
from app.schemas.lease import LeaseOut, LeaseReviewRequest
from app.services import lease_extraction

router = APIRouter(prefix="/leases", tags=["leases"])


@router.post("/upload", response_model=LeaseOut)
def upload_lease(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = file.file.read()
    try:
        document_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Only plain-text lease documents are supported in this build. "
                                  "Extend app/api/leases.py to add PDF text extraction.")

    lease = lease_extraction.process_lease_upload(db, document_text, file.filename)
    return lease


@router.get("/{lease_id}", response_model=LeaseOut)
def get_lease(lease_id: int, db: Session = Depends(get_db)):
    lease = db.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Lease not found.")
    return lease


@router.post("/{lease_id}/review", response_model=LeaseOut)
def review_lease(lease_id: int, review: LeaseReviewRequest, db: Session = Depends(get_db)):
    lease = db.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Lease not found.")

    review_status = dict(lease.review_status or {})

    for action in review.field_actions:
        if action.action == "accept":
            review_status[action.field_name] = "accepted"
        elif action.action == "reject":
            review_status[action.field_name] = "rejected"
        elif action.action == "edit":
            review_status[action.field_name] = "edited"
            if hasattr(lease, action.field_name):
                setattr(lease, action.field_name, action.new_value)

    lease.review_status = review_status

    if review.finalize:
        any_rejected = "rejected" in review_status.values()
        if any_rejected:
            lease.status = "rejected"
        else:
            lease.status = "accepted"
            if lease.unit_id:
                unit = db.get(Unit, lease.unit_id)
                if unit:
                    unit.status = "occupied"

    db.commit()
    db.refresh(lease)
    return lease
