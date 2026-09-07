from datetime import date
from typing import Optional, Any

from pydantic import BaseModel


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


class FieldReviewAction(BaseModel):
    field_name: str
    action: str            # "accept" | "reject" | "edit"
    new_value: Optional[Any] = None  # required if action == "edit"


class LeaseReviewRequest(BaseModel):
    field_actions: list[FieldReviewAction] = []
    finalize: bool = False   # if true and no field is rejected, marks lease accepted
                              # and flips the unit's status to "occupied"
