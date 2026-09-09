from datetime import date, datetime
from typing import Optional, Any

from pydantic import BaseModel, model_validator


class RuleCheckOut(BaseModel):
    rule_id: str
    description: Optional[str] = None
    severity: Optional[str] = None
    result: str
    reason: Optional[str] = None
    source_clause: Optional[str] = None
    # Included so a client can tell current results (is_current=True,
    # superseded_at=None) apart from entries returned in
    # LeaseOut.rule_check_history that a later review call superseded.
    is_current: bool = True
    created_at: Optional[datetime] = None
    superseded_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeaseOut(BaseModel):
    id: int
    unit_id: Optional[str] = None
    source_document_name: str

    landlord_name: Optional[str] = None
    tenant_name: Optional[str] = None
    landlord_signed: Optional[bool] = None
    tenant_signed: Optional[bool] = None

    commencement_date: Optional[date] = None
    expiry_date: Optional[date] = None
    term_months: Optional[int] = None

    monthly_rent: Optional[float] = None
    annual_rent: Optional[float] = None
    deposit_amount: Optional[float] = None

    escalation_clause_text: Optional[str] = None
    escalation_is_defined: Optional[bool] = None

    renewal_terms_text: Optional[str] = None
    termination_terms_text: Optional[str] = None

    extracted_fields: dict[str, Any] = {}
    review_status: dict[str, str] = {}
    status: str

    # Who accepted/rejected this lease and when - see
    # app/db/models.py:Lease.reviewed_by for why these are nullable.
    reviewed_by: Optional[str] = None
    decision_at: Optional[datetime] = None

    # Set only when this lease was accepted over a FAILing high-severity
    # rule - see app/db/models.py:Lease.high_severity_override_reason.
    high_severity_override_reason: Optional[str] = None
    high_severity_overridden_rules: Optional[list[str]] = None

    rule_checks: list[RuleCheckOut] = []
    # Every rule-check row this lease has ever had, including ones a
    # later edit superseded - `rule_checks` above only ever shows the
    # current one per rule. Empty unless the lease has been through at
    # least one review-triggered refresh past its initial upload.
    rule_check_history: list[RuleCheckOut] = []

    class Config:
        from_attributes = True


def _validate_string(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value


def _validate_bool(value: Any) -> Any:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _validate_positive_number(value: Any) -> Any:
    # bool is a subclass of int in Python, so isinstance(True, int) is True -
    # without the explicit bool check here, editing monthly_rent to `true`
    # would silently pass this as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    if value <= 0:
        raise ValueError("must be greater than 0")
    return value


def _validate_positive_int(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a whole number")
    if value <= 0:
        raise ValueError("must be greater than 0")
    return value


def _validate_date(value: Any) -> Any:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("must be an ISO date (YYYY-MM-DD)")
    raise ValueError("must be an ISO date (YYYY-MM-DD)")


# One validator per editable field - the same whitelist api/leases.py
# enforces via EDITABLE_LEASE_FIELDS. A field with no entry here still
# can't be edited at all, since the route checks that whitelist first;
# this map is what stops a *value* of the wrong shape (a string where a
# number belongs, a negative deposit, an unparsable date) from ever
# reaching the database, instead of surfacing as a confusing 500 or a
# silently-wrong row.
FIELD_VALIDATORS = {
    "landlord_name": _validate_string,
    "tenant_name": _validate_string,
    "landlord_signed": _validate_bool,
    "tenant_signed": _validate_bool,
    "commencement_date": _validate_date,
    "expiry_date": _validate_date,
    "term_months": _validate_positive_int,
    "monthly_rent": _validate_positive_number,
    "annual_rent": _validate_positive_number,
    "deposit_amount": _validate_positive_number,
    "escalation_clause_text": _validate_string,
    "escalation_is_defined": _validate_bool,
    "renewal_terms_text": _validate_string,
    "termination_terms_text": _validate_string,
}


class FieldReviewAction(BaseModel):
    field_name: str
    action: str            # "accept" | "reject" | "edit"
    new_value: Optional[Any] = None  # required if action == "edit"

    @model_validator(mode="after")
    def _validate_edit_value(self) -> "FieldReviewAction":
        if self.action != "edit":
            return self
        validator = FIELD_VALIDATORS.get(self.field_name)
        if validator is None:
            # Not an editable field at all - the route's whitelist check
            # rejects this too, with a clearer message naming the field;
            # nothing to validate here.
            return self
        try:
            self.new_value = validator(self.new_value)
        except ValueError as exc:
            raise ValueError(f"'{self.field_name}': {exc}") from exc
        return self


class LeaseReviewRequest(BaseModel):
    action: Optional[str] = None  # "reject" to reject the whole lease outright,
                                   # independent of the per-field flow below
    field_actions: list[FieldReviewAction] = []
    finalize: bool = False   # if true and no field is rejected, marks lease accepted
                              # and flips the unit's status to "occupied"
    # Who is making this call. This build has no auth system to pull an
    # identity from, so the caller states it; app/api/leases.py requires
    # it on any action that actually decides the lease (a whole-lease
    # "reject", or a "finalize" that accepts/rejects it) so a decision
    # is never recorded with no one attached to it. Not required for a
    # plain per-field accept/reject/edit that doesn't finalize, since
    # nothing has been decided yet at that point.
    reviewed_by: Optional[str] = None
    # Required only to finalize an *acceptance* over a FAILing
    # high-severity rule. Without it that finalize is refused with a 409
    # naming the failing rules; with it, the lease is accepted and both
    # the reason and the overridden rule ids are recorded on the lease
    # (app/db/models.py:Lease.high_severity_override_reason).
    # Deliberately an explicit, recorded override rather than a hard
    # block: an owner accepting a lease whose deposit is short by prior
    # agreement is a real business case, and a system that simply
    # refuses gets worked around outside the system, where nothing is
    # audited at all.
    override_reason: Optional[str] = None
