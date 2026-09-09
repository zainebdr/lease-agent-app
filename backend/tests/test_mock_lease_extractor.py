"""
Tests for MockLeaseExtractor (app/ai/mock_lease_extractor.py) - the
deterministic, regex/label-based stand-in used whenever ANTHROPIC_API_KEY
isn't set. These are the tests that would catch a regression in parsing
logic without needing a real model call.
"""
from pathlib import Path

from app.ai.mock_lease_extractor import MockLeaseExtractor

SAMPLE_LEASE = (Path(__file__).resolve().parent.parent / "data" / "sample_lease.txt").read_text()


def test_extracts_all_fields_from_the_shipped_sample_lease():
    fields = MockLeaseExtractor().extract(SAMPLE_LEASE)

    assert fields["landlord_name"].value == "Marina Crest Holdings W.L.L."
    assert fields["tenant_name"].value == "Ahmed Al-Sayed"
    assert fields["commencement_date"].value == "2025-03-01"
    assert fields["expiry_date"].value == "2026-03-01"
    assert fields["term_months"].value == 12
    assert fields["monthly_rent"].value == 9000.0
    assert fields["annual_rent"].value == 108000.0
    assert fields["deposit_amount"].value == 9000.0
    assert fields["escalation_is_defined"].value is True
    assert "Apartment 1204" in fields["unit_reference_text"].value


def test_every_extracted_field_carries_a_source_span_and_confidence():
    fields = MockLeaseExtractor().extract(SAMPLE_LEASE)
    for name, field in fields.items():
        assert 0.0 <= field.confidence <= 1.0, name
        assert field.source_span, name


def test_blank_signature_lines_are_unknown_not_signed():
    """The shipped sample lease has blank signature lines
    ("Landlord Signature: ____________________"). A plain-text rendering
    cannot show ink, so the honest answer is "I don't know", and the
    field is omitted - which the rule engine reads as NOT_DETERMINABLE.

    This is the regression test for the worst bug this extractor had:
    signatures used to be a whole-document keyword check
    ("landlord" in text and "signature" in text), so the two parties
    could never disagree, the value could never be unknown, and a lease
    with empty signature lines reported both parties signed - turning
    R5 (severity: high) into a false PASS."""
    fields = MockLeaseExtractor().extract(SAMPLE_LEASE)
    assert "landlord_signed" not in fields
    assert "tenant_signed" not in fields


def test_signatures_are_detected_per_party_not_document_wide():
    text = (
        "Landlord: Acme Ltd\n"
        "Tenant: Bob Smith\n"
        "Landlord Signature: Acme Ltd (authorised signatory)\n"
        "The tenant has NOT signed this document.\n"
    )
    fields = MockLeaseExtractor().extract(text)
    assert fields["landlord_signed"].value is True
    assert fields["tenant_signed"].value is False


def test_a_line_naming_both_parties_says_nothing_about_either():
    # "signed by both Landlord and Tenant below" is prose that mentions
    # both parties and the word "signed"; it cannot settle whether
    # either one actually signed, so neither field is produced.
    text = "This agreement is signed by both Landlord and Tenant below.\n"
    fields = MockLeaseExtractor().extract(text)
    assert "landlord_signed" not in fields
    assert "tenant_signed" not in fields


def test_omits_fields_it_cannot_find_rather_than_guessing():
    minimal_text = "This document mentions nothing structured at all."
    fields = MockLeaseExtractor().extract(minimal_text)

    # Every field must be absent, not defaulted, when the extractor
    # found no matching labelled line - signatures included.
    assert fields == {}


def test_labels_are_anchored_so_prose_is_not_mistaken_for_a_field():
    """Each of these used to produce a confidently wrong value, because
    matching was "first line containing this substring anywhere"."""
    # "Deposit Terms are set out in Schedule 2." -> deposit_amount = 2.0
    fields = MockLeaseExtractor().extract("Deposit Terms are set out in Schedule 2.")
    assert "deposit_amount" not in fields

    # ...and the same sentence written as a labelled line is still a
    # cross-reference, not an amount.
    fields = MockLeaseExtractor().extract("Deposit Terms: set out in Schedule 2")
    assert "deposit_amount" not in fields

    # "Terms and Conditions apply over 999 months" -> term_months = 999
    fields = MockLeaseExtractor().extract("Terms and Conditions apply over 999 months of usage.")
    assert "term_months" not in fields

    # A run-on line is a sentence, not two names.
    fields = MockLeaseExtractor().extract(
        "Landlord: Acme Ltd and the Tenant: Bob Smith agree as follows."
    )
    assert "landlord_name" not in fields
    assert "tenant_name" not in fields


def test_the_best_matching_label_wins_not_the_first_one():
    text = "Rent: 500\nMonthly Rent: QAR 9,000\n"
    fields = MockLeaseExtractor().extract(text)
    assert fields["monthly_rent"].value == 9000.0


def test_a_stated_zero_is_a_value_not_a_missing_field():
    # `if num:` used to drop these as falsy, so a lease stating no
    # deposit looked identical to one where no deposit was found.
    fields = MockLeaseExtractor().extract("Monthly Rent: QAR 0\nSecurity Deposit: QAR 0\n")
    assert fields["monthly_rent"].value == 0.0
    assert fields["deposit_amount"].value == 0.0


def test_confidence_reflects_match_quality_rather_than_being_a_constant():
    # An unambiguous ISO date beats a numeric date that could equally be
    # D/M/Y or M/D/Y and is only day-first by assumption.
    unambiguous = MockLeaseExtractor().extract("Commencement Date: 2025-12-05")
    ambiguous = MockLeaseExtractor().extract("Commencement Date: 05/12/2025")
    assert unambiguous["commencement_date"].value == ambiguous["commencement_date"].value
    assert ambiguous["commencement_date"].confidence < unambiguous["commencement_date"].confidence

    # An exact label beats the same keyword buried in a longer one.
    exact = MockLeaseExtractor().extract("Landlord: Acme Ltd")
    buried = MockLeaseExtractor().extract("Details of the Landlord: Acme Ltd")
    assert buried["landlord_name"].confidence < exact["landlord_name"].confidence


def test_vague_escalation_clause_is_not_treated_as_defined():
    # Would otherwise match the "is a defined mechanism" regex (has
    # "annually") if not for the vague-phrase override - this is what
    # actually exercises that override, not just the no-match case.
    text = "Escalation Clause: Rent shall be increased annually as mutually agreed between the parties."
    fields = MockLeaseExtractor().extract(text)
    assert fields["escalation_is_defined"].value is False


def test_escalation_clause_with_no_defined_mechanism_at_all():
    text = "Escalation Clause: Rent shall be increased as mutually agreed between the parties."
    fields = MockLeaseExtractor().extract(text)
    assert fields["escalation_is_defined"].value is False


def test_extraction_is_deterministic():
    a = MockLeaseExtractor().extract(SAMPLE_LEASE)
    b = MockLeaseExtractor().extract(SAMPLE_LEASE)
    assert {k: v.value for k, v in a.items()} == {k: v.value for k, v in b.items()}


def test_every_field_records_what_produced_it():
    """A stored confidence is uninterpretable without knowing what
    produced it: 0.65 from this label-matching extractor is a
    measurement ("the label matched weakly"), while 0.65 from a model is
    the model's own opinion of itself. Same number, different meaning,
    so the record names the source - the same reason
    IssuePhoto.assessed_by exists on the Part B side."""
    fields = MockLeaseExtractor().extract(SAMPLE_LEASE)
    assert fields
    for name, field in fields.items():
        assert field.extracted_by == "mock-labels", name
        assert field.to_dict()["extracted_by"] == "mock-labels", name
