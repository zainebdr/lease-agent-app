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
import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai.base import PhotoAssessment
from app.ai.factory import get_image_assessor
from app.config import UPLOAD_DIR
from app.db.enums import IssueStatus, PhotoProcessingStatus, WorkOrderStatus
from app.db.models import Issue, IssuePhoto, Unit, WorkOrder

logger = logging.getLogger(__name__)


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _save_photo(sha256: str, filename: str, content: bytes) -> str:
    """Saves the raw upload under UPLOAD_DIR and returns the stored file
    name (servable at /uploads/<name>, see app/main.py's static mount).

    Content-addressed by the upload's own sha256 rather than by
    issue/index: an identical photo uploaded twice - the same issue, or
    a different one entirely - gets written to disk exactly once, and
    the second IssuePhoto row just points at the file that's already
    there. This is the actual dedup (no duplicate bytes on disk); the
    `sha256` column stored on IssuePhoto (app/db/models.py) is what lets
    a caller notice the re-upload happened at all, and lets a stored
    file's integrity be re-checked later by re-hashing it."""
    suffix = Path(filename or "").suffix
    stored_name = f"{sha256}{suffix}"
    target = UPLOAD_DIR / stored_name
    if not target.exists():
        target.write_bytes(content)
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


def _assess_photo(assessor, content: bytes, filename: str) -> tuple[PhotoAssessment, PhotoProcessingStatus]:
    """Runs the assessor for one photo, isolating a failure to just this
    photo instead of letting it sink the whole upload. A real vision
    call can fail in ways a mock never does - a provider timeout/5xx, a
    response PhotoAssessmentPayload.model_validate rejects - and none of
    that is the uploader's fault or something worth losing every *other*
    photo's real assessment (and the whole issue/work order) over. The
    actual exception is logged server-side rather than surfaced in the
    response - callers see a plainly-labelled placeholder
    (processing_status="failed") and know to look at the photo
    themselves, not a raw internal error message."""
    try:
        return assessor.assess(content, filename), PhotoProcessingStatus.PROCESSED
    except Exception:
        logger.exception("Photo assessment failed for %r; recording a placeholder result.", filename)
        return (
            PhotoAssessment(
                condition="Assessment failed - needs manual review.",
                contents=[],
                damage_notes=None,
                confidence=0.0,
                assessed_by="failed",
            ),
            PhotoProcessingStatus.FAILED,
        )


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
    photo_files: list[tuple[str, bytes, str | None]],
    reported_by: str | None = None,
) -> Issue:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise ValueError(f"Unit '{unit_id}' not found.")

    assessor = get_image_assessor()

    issue = Issue(unit_id=unit_id, reported_by=reported_by, status=IssueStatus.OPEN)
    db.add(issue)
    db.flush()  # get issue.id for the IssuePhoto/WorkOrder FKs below

    photos: list[IssuePhoto] = []
    for filename, content, content_type in photo_files:
        sha256 = _content_hash(content)
        stored_name = _save_photo(sha256, filename, content)
        assessment, processing_status = _assess_photo(assessor, content, filename)

        # The earliest existing row with this same hash, if any - not
        # rejected or skipped, just flagged (see IssuePhoto.duplicate_of_id
        # in app/db/models.py for why: the same real photo can honestly
        # apply to more than one issue). Ordered by id so a duplicate
        # always resolves back to one original, never to another
        # duplicate. This also catches two identical photos uploaded
        # together in the *same* request - the db.flush() below is what
        # makes an earlier photo in this same loop visible to this query
        # before the whole request commits.
        duplicate_of = (
            db.query(IssuePhoto)
            .filter(IssuePhoto.sha256 == sha256)
            .order_by(IssuePhoto.id.asc())
            .first()
        )

        photo = IssuePhoto(
            issue_id=issue.id,
            file_path=stored_name,
            original_filename=filename,
            sha256=sha256,
            size_bytes=len(content),
            content_type=content_type,
            duplicate_of_id=duplicate_of.id if duplicate_of else None,
            condition_assessment=assessment.condition,
            contents_detected=assessment.contents,
            damage_notes=assessment.damage_notes,
            confidence=assessment.confidence,
            assessed_by=assessment.assessed_by,
            processing_status=processing_status,
        )
        db.add(photo)
        db.flush()  # assign photo.id now, so a later duplicate in this
                    # same batch can be detected too (see query above)
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
