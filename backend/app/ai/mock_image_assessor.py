"""
Deterministic stand-in for a real vision-model call.

This is fundamentally different from MockLeaseExtractor: that one does
genuine work (regex/label parsing against real document text). There is
no rule-based equivalent for "look at this photo and assess its
condition" - you either run a real vision model or you don't. What this
mock can honestly demonstrate is the *pipeline*: a structured assessment
(condition, contents/equipment, damage notes, confidence) with a fixed,
always-available shape, chosen deterministically from the photo's own
bytes so the same upload always produces the same result and the whole
issue-reporting flow (assess -> aggregate -> draft work order -> human
review) can be built and tested without any external service or API
cost. Confidence is deliberately capped at a modest value to signal
"structural stand-in", not a real read of the image. Swapping in
app/ai/anthropic_image_assessor.py (a real Claude vision call) or
app/ai/openai_image_assessor.py (a real GPT-4o vision call) is what
turns this from a shape demo into an actual assessment of the photo -
same interface, one env var, no other code changes.
"""
import hashlib

from app.ai.base import PhotoAssessment

# A handful of plausible, varied property-issue scenarios. Selection is
# deterministic (hashed from the image bytes), not random, so re-running
# against the same photo always gives the same mock result.
_SCENARIOS = [
    {
        "condition": "worn, visible water staining near the ceiling",
        "contents": ["AC unit", "ceiling light fixture"],
        "damage_notes": "Staining pattern is consistent with a slow leak from "
                         "the AC drain line or the roof above.",
    },
    {
        "condition": "new, no visible damage",
        "contents": ["kitchen cabinets", "stovetop", "refrigerator"],
        "damage_notes": None,
    },
    {
        "condition": "worn, cracked floor tile near the doorway",
        "contents": ["floor tiling", "baseboard"],
        "damage_notes": "Crack pattern suggests impact damage rather than "
                         "gradual wear.",
    },
    {
        "condition": "old, corroded fixture",
        "contents": ["water heater", "shut-off valve"],
        "damage_notes": "Visible corrosion around the water heater's base "
                         "connections.",
    },
    {
        "condition": "new, minor cosmetic wear only",
        "contents": ["bathroom vanity", "mirror", "light fixture"],
        "damage_notes": None,
    },
]


class MockImageAssessor:
    def assess(self, image_bytes: bytes, filename: str) -> PhotoAssessment:
        digest = hashlib.sha256(image_bytes or filename.encode("utf-8")).digest()
        scenario = _SCENARIOS[digest[0] % len(_SCENARIOS)]
        return PhotoAssessment(
            condition=scenario["condition"],
            contents=list(scenario["contents"]),
            damage_notes=scenario["damage_notes"],
            confidence=0.55,
            assessed_by="mock",
        )
