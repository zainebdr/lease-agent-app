"""
Real LLM-backed implementation of LeaseExtractor. Only instantiated by
app/ai/factory.py when ANTHROPIC_API_KEY is set — not required to run
the app. Requires: pip install anthropic

Design: asks the model to return strict JSON with a value + a short
verbatim source quote per field, which we store as `source_span`. This
keeps the "traceable" requirement true for the real-model path too, not
just the mock.
"""
import json

from app.ai.base import ExtractedField
from app.config import ANTHROPIC_API_KEY

EXTRACTION_PROMPT = """You are extracting structured data from a lease document.
Return ONLY a JSON object (no prose, no markdown fences) with this shape:

{{
  "landlord_name": {{"value": "...", "source_span": "short verbatim quote", "confidence": 0.0-1.0}},
  "tenant_name": {{...}},
  "commencement_date": {{"value": "YYYY-MM-DD", ...}},
  "expiry_date": {{"value": "YYYY-MM-DD", ...}},
  "term_months": {{"value": 12, ...}},
  "monthly_rent": {{"value": 8500, ...}},
  "annual_rent": {{"value": 102000, ...}},
  "deposit_amount": {{"value": 8500, ...}},
  "escalation_clause_text": {{"value": "...", ...}},
  "escalation_is_defined": {{"value": true, ...}},
  "landlord_signed": {{"value": true, ...}},
  "tenant_signed": {{"value": true, ...}}
}}

Omit any key you cannot find with reasonable confidence — do not guess.

LEASE DOCUMENT:
---
{document_text}
---
"""


class LLMLeaseExtractor:
    def __init__(self):
        import anthropic  # imported lazily so the package is only required
                           # when this real implementation is actually used
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract(self, document_text: str) -> dict[str, ExtractedField]:
        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT.format(document_text=document_text)}
            ],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        raw_text = raw_text.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return {}

        fields = {}
        for key, entry in parsed.items():
            if isinstance(entry, dict) and "value" in entry:
                fields[key] = ExtractedField(
                    value=entry["value"],
                    source_span=entry.get("source_span"),
                    confidence=float(entry.get("confidence", 0.5)),
                )
        return fields
