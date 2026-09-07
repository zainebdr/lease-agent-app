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
        # landlord_signed/tenant_signed are inferred from the whole
        # document (see the next test), not one line, so they don't
        # carry a "line N" span the way label:value fields do.
        if name not in ("landlord_signed", "tenant_signed"):
            assert field.source_span, name


def test_signature_detection_is_presence_based_not_actually_verified():
    """Documents a known, deliberate limitation rather than hiding it:
    landlord_signed/tenant_signed just check whether "landlord"/"tenant"
    and "signature" appear anywhere in the document - not whether the
    signature line is actually filled in. The shipped sample lease has
    blank signature lines ("____________________") and still reads as
    signed. Real handwriting/mark detection would need a vision model,
    which is out of scope for Part A (a text-only lease reader) - see
    README "what was left out"."""
    fields = MockLeaseExtractor().extract(SAMPLE_LEASE)
    assert fields["landlord_signed"].value is True
    assert fields["tenant_signed"].value is True


def test_omits_fields_it_cannot_find_rather_than_guessing():
    minimal_text = "This document mentions nothing structured at all."
    fields = MockLeaseExtractor().extract(minimal_text)

    # landlord_signed/tenant_signed are always computed (whole-document
    # presence check, see above) - everything else must be absent, not
    # defaulted, when the extractor found no matching line.
    assert "landlord_name" not in fields
    assert "tenant_name" not in fields
    assert "monthly_rent" not in fields
    assert "commencement_date" not in fields
    assert fields["landlord_signed"].value is False
    assert fields["tenant_signed"].value is False


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
