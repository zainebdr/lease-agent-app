"""
The single place that decides which AI provider backs each feature.
Every service imports get_lease_extractor() / get_image_assessor() from
here - never a concrete class directly - so this is the only file that
needs to know how the decision is made, and it's the same decision
(AI_PROVIDER) for both Part A and Part B.

This is also the only file that would need a new branch to add yet
another AI provider alongside Anthropic/OpenAI - see app/config.py's "AI
provider" section for the selection logic and reasoning, and
anthropic_lease_extractor.py / anthropic_image_assessor.py (Anthropic) or
openai_lease_extractor.py / openai_image_assessor.py (OpenAI) for the
concrete implementations being switched between.
"""
from app.ai.base import LeaseExtractor, ImageAssessor
from app.ai.mock_lease_extractor import MockLeaseExtractor
from app.ai.mock_image_assessor import MockImageAssessor
from app.config import AI_PROVIDER


def get_lease_extractor() -> LeaseExtractor:
    if AI_PROVIDER == "anthropic":
        from app.ai.anthropic_lease_extractor import AnthropicLeaseExtractor
        return AnthropicLeaseExtractor()
    if AI_PROVIDER == "openai":
        from app.ai.openai_lease_extractor import OpenAILeaseExtractor
        return OpenAILeaseExtractor()
    return MockLeaseExtractor()


def get_image_assessor() -> ImageAssessor:
    if AI_PROVIDER == "anthropic":
        from app.ai.anthropic_image_assessor import AnthropicImageAssessor
        return AnthropicImageAssessor()
    if AI_PROVIDER == "openai":
        from app.ai.openai_image_assessor import OpenAIImageAssessor
        return OpenAIImageAssessor()
    return MockImageAssessor()
