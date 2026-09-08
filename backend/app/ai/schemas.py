"""
Pydantic schemas that validate the *shape* of a real LLM's JSON response
before any of it is trusted into an ExtractedField/PhotoAssessment and
from there into the database.

Both the Anthropic and OpenAI implementations of each interface
(app/ai/anthropic_lease_extractor.py + app/ai/openai_lease_extractor.py
for lease extraction; app/ai/anthropic_image_assessor.py +
app/ai/openai_image_assessor.py for photo assessment) ask the model for
strict JSON and already handle a response that isn't even parseable
JSON (json.JSONDecodeError) by falling back to an empty/zero-confidence
result rather than crashing. What they didn't handle is a response that
*is* valid JSON but the wrong shape - "confidence": "high" instead of a
number, "contents": "AC unit" instead of ["AC unit"]. That second case
is just as untrustworthy as unparseable text, so all four
implementations validate the parsed JSON against the models below and
treat a validation failure exactly like a parse failure - the same
fallback, not a crash and not silently-wrong data. This is also what
lets both providers be held to the identical contract: whichever one
answers, the same schema decides whether the answer is trustworthy.
(Plain Python's list("AC unit") would iterate the string into single
characters instead of raising - that's the concrete bug this schema
exists to catch, not a hypothetical one.)
"""
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _reject_bool(v: Any) -> Any:
    # bool is a subclass of int (and coerces to float), so a model
    # returning "confidence": true would otherwise silently become 1.0
    # instead of failing validation - the same isinstance(v, bool) guard
    # app/schemas/lease.py's FIELD_VALIDATORS uses for user-edited
    # numeric fields, applied here to model-produced ones.
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


class PhotoAssessmentPayload(BaseModel):
    """The exact shape ASSESSMENT_PROMPT (defined in
    app/ai/anthropic_image_assessor.py, reused by openai_image_assessor.py)
    asks for."""

    condition: str = Field(min_length=1)
    contents: list[str] = []
    damage_notes: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_not_bool(cls, v: Any) -> Any:
        return _reject_bool(v)


class ExtractedFieldPayload(BaseModel):
    """One field entry inside the parsed JSON response produced by
    EXTRACTION_PROMPT (defined in app/ai/anthropic_lease_extractor.py,
    reused by openai_lease_extractor.py):
    {"value": ..., "source_span": "...", "confidence": 0.0-1.0}.

    `value`'s real type varies per field (a name is a string, rent is a
    number, "signed" is a bool, ...) and is intentionally left as `Any`
    here - app/services/lease_extraction.py's own coercion
    (_coerce_date) and, once a human reviews it, app/schemas/lease.py's
    FIELD_VALIDATORS are what actually enforce a specific field's type.
    This schema's job is only to catch the response being the wrong
    *envelope* shape (missing "value", a non-numeric/out-of-range
    confidence) - not to re-implement per-field type checking twice.
    """

    value: Any
    source_span: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_not_bool(cls, v: Any) -> Any:
        return _reject_bool(v)
