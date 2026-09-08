import io
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import MAX_PHOTO_SIZE_BYTES
from app.db.base import get_db
from app.db.enums import IssueStatus, WorkOrderStatus
from app.db.models import Issue
from app.schemas.issue import IssueOut, WorkOrderReviewRequest
from app.services import issue_reporting

router = APIRouter(prefix="/issues", tags=["issues"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_reviewer(review: WorkOrderReviewRequest) -> str:
    if not review.reviewed_by or not review.reviewed_by.strip():
        raise HTTPException(
            400,
            "'reviewed_by' is required to accept or reject a work order - who made "
            "this decision must be recorded.",
        )
    return review.reviewed_by


def _validate_photo_upload(filename: str, content: bytes) -> None:
    """Raises 400 unless `content` is a genuinely decodable image within
    the size limit.

    A client-supplied Content-Type header is never trusted for this - a
    renamed .txt file can claim to be "image/jpeg" just as easily as a
    real photo can - so this actually opens the bytes with Pillow and
    lets it verify them, the same way any real image consumer would.
    Deliberately runs before anything else in the pipeline (hashing,
    storage, the AI call): there's no reason to spend an AI call, disk
    space, or a stored sha256 on bytes that were never a photo to begin
    with.

    Image.verify() is a structural check (catches truncated/corrupt
    files and non-image bytes) rather than a full pixel decode - fine
    for this purpose, and cheaper than fully decoding every upload.
    """
    if len(content) > MAX_PHOTO_SIZE_BYTES:
        raise HTTPException(
            400,
            f"'{filename}' is {len(content)} bytes, over the "
            f"{MAX_PHOTO_SIZE_BYTES // (1024 * 1024)}MB limit per photo.",
        )
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, f"'{filename}' is not a valid image file.")


@router.post("/upload", response_model=IssueOut)
def upload_issue(
    unit_id: str = Form(...),
    reported_by: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "At least one photo is required.")

    photo_files = []
    for f in files:
        content = f.file.read()
        _validate_photo_upload(f.filename, content)
        photo_files.append((f.filename, content, f.content_type))

    try:
        issue = issue_reporting.process_issue_report(db, unit_id, photo_files, reported_by)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return issue


@router.get("/{issue_id}", response_model=IssueOut)
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "Issue not found.")
    return issue


@router.post("/{issue_id}/review", response_model=IssueOut)
def review_work_order(issue_id: int, review: WorkOrderReviewRequest, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(404, "Issue not found.")
    if not issue.work_order:
        raise HTTPException(404, "No draft work order on this issue.")

    work_order = issue.work_order

    if work_order.status != WorkOrderStatus.DRAFT:
        # Same reasoning as the lease finalize-lock: a work order that's
        # already been accepted or rejected (and may already have moved
        # the issue to "in_progress") must not be silently re-decidable -
        # that could flip Issue.status back and forth out of step with
        # what actually happened. Reopening a decided work order isn't
        # supported yet; that would be its own explicit action.
        raise HTTPException(
            409,
            f"This work order is already '{work_order.status.value}' and can no longer be reviewed.",
        )

    # Edits apply first, so an accept/reject in the same request reflects
    # the edited text rather than the original AI-drafted one.
    if review.title is not None:
        work_order.title = review.title
    if review.description is not None:
        work_order.description = review.description

    if review.action == "accept":
        work_order.status = WorkOrderStatus.ACCEPTED
        work_order.reviewed_by = _require_reviewer(review)
        work_order.decision_at = _utcnow()
        # Accepting the draft is what turns a reported issue into
        # something actually being worked - the issue itself moves from
        # "open" (reported, nothing approved yet) to "in_progress".
        # Marking it "resolved" is a separate, later action (the work
        # actually being completed) that this build doesn't have a flow
        # for yet - see README.
        issue.status = IssueStatus.IN_PROGRESS
    elif review.action == "reject":
        work_order.status = WorkOrderStatus.REJECTED
        work_order.reviewed_by = _require_reviewer(review)
        work_order.decision_at = _utcnow()
        # Rejecting the draft doesn't resolve or dismiss the underlying
        # issue - the property problem the photos showed is still there,
        # it just means this drafted work order wasn't right. The issue
        # stays "open" so it keeps showing up as needing attention (e.g.
        # a corrected work order, or a human writing one from scratch).
    elif review.action is not None:
        raise HTTPException(400, "action must be 'accept' or 'reject' (or omitted to just edit).")

    db.commit()
    db.refresh(issue)
    return issue
