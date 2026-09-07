"""
Orchestrates Part A end to end:
  extract -> match unit -> persist draft -> run rule checks

This function is the one piece you'd move into a background job (Celery/
RQ) if real LLM calls made it too slow for a synchronous request — it
doesn't know or care whether it's called from a route handler directly
or from a task queue.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.factory import get_lease_extractor
from app.db.models import Lease
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
        extracted_fields={k: v.to_dict() for k, v in extracted.items()},
        review_status={k: "pending" for k in extracted.keys()},
        status="draft",
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
    from app.db.models import RuleCheck
    return RuleCheck(lease_id=lease_id, **result)
