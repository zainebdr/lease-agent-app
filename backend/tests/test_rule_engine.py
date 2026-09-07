"""
Unit tests for the 7 owner_ruleset.json checks (app/services/rule_engine.py).

These are pure logic tests: Lease/Unit ORM objects are constructed
in-memory and never added to a session, since the check functions only
read attributes off them - no database is needed here at all. Each test
sets just the fields the rule under test cares about; the rest are left
at their default (None), which is deliberate - the rule engine's core
promise is that missing data means NOT_DETERMINABLE, not a guess, and
that only shows up if other fields being unset doesn't accidentally
affect the rule being tested.
"""
from datetime import date

import pytest

from app.db.enums import RuleResult, UnitStatus
from app.db.models import Lease, Unit
from app.services import rule_engine


def _result(lease, unit, rule_id):
    for r in rule_engine.run_all_checks(lease, unit):
        if r["rule_id"] == rule_id:
            return r
    raise AssertionError(f"{rule_id} did not run - check owner_ruleset.json / _CHECK_FUNCS")


# --- R1: deposit >= one month's rent -------------------------------------

@pytest.mark.parametrize(
    "deposit, rent, expected",
    [
        (9000, 9000, RuleResult.PASS),   # equal counts as satisfying "at least"
        (12000, 9000, RuleResult.PASS),
        (5000, 9000, RuleResult.FAIL),
        (None, 9000, RuleResult.NOT_DETERMINABLE),
        (9000, None, RuleResult.NOT_DETERMINABLE),
    ],
)
def test_r1_deposit_vs_rent(deposit, rent, expected):
    lease = Lease(deposit_amount=deposit, monthly_rent=rent)
    assert _result(lease, None, "R1")["result"] == expected


# --- R2: escalation clause must be a defined mechanism, not vague -------

@pytest.mark.parametrize(
    "is_defined, expected",
    [(True, RuleResult.PASS), (False, RuleResult.FAIL), (None, RuleResult.NOT_DETERMINABLE)],
)
def test_r2_escalation_defined(is_defined, expected):
    lease = Lease(escalation_is_defined=is_defined)
    assert _result(lease, None, "R2")["result"] == expected


# --- R3: term must not exceed 36 months ----------------------------------

@pytest.mark.parametrize(
    "term_months, expected",
    [(12, RuleResult.PASS), (36, RuleResult.PASS), (37, RuleResult.FAIL), (None, RuleResult.NOT_DETERMINABLE)],
)
def test_r3_term_length(term_months, expected):
    lease = Lease(term_months=term_months)
    assert _result(lease, None, "R3")["result"] == expected


# --- R4: expiry after commencement, and term must match the date span ---

def test_r4_dates_consistent_with_term():
    lease = Lease(
        commencement_date=date(2025, 3, 1), expiry_date=date(2026, 3, 1), term_months=12,
    )
    assert _result(lease, None, "R4")["result"] == RuleResult.PASS


def test_r4_term_does_not_match_date_span():
    lease = Lease(
        commencement_date=date(2025, 3, 1), expiry_date=date(2026, 3, 1), term_months=6,
    )
    assert _result(lease, None, "R4")["result"] == RuleResult.FAIL


def test_r4_expiry_not_after_commencement():
    lease = Lease(
        commencement_date=date(2026, 3, 1), expiry_date=date(2025, 3, 1), term_months=12,
    )
    assert _result(lease, None, "R4")["result"] == RuleResult.FAIL


def test_r4_missing_dates_is_not_determinable():
    lease = Lease(commencement_date=None, expiry_date=None, term_months=12)
    assert _result(lease, None, "R4")["result"] == RuleResult.NOT_DETERMINABLE


def test_r4_dates_consistent_but_no_term_to_crosscheck():
    lease = Lease(commencement_date=date(2025, 3, 1), expiry_date=date(2026, 3, 1), term_months=None)
    assert _result(lease, None, "R4")["result"] == RuleResult.NOT_DETERMINABLE


# --- R5: both parties identified and signed ------------------------------

def test_r5_both_signed_passes():
    lease = Lease(landlord_name="Marina Crest Holdings", tenant_name="Ahmed Al-Sayed",
                   landlord_signed=True, tenant_signed=True)
    assert _result(lease, None, "R5")["result"] == RuleResult.PASS


def test_r5_one_party_unsigned_fails():
    lease = Lease(landlord_name="Marina Crest Holdings", tenant_name="Ahmed Al-Sayed",
                   landlord_signed=True, tenant_signed=False)
    assert _result(lease, None, "R5")["result"] == RuleResult.FAIL


def test_r5_missing_party_name_is_not_determinable():
    lease = Lease(landlord_name=None, tenant_name="Ahmed Al-Sayed",
                   landlord_signed=True, tenant_signed=True)
    assert _result(lease, None, "R5")["result"] == RuleResult.NOT_DETERMINABLE


def test_r5_names_present_but_signature_block_not_found():
    lease = Lease(landlord_name="Marina Crest Holdings", tenant_name="Ahmed Al-Sayed",
                   landlord_signed=None, tenant_signed=None)
    assert _result(lease, None, "R5")["result"] == RuleResult.NOT_DETERMINABLE


# --- R6: annual rent must reconcile with monthly rent x 12 --------------

@pytest.mark.parametrize(
    "annual, monthly, expected",
    [
        (108000, 9000, RuleResult.PASS),
        (100000, 9000, RuleResult.FAIL),
        (None, 9000, RuleResult.NOT_DETERMINABLE),
    ],
)
def test_r6_annual_matches_monthly(annual, monthly, expected):
    lease = Lease(annual_rent=annual, monthly_rent=monthly)
    assert _result(lease, None, "R6")["result"] == expected


# --- R7: unit must exist and be available --------------------------------

def test_r7_unit_available_passes():
    lease = Lease(unit_id="MC-B-1204")
    unit = Unit(unit_id="MC-B-1204", property_name="p", building_name="b", label="Apartment 1204",
                status=UnitStatus.AVAILABLE)
    assert _result(lease, unit, "R7")["result"] == RuleResult.PASS


def test_r7_unit_occupied_fails():
    lease = Lease(unit_id="MC-B-1205")
    unit = Unit(unit_id="MC-B-1205", property_name="p", building_name="b", label="Apartment 1205",
                status=UnitStatus.OCCUPIED)
    assert _result(lease, unit, "R7")["result"] == RuleResult.FAIL


def test_r7_no_matched_unit_fails():
    lease = Lease(unit_id="MC-X-9999")
    assert _result(lease, None, "R7")["result"] == RuleResult.FAIL


def test_r7_no_unit_reference_is_not_determinable():
    lease = Lease(unit_id=None)
    assert _result(lease, None, "R7")["result"] == RuleResult.NOT_DETERMINABLE
