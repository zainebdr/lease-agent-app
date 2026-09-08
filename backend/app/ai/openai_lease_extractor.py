"""
Real, provider-backed implementation of LeaseExtractor, using OpenAI
instead of Anthropic - see app/ai/anthropic_lease_extractor.py for the
Anthropic implementation of the exact same interface. Only instantiated
by app/ai/factory.py when OPENAI_API_KEY is set and ANTHROPIC_API_KEY
isn't (see app/config.py's provider-selection logic). Requires:
pip install openai

Deliberately reuses anthropic_lease_extractor.py's EXTRACTION_PROMPT and
KNOWN_FIELD_NAMES allowlist rather than duplicating them, and validates
each parsed field against the same app/ai/schemas.py:ExtractedFieldPayload
the Anthropic path uses - a lease document gets the same fields asked
for and the same per-field envelope shape enforced no matter which
provider answers. Only the wire format of the API call itself (and the
model's own answer) differs.
"""
import json

from pydantic import ValidationError

from app.ai.base import ExtractedField
from app.ai.anthropic_lease_extractor import EXTRACTION_PROMPT, KNOWN_FIELD_NAMES
from app.ai.schemas import ExtractedFieldPayload
from app.config import OPENAI_API_KEY

_MODEL = "gpt-4o"


class OpenAILeaseExtractor:
    def __init__(self):
        import openai  # imported lazily so the package is only required
                        # when this real implementation is actually used
        self._client = openai.OpenAI(api_key=OPENAI_API_KEY)

    def extract(self, document_text: str) -> dict[str, ExtractedField]:
        response = self._client.chat.completions.create(
            model=_MODEL,
            max_tokens=1500,
            # See app/ai/openai_image_assessor.py's comment on this same
            # option - it guarantees syntactically valid JSON, not the
            # right shape, so per-field validation below still runs.
            response_format={"type": "json_object"},
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT.format(document_text=document_text)}
            ],
        )
        raw_text = response.choices[0].message.content or ""

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return {}

        fields = {}
        for key, entry in parsed.items():
            if key not in KNOWN_FIELD_NAMES:
                continue
            try:
                payload = ExtractedFieldPayload.model_validate(entry)
            except ValidationError:
                continue
            fields[key] = ExtractedField(
                value=payload.value,
                source_span=payload.source_span,
                confidence=payload.confidence,
            )
        return fields
