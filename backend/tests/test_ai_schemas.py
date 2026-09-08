"""
Unit tests for app/ai/schemas.py - the Pydantic models that validate a
real LLM's parsed JSON response shape before any of the four real
implementations (app/ai/anthropic_image_assessor.py,
openai_image_assessor.py, anthropic_lease_extractor.py,
openai_lease_extractor.py) trust it into an
ExtractedField/PhotoAssessment. Pure pydantic, no network/anthropic/
openai dependency and no FastAPI app needed - these exercise the
schemas directly against the same malformed shapes a real model
response could produce.
"""
import pytest
from pydantic import ValidationError

from app.ai.schemas import ExtractedFieldPayload, PhotoAssessmentPayload


# --- PhotoAssessmentPayload -------------------------------------------------

def test_well_formed_photo_assessment_is_accepted():
    payload = PhotoAssessmentPayload.model_validate(
        {
            "condition": "worn, visible water damage near ceiling",
            "contents": ["AC unit", "water heater"],
            "damage_notes": "staining suggests a slow leak",
            "confidence": 0.82,
        }
    )
    assert payload.condition == "worn, visible water damage near ceiling"
    assert payload.contents == ["AC unit", "water heater"]
    assert payload.confidence == 0.82


def test_photo_assessment_defaults_contents_and_damage_notes():
    payload = PhotoAssessmentPayload.model_validate({"condition": "new", "confidence": 0.9})
    assert payload.contents == []
    assert payload.damage_notes is None


def test_photo_assessment_rejects_bare_string_contents():
    # The concrete bug this schema exists to catch: plain Python's
    # list("AC unit") would iterate the string into single characters
    # instead of raising. A bare string here must be rejected, not
    # silently accepted as a one-character-per-item list.
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate(
            {"condition": "new", "contents": "AC unit", "confidence": 0.5}
        )


def test_photo_assessment_rejects_missing_condition():
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate({"contents": [], "confidence": 0.5})


def test_photo_assessment_rejects_empty_condition():
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate({"condition": "", "confidence": 0.5})


def test_photo_assessment_rejects_non_numeric_confidence():
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate({"condition": "new", "confidence": "high"})


def test_photo_assessment_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate({"condition": "new", "confidence": 1.5})


def test_photo_assessment_rejects_boolean_confidence():
    # bool is a subclass of int/float in Python - "confidence": true must
    # not silently become 1.0.
    with pytest.raises(ValidationError):
        PhotoAssessmentPayload.model_validate({"condition": "new", "confidence": True})


# --- ExtractedFieldPayload ---------------------------------------------------

def test_well_formed_extracted_field_is_accepted():
    payload = ExtractedFieldPayload.model_validate(
        {"value": "Jane Doe", "source_span": "Tenant: Jane Doe", "confidence": 0.95}
    )
    assert payload.value == "Jane Doe"
    assert payload.source_span == "Tenant: Jane Doe"
    assert payload.confidence == 0.95


def test_extracted_field_defaults_confidence_when_omitted():
    payload = ExtractedFieldPayload.model_validate({"value": 8500})
    assert payload.confidence == 0.5
    assert payload.source_span is None


def test_extracted_field_value_type_is_intentionally_unconstrained():
    # A field's real type varies (name -> str, rent -> number, signed ->
    # bool) - this schema only guards the envelope shape, not per-field
    # typing (that's app/services/lease_extraction.py's _coerce_date and
    # app/schemas/lease.py's FIELD_VALIDATORS, applied after a human
    # reviews the value).
    assert ExtractedFieldPayload.model_validate({"value": True}).value is True
    assert ExtractedFieldPayload.model_validate({"value": 102000}).value == 102000


def test_extracted_field_rejects_missing_value():
    with pytest.raises(ValidationError):
        ExtractedFieldPayload.model_validate({"source_span": "some quote", "confidence": 0.5})


def test_extracted_field_rejects_boolean_confidence():
    with pytest.raises(ValidationError):
        ExtractedFieldPayload.model_validate({"value": "x", "confidence": False})


def test_extracted_field_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        ExtractedFieldPayload.model_validate({"value": "x", "confidence": -0.1})
