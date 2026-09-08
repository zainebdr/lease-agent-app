"""
Real, provider-backed vision implementation of ImageAssessor, using
OpenAI instead of Anthropic - see app/ai/anthropic_image_assessor.py for
the Anthropic implementation of the exact same interface. Only
instantiated by app/ai/factory.py when OPENAI_API_KEY is set and
ANTHROPIC_API_KEY isn't (see app/config.py's provider-selection logic).
Requires: pip install openai

Deliberately reuses anthropic_image_assessor.py's ASSESSMENT_PROMPT
rather than duplicating it, and validates the parsed response against
the same app/ai/schemas.py:PhotoAssessmentPayload the Anthropic path
uses - a photo gets the same question asked and the same response shape
enforced no matter which provider answers it. Only the wire format of
the API call itself (and the model's own answer) differs.
"""
import base64
import json
import mimetypes

from pydantic import ValidationError

from app.ai.base import PhotoAssessment
from app.ai.anthropic_image_assessor import ASSESSMENT_PROMPT
from app.ai.schemas import PhotoAssessmentPayload
from app.config import OPENAI_API_KEY

_MODEL = "gpt-4o"


class OpenAIImageAssessor:
    def __init__(self):
        import openai  # imported lazily so the package is only required
                        # when this real implementation is actually used
        self._client = openai.OpenAI(api_key=OPENAI_API_KEY)

    def assess(self, image_bytes: bytes, filename: str) -> PhotoAssessment:
        media_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        response = self._client.chat.completions.create(
            model=_MODEL,
            max_tokens=500,
            # OpenAI can enforce syntactically valid JSON output at the
            # API level - unlike Anthropic, which only takes the prompt's
            # word for it. That's still not the same guarantee as "the
            # right shape" (a well-formed JSON object can still have
            # "contents": "AC unit" instead of a list), so the
            # PhotoAssessmentPayload validation below still runs
            # regardless.
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64_image}"},
                        },
                        {"type": "text", "text": ASSESSMENT_PROMPT},
                    ],
                }
            ],
        )
        raw_text = response.choices[0].message.content or ""

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return self._unparseable_result()

        try:
            payload = PhotoAssessmentPayload.model_validate(parsed)
        except ValidationError:
            return self._unparseable_result()

        return PhotoAssessment(
            condition=payload.condition,
            contents=payload.contents,
            damage_notes=payload.damage_notes,
            confidence=payload.confidence,
            assessed_by=_MODEL,
        )

    @staticmethod
    def _unparseable_result() -> PhotoAssessment:
        """Same reasoning as AnthropicImageAssessor._unparseable_result
        (see app/ai/anthropic_image_assessor.py) - a response that's either not
        valid JSON or valid JSON in the wrong shape is equally
        untrustworthy, so both produce the same clearly-labelled,
        zero-confidence placeholder for a human to review."""
        return PhotoAssessment(
            condition="Could not parse model response - needs manual review.",
            contents=[],
            damage_notes=None,
            confidence=0.0,
            assessed_by=_MODEL,
        )
