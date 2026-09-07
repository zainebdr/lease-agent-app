from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.enums import IssueStatus, WorkOrderStatus
from app.db.models import Issue
from app.schemas.issue import IssueOut, WorkOrderReviewRequest
from app.services import issue_reporting

router = APIRouter(prefix="/issues", tags=["issues"])


@router.post("/upload", response_model=IssueOut)
def upload_issue(
    unit_id: str = Form(...),
    reported_by: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "At least one photo is required.")

    photo_files = [(f.filename, f.file.read()) for f in files]

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

    # Edits apply first, so an accept/reject in the same request reflects
    # the edited text rather than the original AI-drafted one.
    if review.title is not None:
        work_order.title = review.title
    if review.description is not None:
        work_order.description = review.description

    if review.action == "accept":
        work_order.status = WorkOrderStatus.ACCEPTED
        # Accepting the draft is what turns a reported issue into
        # something actually being worked - the issue itself moves from
        # "open" (reported, nothing approved yet) to "in_progress".
        # Marking it "resolved" is a separate, later action (the work
        # actually being completed) that this build doesn't have a flow
        # for yet - see README.
        issue.status = IssueStatus.IN_PROGRESS
    elif review.action == "reject":
        work_order.status = WorkOrderStatus.REJECTED
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
