"""
Interfaces the rest of the app codes against. Nothing outside app/ai/
ever imports a concrete implementation directly — services get whatever
app/ai/factory.py hands them, so swapping mock <-> real LLM is a config
change, not a code change.
"""
from typing import Protocol, Any


class ExtractedField:
    """A single extracted value with provenance and a confidence score."""

    def __init__(self, value: Any, source_span: str | None, confidence: float):
        self.value = value
        self.source_span = source_span
        self.confidence = confidence

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source_span": self.source_span,
            "confidence": self.confidence,
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
