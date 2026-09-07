from datetime import date
from typing import Optional, Any

from pydantic import BaseModel, model_validator


class RuleCheckOut(BaseModel):
    rule_id: str
    description: Optional[str] = None
    severity: Optional[str] = None
    result: str
    reason: Optional[str] = None
    source_clause: Optional[str] = None

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

    extracted_fields: dict[str, Any] = {}
    review_status: dict[str, str] = {}
    status: str

    rule_checks: list[RuleCheckOut] = []

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
