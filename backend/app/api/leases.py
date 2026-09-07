from datetime import date

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.enums import LeaseStatus, ReviewState, UnitStatus
from app.db.models import Lease, Unit
from app.schemas.lease import LeaseOut, LeaseReviewRequest
from app.services import lease_extraction

router = APIRouter(prefix="/leases", tags=["leases"])

# Only these columns may be changed via a review "edit" action. Anything
# not on this list (status, unit_id, id, extracted_fields, review_status
# itself, ...) can only change through the dedicated flows below - never
# directly from a client-supplied field_name.
EDITABLE_LEASE_FIELDS = {
    "landlord_name", "tenant_name", "landlord_signed", "tenant_signed",
    "commencement_date", "expiry_date", "term_months",
    "monthly_rent", "annual_rent", "deposit_amount",
    "escalation_clause_text", "escalation_is_defined",
}

# Fields whose column type is a real date - an edit sends a plain ISO
# string ("2026-03-01"), which must be converted before assignment.
# SQLite is loose enough to sometimes accept a raw string here, but that
# is not something to rely on (Postgres, e.g., would not).
_DATE_FIELDS = {"commencement_date", "expiry_date"}


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

    if lease.status != LeaseStatus.DRAFT:
        # Finalized means finalized - a lease that is already accepted or
        # rejected (and may already have flipped its unit's status) must
        # not be silently editable through this endpoint. Reopening a
        # finalized lease deliberately isn't supported yet; that would be
        # its own explicit, logged action, not a side effect of a normal
        # review call landing on the wrong lease.
        raise HTTPException(
            409,
            f"This lease is already '{lease.status.value}' and can no longer be reviewed.",
        )

    review_status = dict(lease.review_status or {})

    for action in review.field_actions:
        if action.action == "accept":
            review_status[action.field_name] = ReviewState.ACCEPTED.value
        elif action.action == "reject":
            review_status[action.field_name] = ReviewState.REJECTED.value
        elif action.action == "edit":
            if action.field_name not in EDITABLE_LEASE_FIELDS:
                raise HTTPException(
                    400,
                    f"'{action.field_name}' cannot be edited via review. "
                    f"Editable fields: {sorted(EDITABLE_LEASE_FIELDS)}",
                )
            new_value = action.new_value
            if action.field_name in _DATE_FIELDS and isinstance(new_value, str):
                new_value = date.fromisoformat(new_value)
            review_status[action.field_name] = ReviewState.EDITED.value
            setattr(lease, action.field_name, new_value)

    lease.review_status = review_status

    if review.finalize:
        if ReviewState.REJECTED.value in review_status.values():
            lease.status = LeaseStatus.REJECTED
        elif ReviewState.PENDING.value in review_status.values():
            # Every field must be explicitly accepted/rejected/edited
            # before a lease can be finalized - a field nobody has looked
            # at yet is not the same as one that passed review.
            raise HTTPException(
                400,
                "Cannot finalize: some extracted fields are still 'pending'. "
                "Accept, reject, or edit every field before finalizing.",
            )
        else:
            lease.status = LeaseStatus.ACCEPTED
            if lease.unit_id:
                unit = db.get(Unit, lease.unit_id)
                if unit:
                    unit.status = UnitStatus.OCCUPIED

    db.commit()
    db.refresh(lease)
    return lease
