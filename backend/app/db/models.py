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
"""
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, ForeignKey, JSON
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class Unit(Base):
    __tablename__ = "units"

    unit_id = Column(String, primary_key=True)          # e.g. "MC-B-1204"
    property_name = Column(String, nullable=False)
    building_name = Column(String, nullable=False)
    label = Column(String, nullable=False)               # "Apartment 1204"
    type = Column(String, nullable=True)                  # "2BR"
    area_sqm = Column(Float, nullable=True)
    parking_bay = Column(String, nullable=True)
    status = Column(String, nullable=False, default="available")  # available|occupied

    leases = relationship("Lease", back_populates="unit")


class Lease(Base):
    __tablename__ = "leases"

    id = Column(Integer, primary_key=True)
    unit_id = Column(String, ForeignKey("units.unit_id"), nullable=True)
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
    # {field_name: "pending" | "accepted" | "rejected" | "edited"}
    review_status = Column(JSON, default=dict)

    status = Column(String, default="draft")  # draft | accepted | rejected

    unit = relationship("Unit", back_populates="leases")
    rule_checks = relationship(
        "RuleCheck", back_populates="lease", cascade="all, delete-orphan"
    )


class RuleCheck(Base):
    __tablename__ = "rule_checks"

    id = Column(Integer, primary_key=True)
    lease_id = Column(Integer, ForeignKey("leases.id"), nullable=False)
    rule_id = Column(String, nullable=False)         # "R1".."R7"
    description = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    result = Column(String, nullable=False)          # PASS | FAIL | NOT_DETERMINABLE
    reason = Column(String, nullable=True)
    source_clause = Column(String, nullable=True)

    lease = relationship("Lease", back_populates="rule_checks")
