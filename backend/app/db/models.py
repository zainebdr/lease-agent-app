"""
ORM models.

Design notes:
- `extracted_fields` and `review_status` on Lease are JSON columns that
  carry per-field provenance (where the value came from) and human
  review state (accepted / rejected / edited). This is what makes the
  system "traceable and overridable" rather than a black-box extractor.
- JSON columns use SQLAlchemy's generic JSON type, which maps to a
  native JSON column on Postgres and TEXT-backed JSON on SQLite —
  same model code works unchanged on either engine.
- Status-like columns (Unit.status, Lease.status, RuleCheck.result, ...)
  use sqlalchemy.Enum backed by the str-mixin enums in app/db/enums.py,
  not bare strings — invalid values are rejected at the DB layer, not
  just documented in a comment.
- Every table gets created_at/updated_at via TimestampMixin. This
  product's whole pitch is traceability and review; not being able to
  say when a lease was uploaded or a field last touched undercuts that.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, JSON, Enum
)
from sqlalchemy.orm import relationship, declarative_mixin

from app.db.base import Base
from app.db.enums import UnitStatus, LeaseStatus, RuleResult, IssueStatus, WorkOrderStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@declarative_mixin
class TimestampMixin:
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


# SQLAlchemy's Enum type defaults to storing a Python enum's `.name`.
# Our enum *values* (e.g. "available") are what the rest of the app and
# the seed data (units.json) already use, so every Enum column below
# passes values_callable to store `.value` instead - keeps the DB
# content identical to what plain strings would have held.
def _enum_column(enum_cls, **kw):
    return Column(Enum(enum_cls, values_callable=lambda e: [m.value for m in e]), **kw)


class Unit(Base, TimestampMixin):
    __tablename__ = "units"

    unit_id = Column(String, primary_key=True)          # e.g. "MC-B-1204"
    property_name = Column(String, nullable=False)
    building_name = Column(String, nullable=False)
    label = Column(String, nullable=False)               # "Apartment 1204"
    type = Column(String, nullable=True)                  # "2BR"
    area_sqm = Column(Float, nullable=True)
    parking_bay = Column(String, nullable=True)
    status = _enum_column(UnitStatus, nullable=False, default=UnitStatus.AVAILABLE)

    leases = relationship("Lease", back_populates="unit")
    issues = relationship("Issue", back_populates="unit")


class Lease(Base, TimestampMixin):
    __tablename__ = "leases"

    id = Column(Integer, primary_key=True)
    unit_id = Column(String, ForeignKey("units.unit_id"), nullable=True, index=True)
    source_document_name = Column(String, nullable=False)

    landlord_name = Column(String, nullable=True)
    tenant_name = Column(String, nullable=True)
    landlord_signed = Column(Boolean, nullable=True)
    tenant_signed = Column(Boolean, nullable=True)

    commencement_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    term_months = Column(Integer, nullable=True)

    monthly_rent = Column(Float, nullable=True)
    annual_rent = Column(Float, nullable=True)
    deposit_amount = Column(Float, nullable=True)

    escalation_clause_text = Column(String, nullable=True)
    escalation_is_defined = Column(Boolean, nullable=True)

    # {field_name: {"value": ..., "source_span": "...", "confidence": 0.0-1.0}}
    extracted_fields = Column(JSON, default=dict)
    # {field_name: "pending" | "accepted" | "rejected" | "edited"} — see
    # app/db/enums.py:ReviewState for the allowed values (JSON can't
    # enforce this at the DB layer the way the Enum columns do).
    review_status = Column(JSON, default=dict)

    status = _enum_column(LeaseStatus, nullable=False, default=LeaseStatus.DRAFT, index=True)

    unit = relationship("Unit", back_populates="leases")
    rule_checks = relationship(
        "RuleCheck", back_populates="lease", cascade="all, delete-orphan"
    )


class RuleCheck(Base, TimestampMixin):
    __tablename__ = "rule_checks"

    id = Column(Integer, primary_key=True)
    lease_id = Column(Integer, ForeignKey("leases.id"), nullable=False, index=True)
    rule_id = Column(String, nullable=False)         # "R1".."R7"
    description = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    result = _enum_column(RuleResult, nullable=False, index=True)
    reason = Column(String, nullable=True)
    source_clause = Column(String, nullable=True)

    lease = relationship("Lease", back_populates="rule_checks")


class Issue(Base, TimestampMixin):
    """A reported property issue for a unit, built from one or more
    photos. Aggregates the per-photo assessments in IssuePhoto into a
    single condition/contents picture, and owns the one draft WorkOrder
    generated from it. FK'd to Unit directly (not through Lease) because
    an issue is about the physical unit regardless of which lease, if
    any, currently occupies it."""
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True)
    unit_id = Column(String, ForeignKey("units.unit_id"), nullable=False, index=True)
    reported_by = Column(String, nullable=True)  # free text, e.g. "tenant" | "inspector"

    condition_summary = Column(String, nullable=True)
    contents_summary = Column(JSON, default=list)  # e.g. ["AC unit", "water heater"]
    status = _enum_column(IssueStatus, nullable=False, default=IssueStatus.OPEN, index=True)

    unit = relationship("Unit", back_populates="issues")
    photos = relationship("IssuePhoto", back_populates="issue", cascade="all, delete-orphan")
    work_order = relationship(
        "WorkOrder", back_populates="issue", uselist=False, cascade="all, delete-orphan"
    )


class IssuePhoto(Base, TimestampMixin):
    """One uploaded photo and its own assessment - kept as its own row
    (rather than a list inside Issue) for the same reason RuleCheck is
    its own table: each AI-produced result needs to stay individually
    traceable back to the specific input (and assessor) that produced
    it, not folded into an opaque blob."""
    __tablename__ = "issue_photos"

    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False, index=True)
    file_path = Column(String, nullable=False)

    condition_assessment = Column(String, nullable=True)   # e.g. "worn, visible water damage"
    contents_detected = Column(JSON, default=list)          # ["AC unit", "water heater"]
    damage_notes = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    assessed_by = Column(String, nullable=True)  # "mock" or the real model id used

    issue = relationship("Issue", back_populates="photos")


class WorkOrder(Base, TimestampMixin):
    """The draft work order generated from an Issue. 1:1 with Issue in
    this build (one report -> one draft) - reviewed as a whole (accept /
    reject / edit-then-accept), unlike Lease's per-field review, because
    the brief asks for each *draft work order* to be accepted or
    rejected, not each of its fields individually."""
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False, unique=True, index=True)

    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    status = _enum_column(WorkOrderStatus, nullable=False, default=WorkOrderStatus.DRAFT, index=True)

    issue = relationship("Issue", back_populates="work_order")
