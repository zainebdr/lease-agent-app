"""
Real, provider-backed implementation of LeaseExtractor. Only
instantiated by app/ai/factory.py when a real-AI key is configured -
not required to run the app. Requires: pip install anthropic

This build wires up Anthropic's Claude specifically, but nothing about
the LeaseExtractor interface or the factory ties the app to Anthropic -
a different provider is a new class implementing the same interface
plus one branch in app/ai/factory.py, not a change to any caller. See
app/config.py's "AI provider" section for why Anthropic is the one
implemented here.

Design: asks the model to return strict JSON with a value + a short
verbatim source quote per field, which we store as `source_span`. This
keeps the "traceable" requirement true for the real-model path too, not
just the mock.
"""
import json

from pydantic import ValidationError

from app.ai.base import ExtractedField
from app.ai.schemas import ExtractedFieldPayload
from app.config import ANTHROPIC_API_KEY

EXTRACTION_PROMPT = """You are extracting structured data from a lease document.
Return ONLY a JSON object (no prose, no markdown fences) with this shape:

{{
  "landlord_name": {{"value": "...", "source_span": "short verbatim quote", "confidence": 0.0-1.0}},
  "tenant_name": {{...}},
  "unit_reference_text": {{"value": "however the unit/apartment is identified in the document, e.g. 'Unit 1204' or 'Apt B-1204'", ...}},
  "commencement_date": {{"value": "YYYY-MM-DD", ...}},
  "expiry_date": {{"value": "YYYY-MM-DD", ...}},
  "term_months": {{"value": 12, ...}},
  "monthly_rent": {{"value": 8500, ...}},
  "annual_rent": {{"value": 102000, ...}},
  "deposit_amount": {{"value": 8500, ...}},
  "escalation_clause_text": {{"value": "...", ...}},
  "escalation_is_defined": {{"value": true, ...}},
  "renewal_terms_text": {{"value": "how the lease may be renewed, e.g. 'automatic renewal unless either party gives 60 days notice'", ...}},
  "termination_terms_text": {{"value": "conditions/notice required to terminate early, e.g. '90 days written notice, penalty of 2 months rent'", ...}},
  "landlord_signed": {{"value": true, ...}},
  "tenant_signed": {{"value": true, ...}}
}}

Omit any key you cannot find with reasonable confidence — do not guess.

LEASE DOCUMENT:
---
{document_text}
---
"""

# Every key the prompt above can return. Anything else in the parsed JSON
# (the model inventing a field, or renaming one) is dropped rather than
# passed through - app/services/unit_matching.py and the rule engine only
# ever look up known keys, so an extra key would just be dead weight, but
# a *misspelled* known key (e.g. "unit_reference" instead of
# "unit_reference_text") failing silently instead of loudly is exactly the
# kind of thing this allowlist is meant to catch during development.
KNOWN_FIELD_NAMES = frozenset(
    {
        "landlord_name",
        "tenant_name",
        "unit_reference_text",
        "commencement_date",
        "expiry_date",
        "term_months",
        "monthly_rent",
        "annual_rent",
        "deposit_amount",
        "escalation_clause_text",
        "escalation_is_defined",
        "renewal_terms_text",
        "termination_terms_text",
        "landlord_signed",
        "tenant_signed",
    }
)


class AnthropicLeaseExtractor:
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
            if key not in KNOWN_FIELD_NAMES:
                continue
            try:
                # Same reasoning as the image assessor: valid JSON doesn't
                # mean correctly-shaped JSON - a missing "value" key or a
                # non-numeric/out-of-range "confidence" is just as
                # untrustworthy as the whole response failing to parse, so
                # it's dropped the same way rather than crashing this loop
                # (float(entry.get("confidence")) would raise on a string
                # like "high") or silently storing a bad confidence.
                payload = ExtractedFieldPayload.model_validate(entry)
            except ValidationError:
                continue
            fields[key] = ExtractedField(
                value=payload.value,
                source_span=payload.source_span,
                confidence=payload.confidence,
            )
        return fields
