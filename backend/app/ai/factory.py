"""
The single place that decides mock vs. real, for both AI features. Every
service imports get_lease_extractor() / get_image_assessor() from here -
never a concrete class directly - so this is the only file that needs to
know how the decision is made, and it's the same decision (USE_REAL_LLM)
for both Part A and Part B.

This is also the only file that would need a new branch to add another
AI provider alongside Anthropic - see app/config.py's "AI provider"
section for the reasoning, and llm_lease_extractor.py / 
llm_image_assessor.py for the concrete implementation being switched to.
"""
from app.ai.base import LeaseExtractor, ImageAssessor
from app.ai.mock_lease_extractor import MockLeaseExtractor
from app.ai.mock_image_assessor import MockImageAssessor
from app.config import USE_REAL_LLM


def get_lease_extractor() -> LeaseExtractor:
    if USE_REAL_LLM:
        from app.ai.llm_lease_extractor import LLMLeaseExtractor
        return LLMLeaseExtractor()
    return MockLeaseExtractor()


def get_image_assessor() -> ImageAssessor:
    if USE_REAL_LLM:
        from app.ai.llm_image_assessor import LLMImageAssessor
        return LLMImageAssessor()
    return MockImageAssessor()
