"""
Every status-like value in the schema is defined here exactly once, as a
str-mixin Enum, and reused by both the ORM models (as the column type)
and the Pydantic schemas (as the field type). Before this, each of these
was a bare `String` column with the allowed values living only in a
comment - nothing stopped a typo or inconsistent casing from being
written. A str-mixin (`class X(str, enum.Enum)`) is used deliberately so
existing comparisons like `unit.status == "available"` and JSON
serialization keep working unchanged - these behave like strings
everywhere except they're now a closed, checkable set.
"""
import enum


class UnitStatus(str, enum.Enum):
    AVAILABLE = "available"
    OCCUPIED = "occupied"


class LeaseStatus(str, enum.Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RuleResult(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_DETERMINABLE = "NOT_DETERMINABLE"


class ReviewState(str, enum.Enum):
    """Values live inside Lease.review_status / WorkOrder.review_status,
    which are JSON columns (per-field granularity), not typed DB columns -
    this enum keeps the four allowed values in one place for the Python
    side even though the column itself can't enforce it the way
    sqlalchemy.Enum does for the single-value status columns above."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"


class IssueStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class WorkOrderStatus(str, enum.Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
