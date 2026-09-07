from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class IssuePhotoOut(BaseModel):
    id: int
    file_path: str
    condition_assessment: Optional[str] = None
    contents_detected: list[str] = []
    damage_notes: Optional[str] = None
    confidence: Optional[float] = None
    assessed_by: Optional[str] = None

    class Config:
        from_attributes = True


class WorkOrderOut(BaseModel):
    id: int
    title: str
    description: str
    status: str
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
