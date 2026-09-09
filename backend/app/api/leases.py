from datetime import datetime, timezone

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.enums import LeaseStatus, ReviewState, RuleResult, UnitStatus
from app.db.models import Lease, Unit
from app.schemas.lease import LeaseOut, LeaseReviewRequest
from app.services import lease_extraction


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    "renewal_terms_text", "termination_terms_text",
}


def _reject_if_unit_already_leased(db: Session, lease: Lease) -> None:
    """Raises 409 if some *other* lease already holds this unit as
    'accepted'. Belt-and-braces alongside the DB-level partial unique
    index (app/db/models.py:Lease.__table_args__) - that index is what
    actually prevents two accepted leases on one unit even under a race,
    but it would only surface here as an opaque IntegrityError on
    commit; this check exists so the common (non-racing) case gets a
    clear, specific 409 instead."""
    if not lease.unit_id:
        return
    existing = (
        db.query(Lease)
        .filter(
            Lease.unit_id == lease.unit_id,
            Lease.status == LeaseStatus.ACCEPTED,
            Lease.id != lease.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            409,
            f"Unit '{lease.unit_id}' already has an accepted lease (lease {existing.id}). "
            "Reject or otherwise resolve the existing lease before accepting this one.",
        )


def _require_reviewer(review: LeaseReviewRequest) -> str:
    if not review.reviewed_by or not review.reviewed_by.strip():
        raise HTTPException(
            400,
            "'reviewed_by' is required to accept or reject a lease - who made this "
            "decision must be recorded.",
        )
    return review.reviewed_by


# A rule at this severity failing is the ruleset saying "do not accept
# this lease". The value is the one used in data/owner_ruleset.json.
BLOCKING_SEVERITY = "high"


def _high_severity_failures(rule_results: list[dict]) -> list[dict]:
    return [
        r for r in rule_results
        if r["severity"] == BLOCKING_SEVERITY and r["result"] == RuleResult.FAIL
    ]


def _apply_high_severity_gate(lease: Lease, rule_results: list[dict], review: LeaseReviewRequest) -> None:
    """Refuses to accept a lease that a high-severity rule is FAILing,
    unless the reviewer explicitly overrides with a stated reason.

    Before this existed, `severity` was loaded from the ruleset, stored
    on every RuleCheck row and rendered in the UI - and read by no
    decision anywhere, so a lease whose deposit was a hundredth of its
    rent (R1, severity high, FAIL) could be finalized to "accepted" with
    a plain 200. The ruleset was decorative. This is the one place that
    makes it binding.

    An override is allowed rather than a hard block, but it is never
    silent: the reason and the exact rule ids overridden are written to
    the lease, so "accepted despite R1" stays visible in the record
    instead of being indistinguishable from "accepted cleanly".
    """
    failures = _high_severity_failures(rule_results)
    if not failures:
        return

    summary = "; ".join(f"{f['rule_id']}: {f['reason']}" for f in failures)
    reason = (review.override_reason or "").strip()
    if not reason:
        raise HTTPException(
            409,
            f"Cannot accept: {len(failures)} high-severity rule check(s) are failing "
            f"[{summary}]. Fix the underlying fields, reject the lease, or supply "
            "'override_reason' to accept it anyway - the reason is recorded on the lease.",
        )
    if len(reason) < 10:
        raise HTTPException(
            400,
            "'override_reason' must actually explain the override "
            "(at least 10 characters).",
        )
    lease.high_severity_override_reason = reason
    lease.high_severity_overridden_rules = [f["rule_id"] for f in failures]


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

    # A human can reject the whole lease outright, independent of the
    # per-field flow below - same shape as how a work order is accepted
    # or rejected as a whole rather than field by field.
    if review.action == "reject":
        lease.status = LeaseStatus.REJECTED
        lease.reviewed_by = _require_reviewer(review)
        lease.decision_at = _utcnow()
        db.commit()
        db.refresh(lease)
        return lease
    if review.action is not None:
        raise HTTPException(400, "action must be 'reject' or omitted.")

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
            # action.new_value has already been type/shape-validated and
            # coerced (e.g. an ISO date string -> a real date) by
            # FieldReviewAction's model_validator - nothing left to do
            # here but assign it.
            review_status[action.field_name] = ReviewState.EDITED.value
            setattr(lease, action.field_name, action.new_value)

    lease.review_status = review_status

    # Re-run the rule engine against whatever the lease's fields hold
    # right now. If a field was just edited, the persisted RuleCheck
    # rows must reflect that value, not the original AI extraction -
    # otherwise a displayed PASS/FAIL can describe data a human already
    # overwrote, which defeats the point of a checkable record. Always
    # refreshed, not just when an edit happened, so this stays correct
    # without tracking "did anything actually change" separately.
    rule_results = lease_extraction.refresh_rule_checks(db, lease)

    if review.finalize:
        if ReviewState.REJECTED.value in review_status.values():
            lease.status = LeaseStatus.REJECTED
            lease.reviewed_by = _require_reviewer(review)
            lease.decision_at = _utcnow()
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
            _reject_if_unit_already_leased(db, lease)
            # Ordered deliberately: the unit conflict and the failing-rule
            # gate both reject the acceptance, and both must run before
            # anything is mutated toward "accepted".
            _apply_high_severity_gate(lease, rule_results, review)
            lease.status = LeaseStatus.ACCEPTED
            lease.reviewed_by = _require_reviewer(review)
            lease.decision_at = _utcnow()
            if lease.unit_id:
                unit = db.get(Unit, lease.unit_id)
                if unit:
                    unit.status = UnitStatus.OCCUPIED

    try:
        db.commit()
    except IntegrityError:
        # Defense in depth against the _reject_if_unit_already_leased
        # check above racing with a concurrent accept on the same unit -
        # the partial unique index on Lease (app/db/models.py) is the
        # actual guarantee; this just turns a raw IntegrityError into a
        # clear 409 instead of a 500.
        db.rollback()
        raise HTTPException(
            409,
            f"Unit '{lease.unit_id}' already has an accepted lease. "
            "Reject or otherwise resolve the existing lease before accepting this one.",
        )
    db.refresh(lease)
    return lease
