"""
Real, provider-backed vision implementation of ImageAssessor. Only
instantiated by app/ai/factory.py when a real-AI key is configured -
not required to run the app. Requires: pip install anthropic

This build wires up Anthropic's Claude specifically (same choice as
llm_lease_extractor.py, see app/config.py's "AI provider" section for
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

from app.ai.base import PhotoAssessment
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


class LLMImageAssessor:
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
            return PhotoAssessment(
                condition="Could not parse model response - needs manual review.",
                contents=[],
                damage_notes=None,
                confidence=0.0,
                assessed_by=_MODEL,
            )

        return PhotoAssessment(
            condition=parsed.get("condition", "unknown"),
            contents=list(parsed.get("contents", [])),
            damage_notes=parsed.get("damage_notes"),
            confidence=float(parsed.get("confidence", 0.5)),
            assessed_by=_MODEL,
        )
