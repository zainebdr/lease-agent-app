"""
Tests for the high-severity finalize gate (app/api/leases.py:
_apply_high_severity_gate).

The defect these cover: `severity` was read from owner_ruleset.json,
stored on every rule_checks row and shown in the UI, and then consulted
by no decision anywhere. A lease with a deposit of 100 against a rent of
9,000 - R1, severity "high", FAIL - could be finalized to "accepted"
with a plain 200, and the unit flipped to occupied. The ruleset was
decorative.

The rule now binds, with a deliberate escape hatch: a reviewer may still
accept over a failing high-severity rule, but only by stating a reason,
and the reason and the overridden rule ids are recorded on the lease.
"""

def _upload(client, text: str) -> dict:
    resp = client.post("/leases/upload", files={"file": ("lease.txt", text, "text/plain")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _accept_all(lease: dict, **extra) -> dict:
    return {
        "field_actions": [{"field_name": k, "action": "accept"} for k in lease["review_status"]],
        "finalize": True,
        "reviewed_by": "owner@example.com",
        **extra,
    }


def _rule(lease: dict, rule_id: str) -> dict:
    return next(rc for rc in lease["rule_checks"] if rc["rule_id"] == rule_id)


# R1 (high) FAILs: the deposit is far below one month's rent. Everything
# else about this lease is consistent, so R1 is the only thing standing
# between it and acceptance.
DEPOSIT_TOO_LOW = """Landlord: Acme Ltd
Tenant: Bob Smith
Premises: Apartment 1204, Tower B
Commencement Date: 2025-03-01
Expiry Date: 2026-03-01
Term: 12 months
Monthly Rent: QAR 9,000
Annual Rent: QAR 108,000
Security Deposit: QAR 100
Escalation Clause: Rent shall increase by 5% annually.
Landlord Signature: Acme Ltd
Tenant Signature: Bob Smith
"""

# R2 (medium) FAILs on the vague escalation clause; nothing high-severity
# fails. A medium failure is reported, not enforced.
VAGUE_ESCALATION = DEPOSIT_TOO_LOW.replace(
    "Security Deposit: QAR 100", "Security Deposit: QAR 9,000"
).replace(
    "Escalation Clause: Rent shall increase by 5% annually.",
    "Escalation Clause: Rent shall increase as mutually agreed.",
)


def test_finalize_is_refused_while_a_high_severity_rule_fails(client):
    lease = _upload(client, DEPOSIT_TOO_LOW)
    assert _rule(lease, "R1")["result"] == "FAIL"
    assert _rule(lease, "R1")["severity"] == "high"

    resp = client.post(f"/leases/{lease['id']}/review", json=_accept_all(lease))
    assert resp.status_code == 409, resp.text
    assert "R1" in resp.json()["detail"]

    # Refused means nothing moved: the lease is still a reviewable draft
    # and the unit was not occupied on the way out.
    after = client.get(f"/leases/{lease['id']}").json()
    assert after["status"] == "draft"
    assert client.get("/units/MC-B-1204").json()["unit"]["status"] == "available"


def test_an_explicit_override_is_allowed_and_recorded(client):
    lease = _upload(client, DEPOSIT_TOO_LOW)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json=_accept_all(lease, override_reason="Owner accepted a reduced deposit in writing, ref MC-2025-114."),
    )
    assert resp.status_code == 200, resp.text
    accepted = resp.json()

    assert accepted["status"] == "accepted"
    assert accepted["high_severity_overridden_rules"] == ["R1"]
    assert "MC-2025-114" in accepted["high_severity_override_reason"]
    # The failing check is not rewritten to look clean - an overridden
    # lease still carries the FAIL that was overridden.
    assert _rule(accepted, "R1")["result"] == "FAIL"


def test_a_token_override_reason_is_not_enough(client):
    lease = _upload(client, DEPOSIT_TOO_LOW)
    resp = client.post(f"/leases/{lease['id']}/review", json=_accept_all(lease, override_reason="ok"))
    assert resp.status_code == 400
    assert client.get(f"/leases/{lease['id']}").json()["status"] == "draft"


def test_fixing_the_underlying_field_clears_the_gate_without_an_override(client):
    lease = _upload(client, DEPOSIT_TOO_LOW)
    actions = [{"field_name": k, "action": "accept"} for k in lease["review_status"] if k != "deposit_amount"]
    actions.append({"field_name": "deposit_amount", "action": "edit", "new_value": 9000})

    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"field_actions": actions, "finalize": True, "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    accepted = resp.json()
    assert accepted["status"] == "accepted"
    # No override was needed, so none is recorded - "accepted cleanly"
    # stays distinguishable from "accepted anyway".
    assert accepted["high_severity_override_reason"] is None
    assert _rule(accepted, "R1")["result"] == "PASS"


def test_a_medium_severity_failure_does_not_block_acceptance(client):
    lease = _upload(client, VAGUE_ESCALATION)
    assert _rule(lease, "R2")["result"] == "FAIL"
    assert _rule(lease, "R2")["severity"] == "medium"

    resp = client.post(f"/leases/{lease['id']}/review", json=_accept_all(lease))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"
    assert resp.json()["high_severity_override_reason"] is None


def test_a_not_determinable_high_severity_rule_does_not_block(client):
    """NOT_DETERMINABLE is not FAIL. A lease whose signature state can't
    be read from the text (R5, high) is exactly the case the human
    review flow exists for - it must reach a reviewer, not be refused."""
    no_signatures = DEPOSIT_TOO_LOW.replace(
        "Security Deposit: QAR 100", "Security Deposit: QAR 9,000"
    ).replace("Landlord Signature: Acme Ltd\n", "").replace("Tenant Signature: Bob Smith\n", "")

    lease = _upload(client, no_signatures)
    assert _rule(lease, "R5")["result"] == "NOT_DETERMINABLE"

    resp = client.post(f"/leases/{lease['id']}/review", json=_accept_all(lease))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"


def test_the_gate_does_not_interfere_with_rejecting_a_lease(client):
    lease = _upload(client, DEPOSIT_TOO_LOW)
    resp = client.post(
        f"/leases/{lease['id']}/review",
        json={"action": "reject", "reviewed_by": "owner@example.com"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
