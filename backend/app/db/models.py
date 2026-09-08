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
- `reviewed_by`/`decision_at` on Lease and WorkOrder record *who* made
  the accept/reject decision and *when* - schema changes only, since
  this build has no auth system to source an identity from; the review
  request bodies (app/schemas/lease.py, app/schemas/issue.py) now accept
  a caller-supplied `reviewed_by` string that a real deployment would
  instead populate from an authenticated session.
- RuleCheck rows are never deleted. `refresh_rule_checks` used to wipe
  and recreate them on every review call, destroying the exact history
  ("this was FAIL, then a human edited the deposit and it went PASS")
  the traceability pitch depends on. Old rows are now marked superseded
  (`is_current=False`, `superseded_at` set) instead of deleted; only
  `Lease.rule_checks` (the current set) is exposed by the main API
  response, while `Lease.rule_check_history` returns every row ever
  produced, in order.
- Schema changes here are managed with Alembic (see backend/alembic/) -
  a bare `Base.metadata.create_all()` only creates tables that don't
  exist yet, so it silently does nothing for a column added to a table
  that's already there. Without a migration tool, every column added
  after someone's `app.db` already exists (exactly what's happening in
  this change - `reviewed_by`, `decision_at`, `is_current`,
  `superseded_at`, `sha256`, `size_bytes`, `content_type`) would just be
  missing from their database with no error until first used.
- `IssuePhoto.duplicate_of_id` flags a content-hash match against an
  earlier photo (see app/services/issue_reporting.py) without changing
  upload behavior at all - the duplicate is still stored and assessed
  as normal, this just makes the relationship visible. Deliberately
  automatic/silent rather than asking a human to confirm the reuse at
  upload time (the same photo can legitimately apply to more than one
  issue) - see the README for the "should this ever block or prompt
  instead" open question.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, JSON, Enum,
    Index, text, true,
)
from sqlalchemy.orm import relationship, declarative_mixin

from app.db.base import Base
from app.db.enums import (
    UnitStatus,
    LeaseStatus,
    RuleResult,
    IssueStatus,
    WorkOrderStatus,
    PhotoProcessingStatus,
)


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

    renewal_terms_text = Column(String, nullable=True)
    termination_terms_text = Column(String, nullable=True)

    # {field_name: {"value": ..., "source_span": "...", "confidence": 0.0-1.0}}
    extracted_fields = Column(JSON, default=dict)
    # {field_name: "pending" | "accepted" | "rejected" | "edited"} — see
    # app/db/enums.py:ReviewState for the allowed values (JSON can't
    # enforce this at the DB layer the way the Enum columns do).
    review_status = Column(JSON, default=dict)

    status = _enum_column(LeaseStatus, nullable=False, default=LeaseStatus.DRAFT, index=True)

    # Who decided this lease's fate (accept/reject) and when. No auth
    # system exists in this build to source an identity from, so this is
    # whatever string the review request supplied - see
    # app/schemas/lease.py:LeaseReviewRequest.reviewed_by. Left nullable
    # so pre-existing rows (and a whole-lease action taken before this
    # column existed) don't need a backfill value; app/api/leases.py
    # always sets both together whenever status leaves DRAFT.
    reviewed_by = Column(String, nullable=True)
    decision_at = Column(DateTime, nullable=True)

    unit = relationship("Unit", back_populates="leases")

    # Full history of every RuleCheck row ever produced for this lease,
    # oldest first - never filtered, never deleted. `rule_checks` below
    # (what the API actually serializes) is the "is_current" subset of
    # this same collection.
    _rule_check_rows = relationship(
        "RuleCheck",
        back_populates="lease",
        cascade="all, delete-orphan",
        order_by="RuleCheck.id",
    )

    @property
    def rule_checks(self) -> list["RuleCheck"]:
        """The current rule-check result per rule - what
        app/schemas/lease.py:LeaseOut serializes. Superseded rows are
        deliberately excluded here so a reviewer sees one row per rule,
        not the whole history mixed in; use `rule_check_history` for
        the full audit trail."""
        return [rc for rc in self._rule_check_rows if rc.is_current]

    @property
    def rule_check_history(self) -> list["RuleCheck"]:
        """Every RuleCheck row this lease has ever had, oldest first -
        the record that shows a field being edited flipped a rule from
        FAIL to PASS, which `rule_checks` alone can't show once a
        newer, current row exists for the same rule_id."""
        return list(self._rule_check_rows)

    __table_args__ = (
        # A unit can have at most one *accepted* lease at a time - two
        # accepted leases on the same unit means double-booking it.
        # This is a partial unique index (only rows where status =
        # 'accepted' participate) rather than a plain unique constraint
        # on unit_id, since a unit legitimately accumulates many DRAFT/
        # REJECTED leases over time (renewals, applications that didn't
        # go through, ...) - only "accepted" is exclusive.
        Index(
            "uq_leases_one_accepted_per_unit",
            "unit_id",
            unique=True,
            sqlite_where=text("status = 'accepted'"),
            postgresql_where=text("status = 'accepted'"),
        ),
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

    # False once a later re-run of the rule engine (app/services/
    # lease_extraction.py:refresh_rule_checks) has produced a newer row
    # for the same rule_id on the same lease. The row itself is never
    # deleted or overwritten - this is what keeps the history intact.
    is_current = Column(Boolean, nullable=False, default=True, server_default=true())
    superseded_at = Column(DateTime, nullable=True)

    lease = relationship("Lease", back_populates="_rule_check_rows")

    __table_args__ = (
        Index("ix_rule_checks_lease_id_is_current", "lease_id", "is_current"),
    )


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

    # The name the uploader's browser/OS sent - kept purely for display
    # ("your photo 'IMG_4021.jpg'") and never used to build file_path,
    # which is content-addressed (see _save_photo) precisely so a
    # attacker- or accident-controlled filename never reaches the
    # filesystem. Nullable for the same pre-existing-row reason as the
    # sha256/size_bytes/content_type fields below.
    original_filename = Column(String, nullable=True)

    # Content-addressing/integrity fields for the stored upload. Nullable
    # because a pre-existing row from before this column existed has no
    # way to retroactively compute these without re-reading a file that
    # may itself predate the migration; every row created going forward
    # (app/services/issue_reporting.py) always sets all three.
    sha256 = Column(String(64), nullable=True, index=True)
    size_bytes = Column(Integer, nullable=True)
    content_type = Column(String, nullable=True)

    # Set when this upload's sha256 matches an earlier IssuePhoto row -
    # points at the *earliest* such row (see app/services/
    # issue_reporting.py:process_issue_report), so a photo re-uploaded
    # many times always resolves back to one original, never a chain of
    # duplicates-of-duplicates. This does not change upload behavior at
    # all - a duplicate is still stored (well, its metadata row is;
    # _save_photo's content-addressed storage means the file itself
    # isn't re-written to disk) and assessed exactly like any other
    # photo - it only makes the "this is the same photo as #12" fact
    # visible instead of something you'd have to notice by comparing
    # sha256 values by hand. NULL means "not a known duplicate", not
    # "definitely unique" - it's only ever compared against photos
    # already in this database.
    duplicate_of_id = Column(Integer, ForeignKey("issue_photos.id"), nullable=True, index=True)

    condition_assessment = Column(String, nullable=True)   # e.g. "worn, visible water damage"
    contents_detected = Column(JSON, default=list)          # ["AC unit", "water heater"]
    damage_notes = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    assessed_by = Column(String, nullable=True)  # "mock" or the real model id used

    # Whether the AI assessment above is real or a placeholder. A single
    # photo's assessor call can fail (provider error, or a response that
    # fails PhotoAssessmentPayload validation) without failing the whole
    # upload - see app/services/issue_reporting.py:process_issue_report -
    # so this is what tells a human "the condition_assessment on this one
    # photo is a stand-in, go look at the photo yourself" instead of
    # quietly presenting a placeholder as if it were a real assessment.
    # Nullable for the same pre-existing-row reason as the fields above;
    # every row created going forward always sets it.
    processing_status = _enum_column(PhotoProcessingStatus, nullable=True)

    issue = relationship("Issue", back_populates="photos")
    duplicate_of = relationship("IssuePhoto", remote_side=[id], foreign_keys=[duplicate_of_id])


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

    # Same reasoning as Lease.reviewed_by/decision_at above - who
    # accepted or rejected this work order, and when.
    reviewed_by = Column(String, nullable=True)
    decision_at = Column(DateTime, nullable=True)

    issue = relationship("Issue", back_populates="work_order")
