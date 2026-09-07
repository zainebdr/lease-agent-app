"""
The single place that decides mock vs. real. Every service imports
get_lease_extractor() from here — never a concrete class directly — so
this is the only file that needs to know how the decision is made.
"""
from app.ai.base import LeaseExtractor
from app.ai.mock_lease_extractor import MockLeaseExtractor
from app.config import USE_REAL_LLM


def get_lease_extractor() -> LeaseExtractor:
    if USE_REAL_LLM:
        from app.ai.llm_lease_extractor import LLMLeaseExtractor
        return LLMLeaseExtractor()
    return MockLeaseExtractor()
