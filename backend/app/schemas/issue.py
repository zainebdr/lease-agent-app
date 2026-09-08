from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class IssuePhotoOut(BaseModel):
    id: int
    file_path: str
    # The uploader's original filename, kept purely for display - see
    # app/db/models.py:IssuePhoto.original_filename. None for a photo
    # uploaded before this column existed.
    original_filename: Optional[str] = None
    # Integrity/dedup metadata for the stored file - see
    # app/db/models.py:IssuePhoto and app/services/issue_reporting.py.
    # None for a photo uploaded before these columns existed.
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    # Set when this photo's content hash matches an earlier upload - the
    # id of that earlier (original) IssuePhoto row. Not a rejection or a
    # skip, just a flag: see app/db/models.py:IssuePhoto.duplicate_of_id.
    duplicate_of_id: Optional[int] = None
    condition_assessment: Optional[str] = None
    contents_detected: list[str] = []
    damage_notes: Optional[str] = None
    confidence: Optional[float] = None
    assessed_by: Optional[str] = None
    # "processed" (a real assessment) or "failed" (this one photo's AI
    # call errored and condition_assessment/etc. above are a placeholder,
    # not a real result) - see app/db/enums.py:PhotoProcessingStatus and
    # app/services/issue_reporting.py:_assess_photo. None for a photo
    # uploaded before this column existed.
    processing_status: Optional[str] = None

    class Config:
        from_attributes = True


class WorkOrderOut(BaseModel):
    id: int
    title: str
    description: str
    status: str
    # Who accepted/rejected this work order and when - see
    # app/db/models.py:WorkOrder.reviewed_by.
    reviewed_by: Optional[str] = None
    decision_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class IssueOut(BaseModel):
    id: int
    unit_id: str
    reported_by: Optional[str] = None
    condition_summary: Optional[str] = None
    contents_summary: list[str] = []
    status: str
    created_at: datetime

    photos: list[IssuePhotoOut] = []
    work_order: Optional[WorkOrderOut] = None

    class Config:
        from_attributes = True


class WorkOrderReviewRequest(BaseModel):
    action: Optional[str] = None       # "accept" | "reject" | None (edit-only)
    title: Optional[str] = None        # optional edit applied before accept/reject
    description: Optional[str] = None  # optional edit applied before accept/reject
    # Who is making this call - see app/schemas/lease.py:LeaseReviewRequest.
    # reviewed_by for the same reasoning. Required by app/api/issues.py
    # whenever action is "accept" or "reject" (an edit-only call doesn't
    # decide anything yet, so it's optional here).
    reviewed_by: Optional[str] = None
