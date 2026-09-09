"""
Interfaces the rest of the app codes against. Nothing outside app/ai/
ever imports a concrete implementation directly — services get whatever
app/ai/factory.py hands them, so swapping mock <-> real LLM is a config
change, not a code change.
"""
from typing import Protocol, Any


class ExtractedField:
    """A single extracted value with provenance and a confidence score.

    `extracted_by` names what produced the value - "mock-labels", or the
    real model id - for the same reason IssuePhoto.assessed_by exists on
    the Part B side: without it, a stored confidence is uninterpretable.
    0.65 from the label-matching mock is a *measurement* (the label
    matched weakly - go read that line), while 0.65 from a model is the
    model's own opinion of itself, which is not calibrated against
    anything. Same number, same column, different meaning and a
    different action for the human reviewing it - so the record has to
    say which one it is.

    Note that neither the brief nor owner_ruleset.json asks for
    confidence at all; it exists to answer "which of these fields should
    a human look at first", which the brief does ask for ("flag ...
    values that look wrong"). It deliberately never gates a decision -
    a machine's estimate of its own reliability is the last thing that
    should be allowed to overrule a human. Contrast RuleCheck.severity,
    which is the *owner's* policy and does block acceptance
    (app/api/leases.py:_apply_high_severity_gate).
    """

    def __init__(
        self,
        value: Any,
        source_span: str | None,
        confidence: float,
        extracted_by: str = "unknown",
    ):
        self.value = value
        self.source_span = source_span
        self.confidence = confidence
        self.extracted_by = extracted_by

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source_span": self.source_span,
            "confidence": self.confidence,
            "extracted_by": self.extracted_by,
        }


class LeaseExtractor(Protocol):
    def extract(self, document_text: str) -> dict[str, ExtractedField]:
        """
        Returns a dict keyed by field name, e.g.:
        {
          "landlord_name": ExtractedField("Marina Crest Holdings", "para 1", 0.95),
          "monthly_rent": ExtractedField(8500, "para 4", 0.8),
          ...
        }
        Fields the extractor could not find are simply omitted — the
        caller (rule engine / API layer) treats missing keys as
        NOT_DETERMINABLE rather than guessing a default.
        """
        ...


class PhotoAssessment:
    """The result of assessing a single uploaded property photo."""

    def __init__(
        self,
        condition: str,
        contents: list[str],
        damage_notes: str | None,
        confidence: float,
        assessed_by: str = "unknown",
    ):
        self.condition = condition
        self.contents = contents
        self.damage_notes = damage_notes
        self.confidence = confidence
        self.assessed_by = assessed_by  # "mock" or the real model id used

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "contents": self.contents,
            "damage_notes": self.damage_notes,
            "confidence": self.confidence,
            "assessed_by": self.assessed_by,
        }


class ImageAssessor(Protocol):
    def assess(self, image_bytes: bytes, filename: str) -> PhotoAssessment:
        """
        Assess a single property photo:
        - condition: e.g. "new" vs. "worn/old", any visible damage
        - contents: equipment/fixtures visible (AC unit, water heater,
          appliances, ...) so the owner knows what the unit holds and
          what the issue concerns
        Always returns a result (never omits fields the way lease
        extraction can) — a photo assessment has no equivalent of a
        missing label to key off, so confidence is what communicates
        uncertainty here rather than an absent key.
        """
        ...
