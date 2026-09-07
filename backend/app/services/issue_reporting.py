"""
Orchestrates Part B end to end:
  for each uploaded photo -> assess -> save file -> persist IssuePhoto
  -> aggregate into Issue -> draft a WorkOrder

Same shape as Part A's lease_extraction.process_lease_upload(): one
function a route handler calls, unaware of whether the assessor behind
it is the mock or a real vision model - and the natural unit to move
behind a queue if real per-photo vision calls made several-files-per-
request latency add up.
"""
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai.base import PhotoAssessment
from app.ai.factory import get_image_assessor
from app.config import UPLOAD_DIR
from app.db.enums import IssueStatus, WorkOrderStatus
from app.db.models import Issue, IssuePhoto, Unit, WorkOrder


def _save_photo(issue_id: int, index: int, filename: str, content: bytes) -> str:
    """Saves the raw upload under UPLOAD_DIR and returns the stored file
    name (servable at /uploads/<name>, see app/main.py's static mount).
    An owner reviewing a draft work order needs to see the actual photo
    next to the AI's assessment, not just the assessment text."""
    safe_name = Path(filename or f"photo_{index}").name
    stored_name = f"issue_{issue_id}_{index}_{safe_name}"
    (UPLOAD_DIR / stored_name).write_bytes(content)
    return stored_name


def _aggregate(photos: list[IssuePhoto]) -> tuple[str, list[str]]:
    """Combines the per-photo assessments into one issue-level picture.
    Order-preserving de-dup (not a set) so the summary reads naturally
    and stays deterministic across runs."""
    conditions = list(dict.fromkeys(
        p.condition_assessment for p in photos if p.condition_assessment
    ))
    contents = list(dict.fromkeys(
        item for p in photos for item in (p.contents_detected or [])
    ))
    return "; ".join(conditions), contents


def _draft_work_order(
    unit: Unit, condition_summary: str, contents_summary: list[str], photos: list[IssuePhoto]
) -> tuple[str, str]:
    """Turns the aggregated issue into a short title + description, per
    the brief: 'a short title, what's wrong, and the affected unit'.
    Deliberately plain rule-based composition over the AI-produced
    per-photo assessments - the assessment step is the AI's job; turning
    an already-structured assessment into a short human-readable draft
    isn't a place that benefits from a second model call."""
    primary_content = contents_summary[0] if contents_summary else "unit"
    title = f"{primary_content.title()} issue - {unit.label}"[:120]

    lines = [f"Condition: {condition_summary or 'not assessed'}."]
    if contents_summary:
        lines.append(f"Affected/visible items: {', '.join(contents_summary)}.")
    damage_notes = [p.damage_notes for p in photos if p.damage_notes]
    if damage_notes:
        lines.append("Notes: " + " ".join(damage_notes))

    return title, " ".join(lines)


def process_issue_report(
    db: Session,
    unit_id: str,
    photo_files: list[tuple[str, bytes]],
    reported_by: str | None = None,
) -> Issue:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise ValueError(f"Unit '{unit_id}' not found.")

    assessor = get_image_assessor()

    issue = Issue(unit_id=unit_id, reported_by=reported_by, status=IssueStatus.OPEN)
    db.add(issue)
    db.flush()  # get issue.id for the photo file names / FKs

    photos: list[IssuePhoto] = []
    for index, (filename, content) in enumerate(photo_files):
        stored_name = _save_photo(issue.id, index, filename, content)
        assessment: PhotoAssessment = assessor.assess(content, filename)
        photo = IssuePhoto(
            issue_id=issue.id,
            file_path=stored_name,
            condition_assessment=assessment.condition,
            contents_detected=assessment.contents,
            damage_notes=assessment.damage_notes,
            confidence=assessment.confidence,
            assessed_by=assessment.assessed_by,
        )
        db.add(photo)
        photos.append(photo)

    condition_summary, contents_summary = _aggregate(photos)
    issue.condition_summary = condition_summary
    issue.contents_summary = contents_summary

    title, description = _draft_work_order(unit, condition_summary, contents_summary, photos)
    db.add(WorkOrder(
        issue_id=issue.id,
        title=title,
        description=description,
        status=WorkOrderStatus.DRAFT,
    ))

    db.commit()
    db.refresh(issue)
    return issue
