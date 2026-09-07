"""
Integration tests for /leases/* (app/api/leases.py), against the mock
lease extractor (no API key needed - see conftest.py). Covers the full
upload -> review -> finalize lifecycle, and the four review-time
behaviors added after the initial build: the finalize lock, whole-lease
reject, rule-check refresh after an edit, and edit-value validation.
"""
from pathlib import Path

SAMPLE_LEASE_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_lease.txt"


def _upload_sample_lease(client):
    with open(SAMPLE_LEASE_PATH, "rb") as f:
        resp = client.post("/leases/upload", files={"file": ("sample_lease.txt", f, "text/plain")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rule_result(lease_json, rule_id):
    for rc in lease_json["rule_checks"]:
        if rc["rule_id"] == rule_id:
            return rc["result"]
    raise AssertionError(f"{rule_id} missing from rule_checks")


def test_upload_extracts_fields_matches_unit_and_runs_all_rule_checks(client):
    lease = _upload_sample_lease(client)

    assert lease["status"] == "draft"
    assert lease["unit_id"] == "MC-B-1204"  # matched from the "Apartment 1204" premises line
    assert lease["landlord_name"] == "Marina Crest Holdings W.L.L."
    assert lease["tenant_name"] == "Ahmed Al-Sayed"
    assert len(lease["rule_checks"]) == 7
    assert all(v == "pending" for v in lease["review_status"].values())

    # Sample lease is internally consistent, so every determinable rule
    # should pass: deposit == rent (R1), escalation has a % (R2), 12mo
    # term (R3), dates match the stated term (R4), both parties named
    # and "signed" (R5, presence-based per the mock extractor), annual
    # == monthly x 12 (R6), and the unit exists and is available (R7).
    for rule_id in ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]:
        assert _rule_result(lease, rule_id) == "PASS", rule_id


def test_accept_all_then_finalize_accepts_lease_and_occupies_unit(client):
    lease = _upload_sample_lease(client)
    lease_id = lease["id"]

    field_actions = [
        {"field_name": k, "action": "accept"} for k in lease["review_status"].keys()
    ]
    resp = client.post(f"/leases/{lease_id}/review", json={"field_actions": field_actions, "finalize": True})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["status"] == "accepted"
    assert all(v == "accepted" for v in updated["review_status"].values())

    unit_resp = client.get(f"/units/{lease['unit_id']}")
    assert unit_resp.status_code == 200
    assert unit_resp.json()["unit"]["status"] == "occupied"


def test_finalize_fails_while_any_field_still_pending(client):
    lease = _upload_sample_lease(client)
    resp = client.post(f"/leases/{lease['id']}/review", json={"field_actions": [], "finalize": True})
    assert resp.status_code == 400
    assert "pending" in resp.json()["detail"].lower()


def test_review_is_locked_once_finalized(client):
    lease = _upload_sample_lease(client)
    field_actions = [{"field_name": k, "action": "accept"} for k in lease["review_status"].keys()]
    client.post(f"/leases/{lease['id']}/review", json={"field_actions": field_actions, "finalize": True})

    # Any further review call on this lease - even an unrelated one -
    # must be rejected now that it's no longer a draft.
    resp = client.post(f"/leases/{lease['id']}/review", json={"field_actions": [], "finalize": False})
    assert resp.status_code == 409


def test_whole_lease_reject_bypasses_per_field_review(client):
    lease = _upload_sample_lease(client)
    resp = client.post(f"/leases/{lease['id']}/review", json={"action": "reject"})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["status"] == "rejected"
    # A whole-lease reject doesn't touch per-field state - nothing was
    # individually accepted/rejected/edited, the document was.
    assert all(v == "pending" for v in updated["review_status"].values())

    # And it's now locked too, same as an accepted lease.
    resp2 = client.post(f"/leases/{lease['id']}/review", json={"action": "reject"})
    assert resp2.status_code == 409


def test_unknown_action_value_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(f"/leases/{lease['id']}/review", json={"action": "approve"})
    assert resp.status_code == 400


def test_editing_a_field_reruns_rule_checks(client):
    lease = _upload_sample_lease(client)
    assert _rule_result(lease, "R1") == "PASS"  # deposit (9000) >= rent (9000)

    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "deposit_amount", "action": "edit", "new_value": 500}]},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["deposit_amount"] == 500
    assert updated["review_status"]["deposit_amount"] == "edited"
    # R1 must reflect the edited value, not the original extraction -
    # this is the whole point of refresh_rule_checks().
    assert _rule_result(updated, "R1") == "FAIL"


def test_editing_a_non_editable_field_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "status", "action": "edit", "new_value": "accepted"}]},
    )
    assert resp.status_code == 400


def test_editing_a_number_field_with_a_non_numeric_value_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "monthly_rent", "action": "edit", "new_value": "a lot"}]},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("monthly_rent" in str(d) for d in detail)


def test_editing_a_number_field_with_a_boolean_is_rejected(client):
    # bool is a subclass of int in Python - this is specifically the
    # case FIELD_VALIDATORS' explicit isinstance(value, bool) guard
    # exists for for numeric fields.
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "term_months", "action": "edit", "new_value": True}]},
    )
    assert resp.status_code == 422


def test_editing_a_date_field_with_an_invalid_date_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "commencement_date", "action": "edit", "new_value": "not-a-date"}]},
    )
    assert resp.status_code == 422


def test_editing_a_date_field_with_a_valid_iso_string_is_coerced(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "expiry_date", "action": "edit", "new_value": "2027-01-15"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expiry_date"] == "2027-01-15"


def test_editing_a_string_field_to_blank_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "tenant_name", "action": "edit", "new_value": "   "}]},
    )
    assert resp.status_code == 422
