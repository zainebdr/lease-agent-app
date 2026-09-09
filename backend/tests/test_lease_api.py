"""
Integration tests for /leases/* (app/api/leases.py), against the mock
lease extractor (no API key needed - see conftest.py). Covers the full
upload -> review -> finalize lifecycle, and the review-time behaviors
added after the initial build: the finalize lock, whole-lease reject,
rule-check refresh after an edit (and that the pre-edit result survives
in history rather than being deleted), edit-value validation, the
required reviewed_by/decision_at on any decision, and the one-accepted-
lease-per-unit guard.
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
    # passes: deposit == rent (R1), escalation has a % (R2), 12mo term
    # (R3), dates match the stated term (R4), annual == monthly x 12
    # (R6), and the unit exists and is available (R7).
    for rule_id in ["R1", "R2", "R3", "R4", "R6", "R7"]:
        assert _rule_result(lease, rule_id) == "PASS", rule_id

    # R5 is the one that is NOT determinable, and that is the correct
    # answer rather than a weaker one: the sample lease's signature
    # lines are blank ("Landlord Signature: ____"), and plain text
    # cannot show whether they were signed. This used to report PASS,
    # because signatures were a whole-document keyword check that could
    # never say "unknown" - see
    # tests/test_mock_lease_extractor.py:test_blank_signature_lines_are_unknown_not_signed.
    assert _rule_result(lease, "R5") == "NOT_DETERMINABLE"


def test_accept_all_then_finalize_accepts_lease_and_occupies_unit(client):
    lease = _upload_sample_lease(client)
    lease_id = lease["id"]

    field_actions = [
        {"field_name": k, "action": "accept"} for k in lease["review_status"].keys()
    ]
    resp = client.post(
        f"/leases/{lease_id}/review",
        json={"field_actions": field_actions, "finalize": True, "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["status"] == "accepted"
    assert all(v == "accepted" for v in updated["review_status"].values())
    # Who decided this, and when, must be recorded - this is the whole
    # point of Lease.reviewed_by/decision_at.
    assert updated["reviewed_by"] == "owner@example.com"
    assert updated["decision_at"] is not None

    unit_resp = client.get(f"/units/{lease['unit_id']}")
    assert unit_resp.status_code == 200
    assert unit_resp.json()["unit"]["status"] == "occupied"


def test_finalize_accept_without_reviewed_by_is_rejected(client):
    lease = _upload_sample_lease(client)
    field_actions = [{"field_name": k, "action": "accept"} for k in lease["review_status"].keys()]
    resp = client.post(f"/leases/{lease['id']}/review", json={"field_actions": field_actions, "finalize": True})
    assert resp.status_code == 400
    assert "reviewed_by" in resp.json()["detail"]


def test_whole_lease_reject_without_reviewed_by_is_rejected(client):
    lease = _upload_sample_lease(client)
    resp = client.post(f"/leases/{lease['id']}/review", json={"action": "reject"})
    assert resp.status_code == 400
    assert "reviewed_by" in resp.json()["detail"]


def test_cannot_accept_a_lease_for_a_unit_that_already_has_an_accepted_lease(client):
    # Both uploads of the same sample lease match the same seeded unit
    # (MC-B-1204) - accepting the second one while the first is already
    # "accepted" would double-book the unit.
    first = _upload_sample_lease(client)
    first_actions = [{"field_name": k, "action": "accept"} for k in first["review_status"].keys()]
    resp = client.post(
        f"/leases/{first['id']}/review",
        json={"field_actions": first_actions, "finalize": True, "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text

    second = _upload_sample_lease(client)
    second_actions = [{"field_name": k, "action": "accept"} for k in second["review_status"].keys()]
    resp2 = client.post(
        f"/leases/{second['id']}/review",
        json={"field_actions": second_actions, "finalize": True, "reviewed_by": "owner@example.com"},
    )
    assert resp2.status_code == 409, resp2.text
    assert "already has an accepted lease" in resp2.json()["detail"]

    # The rejected-by-conflict attempt must not have partially applied -
    # the second lease stays in draft, not silently accepted.
    unchanged = client.get(f"/leases/{second['id']}").json()
    assert unchanged["status"] == "draft"


def test_finalize_fails_while_any_field_still_pending(client):
    lease = _upload_sample_lease(client)
    resp = client.post(f"/leases/{lease['id']}/review", json={"field_actions": [], "finalize": True})
    assert resp.status_code == 400
    assert "pending" in resp.json()["detail"].lower()


def test_review_is_locked_once_finalized(client):
    lease = _upload_sample_lease(client)
    field_actions = [{"field_name": k, "action": "accept"} for k in lease["review_status"].keys()]
    client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": field_actions, "finalize": True, "reviewed_by": "owner@example.com"},
    )

    # Any further review call on this lease - even an unrelated one -
    # must be rejected now that it's no longer a draft.
    resp = client.post(f"/leases/{lease['id']}/review", json={"field_actions": [], "finalize": False})
    assert resp.status_code == 409


def test_whole_lease_reject_bypasses_per_field_review(client):
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"action": "reject", "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["status"] == "rejected"
    # A whole-lease reject doesn't touch per-field state - nothing was
    # individually accepted/rejected/edited, the document was.
    assert all(v == "pending" for v in updated["review_status"].values())
    assert updated["reviewed_by"] == "owner@example.com"
    assert updated["decision_at"] is not None

    # And it's now locked too, same as an accepted lease.
    resp2 = client.post(
        f"/leases/{lease['id']}/review",
        json={"action": "reject", "reviewed_by": "owner@example.com"},
    )
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
    # rule_checks (the "current" view) must show exactly one row per
    # rule, not the old one alongside the new one.
    assert len([rc for rc in updated["rule_checks"] if rc["rule_id"] == "R1"]) == 1


def test_editing_a_field_preserves_prior_rule_check_result_in_history(client):
    # This is the traceability case the product is sold on: a reviewer
    # must be able to see that R1 was PASS at upload time and FAIL after
    # a human edited the deposit, not just the latest state.
    lease = _upload_sample_lease(client)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": [{"field_name": "deposit_amount", "action": "edit", "new_value": 500}]},
    )
    updated = resp.json()

    r1_history = [rc for rc in updated["rule_check_history"] if rc["rule_id"] == "R1"]
    assert len(r1_history) == 2, "expected the original PASS row and the new FAIL row to both survive"

    old_row = next(rc for rc in r1_history if not rc["is_current"])
    new_row = next(rc for rc in r1_history if rc["is_current"])
    assert old_row["result"] == "PASS"
    assert old_row["superseded_at"] is not None
    assert new_row["result"] == "FAIL"
    assert new_row["superseded_at"] is None


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


def test_upload_omits_renewal_and_termination_terms_when_not_in_document(client):
    # The sample lease has an escalation clause that happens to mention
    # the word "renewal" in passing ("...upon renewal") but no actual
    # renewal or termination clause - missing data is never guessed, so
    # both fields should come back empty rather than the extractor
    # latching onto that unrelated mention.
    lease = _upload_sample_lease(client)
    assert lease["renewal_terms_text"] is None
    assert lease["termination_terms_text"] is None
    assert "renewal_terms_text" not in lease["review_status"]
    assert "termination_terms_text" not in lease["review_status"]


def test_upload_extracts_renewal_and_termination_terms_when_present(client):
    lease_text = (
        SAMPLE_LEASE_PATH.read_text()
        + "\nRenewal: This lease renews automatically for successive 12-month "
        "terms unless either party gives 60 days written notice.\n"
        "Termination: Either party may terminate early with 90 days written "
        "notice and payment of a 2-month rent penalty.\n"
    )
    resp = client.post(
        "/leases/upload",
        files={"file": ("with_clauses.txt", lease_text.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    lease = resp.json()

    assert "renews automatically" in lease["renewal_terms_text"]
    assert "terminate early" in lease["termination_terms_text"]
    assert lease["review_status"]["renewal_terms_text"] == "pending"
    assert lease["review_status"]["termination_terms_text"] == "pending"
    # Traceable back to where it came from, same as every other field.
    assert lease["extracted_fields"]["renewal_terms_text"]["source_span"]
    assert lease["extracted_fields"]["termination_terms_text"]["source_span"]


def test_renewal_and_termination_terms_are_editable_via_review(client):
    # Also covers the case the mock extractor found nothing for these
    # two fields at all (test_upload_omits_... above) - a human should
    # still be able to add them by hand, same as any other editable
    # field the AI missed.
    lease = _upload_sample_lease(client)
    assert lease["renewal_terms_text"] is None

    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={
            "field_actions": [
                {
                    "field_name": "renewal_terms_text",
                    "action": "edit",
                    "new_value": "Automatic 12-month renewal absent 60 days notice.",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["renewal_terms_text"] == "Automatic 12-month renewal absent 60 days notice."
    assert updated["review_status"]["renewal_terms_text"] == "edited"
