"""
Integration tests for /issues/* (app/api/issues.py, app/services/
issue_reporting.py), against the mock image assessor (no API key needed
- see conftest.py). Covers photo upload -> draft work order -> human
review, and the Issue.status lifecycle wiring (accept -> in_progress,
reject -> stays open, and the review lock once decided).
"""

AVAILABLE_UNIT = "MC-B-1204"  # seeded as "available" in data/units.json


def _upload_issue(client, unit_id=AVAILABLE_UNIT, n_photos=2, reported_by="tenant@example.com"):
    # Byte content just needs to be distinct per photo so the mock
    # assessor's hash-based scenario picker can vary - these aren't real
    # images and don't need to be, since MockImageAssessor never decodes
    # the bytes as an image (see its own module docstring).
    files = [
        ("files", (f"photo{i}.jpg", f"fake-photo-bytes-{i}".encode(), "image/jpeg"))
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
    resp = client.post(
        "/issues/upload",
        data={"unit_id": "NO-SUCH-UNIT"},
        files=[("files", ("photo.jpg", b"fake-bytes", "image/jpeg"))],
    )
    assert resp.status_code == 404


def test_accepting_work_order_moves_issue_to_in_progress(client):
    issue = _upload_issue(client)
    resp = client.post(f"/issues/{issue['id']}/review", json={"action": "accept"})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["status"] == "accepted"
    assert updated["status"] == "in_progress"


def test_rejecting_work_order_leaves_issue_open(client):
    issue = _upload_issue(client)
    resp = client.post(f"/issues/{issue['id']}/review", json={"action": "reject"})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["work_order"]["status"] == "rejected"
    # The underlying property problem is still there - rejecting the
    # draft doesn't resolve or dismiss the issue itself.
    assert updated["status"] == "open"


def test_review_is_locked_once_decided(client):
    issue = _upload_issue(client)
    client.post(f"/issues/{issue['id']}/review", json={"action": "accept"})

    resp = client.post(f"/issues/{issue['id']}/review", json={"action": "reject"})
    assert resp.status_code == 409


def test_editing_title_and_description_applies_before_accept(client):
    issue = _upload_issue(client)
    resp = client.post(
        f"/issues/{issue['id']}/review",
        json={"title": "Leaking AC unit", "description": "Water staining near ceiling AC vent.", "action": "accept"},
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
