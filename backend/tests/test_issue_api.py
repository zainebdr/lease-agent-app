"""
Integration tests for /issues/* (app/api/issues.py, app/services/
issue_reporting.py), against the mock image assessor (no API key needed
- see conftest.py). Covers photo upload -> draft work order -> human
review, the Issue.status lifecycle wiring (accept -> in_progress, reject
-> stays open, and the review lock once decided), the required
reviewed_by/decision_at on any decision, the stored-photo integrity/
dedup metadata (sha256, size_bytes, content_type, duplicate_of_id,
original_filename, processing_status), the image-validation gate on
upload (app/api/issues.py:_validate_photo_upload) that rejects anything
that isn't a real, decodable image before it's hashed, stored, or sent
to the AI, and per-photo AI-assessment failure isolation
(app/services/issue_reporting.py:_assess_photo) so one photo's assessor
call failing doesn't sink the rest of the upload.
"""

import hashlib
import io

from PIL import Image

from app.ai.base import PhotoAssessment

AVAILABLE_UNIT = "MC-B-1204"  # seeded as "available" in data/units.json


def _fake_photo_bytes(seed: int = 0) -> bytes:
    """A tiny (2x2), otherwise-meaningless but genuinely valid PNG -
    real enough to pass _validate_photo_upload's image check, varied by
    `seed` so different calls hash differently (MockImageAssessor's
    scenario picker is hash-based - see its own module docstring)."""
    color = (seed % 256, (seed * 7) % 256, (seed * 13) % 256)
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _upload_issue(client, unit_id=AVAILABLE_UNIT, n_photos=2, reported_by="tenant@example.com"):
    files = [
        ("files", (f"photo{i}.png", _fake_photo_bytes(i), "image/png"))
        for i in range(n_photos)
    ]
    data = {"unit_id": unit_id}
    if reported_by:
        data["reported_by"] = reported_by
    resp = client.post("/issues/upload", data=data, files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_issue_assesses_every_photo_and_drafts_a_work_order(client):
    issue = _upload_issue(client, n_photos=2)

    assert issue["status"] == "open"
    assert issue["reported_by"] == "tenant@example.com"
    assert len(issue["photos"]) == 2
    for photo in issue["photos"]:
        assert photo["assessed_by"] == "mock"
        assert photo["condition_assessment"]
        assert 0.0 <= photo["confidence"] <= 1.0

    assert issue["condition_summary"]  # aggregated from the per-photo assessments
    assert issue["work_order"] is not None
    assert issue["work_order"]["status"] == "draft"
    assert issue["work_order"]["title"]
    assert issue["work_order"]["description"]


def test_upload_issue_for_unknown_unit_is_rejected(client):
    # A valid photo (image validation runs before the unit lookup) but a
    # unit_id that doesn't exist - the unit check further downstream is
    # what should reject this, not the image validation.
    resp = client.post(
        "/issues/upload",
        data={"unit_id": "NO-SUCH-UNIT"},
        files=[("files", ("photo.png", _fake_photo_bytes(), "image/png"))],
    )
    assert resp.status_code == 404


def test_uploading_a_non_image_file_is_rejected(client):
    resp = client.post(
        "/issues/upload",
        data={"unit_id": AVAILABLE_UNIT},
        files=[("files", ("not-a-photo.jpg", b"this is plain text, not an image", "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.json()["detail"]


def test_uploading_an_oversized_photo_is_rejected(client):
    from app.config import MAX_PHOTO_SIZE_BYTES

    oversized = b"x" * (MAX_PHOTO_SIZE_BYTES + 1)
    resp = client.post(
        "/issues/upload",
        data={"unit_id": AVAILABLE_UNIT},
        files=[("files", ("huge.jpg", oversized, "image/jpeg"))],
    )
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]


def test_accepting_work_order_moves_issue_to_in_progress(client):
    issue = _upload_issue(client)
    resp = client.post(
        f"/issues/{issue['id']}/review",
        json={"action": "accept", "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["status"] == "accepted"
    assert updated["status"] == "in_progress"
    # Who decided this, and when, must be recorded.
    assert updated["work_order"]["reviewed_by"] == "owner@example.com"
    assert updated["work_order"]["decision_at"] is not None


def test_accepting_work_order_without_reviewed_by_is_rejected(client):
    issue = _upload_issue(client)
    resp = client.post(f"/issues/{issue['id']}/review", json={"action": "accept"})
    assert resp.status_code == 400
    assert "reviewed_by" in resp.json()["detail"]


def test_rejecting_work_order_leaves_issue_open(client):
    issue = _upload_issue(client)
    resp = client.post(
        f"/issues/{issue['id']}/review",
        json={"action": "reject", "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["status"] == "rejected"
    # The underlying property problem is still there - rejecting the
    # draft doesn't resolve or dismiss the issue itself.
    assert updated["status"] == "open"
    assert updated["work_order"]["reviewed_by"] == "owner@example.com"


def test_review_is_locked_once_decided(client):
    issue = _upload_issue(client)
    client.post(f"/issues/{issue['id']}/review", json={"action": "accept", "reviewed_by": "owner@example.com"})

    resp = client.post(f"/issues/{issue['id']}/review", json={"action": "reject", "reviewed_by": "owner@example.com"})
    assert resp.status_code == 409


def test_editing_title_and_description_applies_before_accept(client):
    issue = _upload_issue(client)
    resp = client.post(
        f"/issues/{issue['id']}/review",
        json={
            "title": "Leaking AC unit",
            "description": "Water staining near ceiling AC vent.",
            "action": "accept",
            "reviewed_by": "owner@example.com",
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["title"] == "Leaking AC unit"
    assert updated["work_order"]["description"] == "Water staining near ceiling AC vent."
    assert updated["work_order"]["status"] == "accepted"


def test_edit_only_review_leaves_work_order_in_draft(client):
    issue = _upload_issue(client)
    resp = client.post(f"/issues/{issue['id']}/review", json={"title": "Renamed title"})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["title"] == "Renamed title"
    assert updated["work_order"]["status"] == "draft"


def test_uploaded_photo_records_integrity_metadata(client):
    content = _fake_photo_bytes(0)
    issue = client.post(
        "/issues/upload",
        data={"unit_id": AVAILABLE_UNIT},
        files=[("files", ("photo0.png", content, "image/png"))],
    ).json()
    photo = issue["photos"][0]

    assert photo["sha256"] == hashlib.sha256(content).hexdigest()
    assert photo["size_bytes"] == len(content)
    assert photo["content_type"] == "image/png"
    # This is the first upload of this content - not a duplicate of
    # anything.
    assert photo["duplicate_of_id"] is None


def test_reuploading_identical_photo_bytes_is_flagged_as_a_duplicate_and_deduped_on_disk(client):
    # Same bytes, uploaded twice as separate reports - a re-upload should
    # be detectable (matching sha256, duplicate_of_id set) and not stored
    # as a second copy on disk (same file_path).
    content = _fake_photo_bytes(99)
    files = [("files", ("photo.png", content, "image/png"))]
    first = client.post("/issues/upload", data={"unit_id": AVAILABLE_UNIT}, files=files)
    assert first.status_code == 200, first.text
    second = client.post("/issues/upload", data={"unit_id": AVAILABLE_UNIT}, files=files)
    assert second.status_code == 200, second.text

    first_photo = first.json()["photos"][0]
    second_photo = second.json()["photos"][0]
    assert first_photo["sha256"] == second_photo["sha256"]
    assert first_photo["file_path"] == second_photo["file_path"]
    assert first_photo["duplicate_of_id"] is None
    assert second_photo["duplicate_of_id"] == first_photo["id"]


def test_two_identical_photos_in_the_same_upload_are_also_flagged(client):
    # Both photos in *one* request, not two separate ones - the second
    # must still be detected as a duplicate of the first.
    content = _fake_photo_bytes(7)
    files = [
        ("files", ("a.png", content, "image/png")),
        ("files", ("b.png", content, "image/png")),
    ]
    resp = client.post("/issues/upload", data={"unit_id": AVAILABLE_UNIT}, files=files)
    assert resp.status_code == 200, resp.text
    photos = resp.json()["photos"]
    assert photos[0]["duplicate_of_id"] is None
    assert photos[1]["duplicate_of_id"] == photos[0]["id"]


def test_photo_records_original_filename_separately_from_stored_path(client):
    content = _fake_photo_bytes(42)
    resp = client.post(
        "/issues/upload",
        data={"unit_id": AVAILABLE_UNIT},
        files=[("files", ("front-door-crack.png", content, "image/png"))],
    )
    assert resp.status_code == 200, resp.text
    photo = resp.json()["photos"][0]

    assert photo["original_filename"] == "front-door-crack.png"
    # Storage itself stays content-addressed (the whole point of
    # _save_photo) - the stored path is the sha256, not the uploaded name.
    assert photo["file_path"] != "front-door-crack.png"
    assert photo["file_path"].startswith(photo["sha256"])
    assert photo["processing_status"] == "processed"


class _FlakyAssessor:
    """A stand-in ImageAssessor that fails on the Nth call (1-indexed) and
    succeeds on every other - simulates a real vision provider's call
    failing for exactly one photo in a multi-photo upload, to prove that
    failure is isolated to that one photo (app/services/
    issue_reporting.py:_assess_photo) rather than sinking the whole
    upload/issue/work order."""

    def __init__(self, fail_at: int):
        self._fail_at = fail_at
        self._calls = 0

    def assess(self, image_bytes: bytes, filename: str) -> PhotoAssessment:
        self._calls += 1
        if self._calls == self._fail_at:
            raise RuntimeError("simulated provider failure")
        return PhotoAssessment(
            condition="new, no visible damage",
            contents=["AC unit"],
            damage_notes=None,
            confidence=0.9,
            assessed_by="flaky-test-assessor",
        )


def test_one_photos_assessment_failure_does_not_sink_the_whole_upload(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.issue_reporting.get_image_assessor", lambda: _FlakyAssessor(fail_at=1)
    )

    issue = _upload_issue(client, n_photos=3)

    assert len(issue["photos"]) == 3  # nothing was lost - all 3 rows exist
    assert issue["work_order"] is not None  # the upload as a whole still succeeded

    failed = [p for p in issue["photos"] if p["processing_status"] == "failed"]
    processed = [p for p in issue["photos"] if p["processing_status"] == "processed"]
    assert len(failed) == 1
    assert len(processed) == 2
    # The failed photo gets a clearly-labelled placeholder, not the raw
    # exception text and not a fabricated real-looking assessment.
    assert "manual review" in failed[0]["condition_assessment"].lower()
    assert failed[0]["confidence"] == 0.0
    # The other two photos got the real assessor's actual result.
    for p in processed:
        assert p["assessed_by"] == "flaky-test-assessor"
        assert p["confidence"] == 0.9
