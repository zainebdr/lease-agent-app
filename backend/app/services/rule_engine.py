"""
Runs owner_ruleset.json's 7 rules against a Lease + its matched Unit.

This is fully deterministic real logic — it does not depend on the AI
extractor being mocked or real. Whatever fields the extractor found (or
didn't) flow in here, and each rule independently decides PASS / FAIL /
NOT_DETERMINABLE. Missing data always means NOT_DETERMINABLE, never a
guessed PASS or FAIL — that is the core promise of this function.
"""
import json
from datetime import date

from app.config import RULESET_PATH
from app.db.enums import RuleResult, UnitStatus
from app.db.models import Lease, Unit


def _months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) - (1 if d2.day < d1.day else 0)


def load_ruleset() -> list[dict]:
    with open(RULESET_PATH) as f:
        return json.load(f)["rules"]


def _check_r1(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if lease.deposit_amount is None or lease.monthly_rent is None:
        return RuleResult.NOT_DETERMINABLE, "Deposit amount or monthly rent not found in document.", None
    if lease.deposit_amount >= lease.monthly_rent:
        return RuleResult.PASS, f"Deposit ({lease.deposit_amount}) >= monthly rent ({lease.monthly_rent}).", "deposit clause"
    return RuleResult.FAIL, f"Deposit ({lease.deposit_amount}) is less than monthly rent ({lease.monthly_rent}).", "deposit clause"


def _check_r2(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if lease.escalation_is_defined is None:
        return RuleResult.NOT_DETERMINABLE, "No escalation clause found in document.", None
    if lease.escalation_is_defined:
        return RuleResult.PASS, "Escalation clause defines an actual mechanism or percentage.", lease.escalation_clause_text
    return RuleResult.FAIL, "Escalation clause is vague (e.g. 'as mutually agreed') rather than a defined mechanism.", lease.escalation_clause_text


def _check_r3(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if lease.term_months is None:
        return RuleResult.NOT_DETERMINABLE, "Term length not found in document.", None
    if lease.term_months <= 36:
        return RuleResult.PASS, f"Term is {lease.term_months} months (<= 36).", "term clause"
    return RuleResult.FAIL, f"Term is {lease.term_months} months, exceeds 36-month limit without owner approval.", "term clause"


def _check_r4(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if not lease.commencement_date or not lease.expiry_date:
        return RuleResult.NOT_DETERMINABLE, "Commencement or expiry date not found in document.", None
    if lease.expiry_date <= lease.commencement_date:
        return RuleResult.FAIL, "Expiry date is not after commencement date.", "lease dates"
    actual_months = _months_between(lease.commencement_date, lease.expiry_date)
    if lease.term_months is None:
        return RuleResult.NOT_DETERMINABLE, "Dates are consistent but stated term length was not found to cross-check.", "lease dates"
    if actual_months == lease.term_months:
        return RuleResult.PASS, f"Dates span {actual_months} months, matching stated term.", "lease dates"
    return RuleResult.FAIL, f"Dates span {actual_months} months but stated term is {lease.term_months} months.", "lease dates"


def _check_r5(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    landlord_present = bool(lease.landlord_name)
    tenant_present = bool(lease.tenant_name)
    if not landlord_present or not tenant_present:
        return RuleResult.NOT_DETERMINABLE, "Landlord and/or tenant name not found in document.", None
    if lease.landlord_signed is None or lease.tenant_signed is None:
        return RuleResult.NOT_DETERMINABLE, "Signature block not clearly found in document.", "signature block"
    if lease.landlord_signed and lease.tenant_signed:
        return RuleResult.PASS, "Both parties are identified and signed.", "signature block"
    return RuleResult.FAIL, "One or both parties are missing a signature.", "signature block"


def _check_r6(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if lease.annual_rent is None or lease.monthly_rent is None:
        return RuleResult.NOT_DETERMINABLE, "Annual or monthly rent not found in document.", None
    if abs(lease.annual_rent - lease.monthly_rent * 12) < 0.01:
        return RuleResult.PASS, "Annual rent equals monthly rent x 12.", "rent clause"
    return RuleResult.FAIL, f"Annual rent ({lease.annual_rent}) does not equal monthly rent x 12 ({lease.monthly_rent * 12}).", "rent clause"


def _check_r7(lease: Lease, unit: Unit | None) -> tuple[RuleResult, str, str | None]:
    if not lease.unit_id:
        return RuleResult.NOT_DETERMINABLE, "Could not match lease to a known unit_id.", None
    if unit is None:
        return RuleResult.FAIL, f"Unit '{lease.unit_id}' does not exist in owner's unit records.", None
    if unit.status == UnitStatus.AVAILABLE:
        return RuleResult.PASS, f"Unit '{lease.unit_id}' exists and is marked available.", None
    return RuleResult.FAIL, f"Unit '{lease.unit_id}' is currently '{unit.status.value}', not available.", None


_CHECK_FUNCS = {
    "R1": _check_r1, "R2": _check_r2, "R3": _check_r3, "R4": _check_r4,
    "R5": _check_r5, "R6": _check_r6, "R7": _check_r7,
}


def run_all_checks(lease: Lease, unit: Unit | None) -> list[dict]:
    rules = load_ruleset()
    results = []
    for rule in rules:
        func = _CHECK_FUNCS.get(rule["id"])
        if not func:
            continue
        result, reason, source_clause = func(lease, unit)
        results.append(
            {
                "rule_id": rule["id"],
                "description": rule["description"],
                "severity": rule["severity"],
                "result": result,
                "reason": reason,
                "source_clause": source_clause,
            }
        )
    return results
