"""
Real, provider-backed vision implementation of ImageAssessor. Only
instantiated by app/ai/factory.py when a real-AI key is configured -
not required to run the app. Requires: pip install anthropic

This build wires up Anthropic's Claude specifically (same choice as
anthropic_lease_extractor.py, see app/config.py's "AI provider" section for
why), but nothing about the ImageAssessor interface or the factory
ties the app to Anthropic - a different vision provider is a new class
implementing the same interface plus one branch in app/ai/factory.py.

Design: sends the photo as an image content block alongside a prompt
asking for strict JSON (condition, contents, damage_notes, confidence) -
the same traceable-JSON contract used for lease extraction, so both AI
features are auditable the same way.
"""
import base64
import json
import mimetypes

from pydantic import ValidationError

from app.ai.base import PhotoAssessment
from app.ai.schemas import PhotoAssessmentPayload
from app.config import ANTHROPIC_API_KEY

ASSESSMENT_PROMPT = """You are assessing a photo of a rental property unit for a property owner.

Return ONLY a JSON object (no prose, no markdown fences) with this shape:

{
  "condition": "short description, e.g. 'worn, visible water damage near ceiling' or 'new, no visible damage'",
  "contents": ["equipment/fixtures/appliances visible, e.g. AC unit, water heater, stovetop"],
  "damage_notes": "short note on any visible damage and its likely cause, or null if none",
  "confidence": 0.0-1.0
}

Be specific about what's visible. Only note a likely cause for damage
when there's actual visual evidence for it - otherwise just describe
what's seen."""

_MODEL = "claude-sonnet-4-6"


class AnthropicImageAssessor:
    def __init__(self):
        import anthropic  # imported lazily so the package is only required
                           # when this real implementation is actually used
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def assess(self, image_bytes: bytes, filename: str) -> PhotoAssessment:
        media_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_image,
                            },
                        },
                        {"type": "text", "text": ASSESSMENT_PROMPT},
                    ],
                }
            ],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        raw_text = raw_text.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            # Unlike lease extraction (where an unparsed response can just
            # mean "return no fields"), a photo assessment always needs
            # to produce *something* for a human to review - so a parse
            # failure becomes a clearly-labelled, zero-confidence result
            # instead of a crash or a silent empty response.
            return self._unparseable_result()

        try:
            # Valid JSON isn't the same as *correctly-shaped* JSON - e.g.
            # "contents": "AC unit" is valid JSON but would make
            # list(parsed["contents"]) silently iterate the string into
            # ["A", "C", " ", "u", ...] instead of raising. Validating
            # against the shared schema (app/ai/schemas.py) catches that
            # the same way a parse failure is caught, rather than trusting
            # the model's JSON to also be the right shape.
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
        """Shared fallback for a model response that's either not valid
        JSON at all, or valid JSON in the wrong shape - both are equally
        untrustworthy, so both produce the same clearly-labelled,
        zero-confidence result for a human to review instead of a crash
        or silently-wrong data."""
        return PhotoAssessment(
            condition="Could not parse model response - needs manual review.",
            contents=[],
            damage_notes=None,
            confidence=0.0,
            assessed_by=_MODEL,
        )
