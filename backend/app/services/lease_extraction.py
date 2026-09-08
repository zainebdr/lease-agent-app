"""
Orchestrates Part A end to end:
  extract -> match unit -> persist draft -> run rule checks

This function is the one piece you'd move into a background job (Celery/
RQ) if real LLM calls made it too slow for a synchronous request — it
doesn't know or care whether it's called from a route handler directly
or from a task queue.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.factory import get_lease_extractor
from app.db.enums import LeaseStatus, ReviewState
from app.db.models import Lease, RuleCheck, Unit
from app.services import rule_engine, unit_matching


def _coerce_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return value


def process_lease_upload(db: Session, document_text: str, source_document_name: str) -> Lease:
    extractor = get_lease_extractor()
    extracted = extractor.extract(document_text)

    def val(name):
        field = extracted.get(name)
        return field.value if field else None

    unit_reference_text = val("unit_reference_text")
    matched_unit = unit_matching.match_unit(db, unit_reference_text)

    lease = Lease(
        source_document_name=source_document_name,
        unit_id=matched_unit.unit_id if matched_unit else None,
        landlord_name=val("landlord_name"),
        tenant_name=val("tenant_name"),
        landlord_signed=val("landlord_signed"),
        tenant_signed=val("tenant_signed"),
        commencement_date=_coerce_date(val("commencement_date")),
        expiry_date=_coerce_date(val("expiry_date")),
        term_months=val("term_months"),
        monthly_rent=val("monthly_rent"),
        annual_rent=val("annual_rent"),
        deposit_amount=val("deposit_amount"),
        escalation_clause_text=val("escalation_clause_text"),
        escalation_is_defined=val("escalation_is_defined"),
        renewal_terms_text=val("renewal_terms_text"),
        termination_terms_text=val("termination_terms_text"),
        extracted_fields={k: v.to_dict() for k, v in extracted.items()},
        review_status={k: ReviewState.PENDING.value for k in extracted.keys()},
        status=LeaseStatus.DRAFT,
    )
    db.add(lease)
    db.flush()  # get lease.id before running checks that reference it

    check_results = rule_engine.run_all_checks(lease, matched_unit)
    for result in check_results:
        db.add(rule_engine_row(lease.id, result))

    db.commit()
    db.refresh(lease)
    return lease


def rule_engine_row(lease_id: int, result: dict):
    return RuleCheck(lease_id=lease_id, **result)


def refresh_rule_checks(db: Session, lease: Lease) -> None:
    """Re-run the rule engine against whatever the lease's fields hold
    right now, and record the fresh result alongside - not in place of -
    whatever was there before.

    Called after every review action that can change a lease's field
    values (an "edit"), so a displayed PASS/FAIL always describes the
    lease as it currently stands, not a stale extraction-time snapshot.
    Deliberately unconditional (always refreshes, not just "if
    something changed") - that keeps this correct without the caller
    having to track edit state separately, and re-running 7 in-memory
    checks is cheap enough that it isn't worth the bookkeeping to skip.

    Previously this deleted and recreated every RuleCheck row on each
    call, which destroyed the exact history ("this was FAIL, then a
    human edited the deposit and it went PASS") the traceability pitch
    depends on. Existing current rows are now marked superseded instead
    of deleted; see app/db/models.py:Lease.rule_checks (current only)
    vs. rule_check_history (everything). Does not commit - the caller
    controls the transaction boundary.
    """
    unit = db.get(Unit, lease.unit_id) if lease.unit_id else None
    now = datetime.now(timezone.utc)
    for existing in lease._rule_check_rows:
        if existing.is_current:
            existing.is_current = False
            existing.superseded_at = now
    for result in rule_engine.run_all_checks(lease, unit):
        db.add(rule_engine_row(lease.id, result))
