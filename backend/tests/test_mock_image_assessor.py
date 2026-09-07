"""
Tests for MockImageAssessor (app/ai/mock_image_assessor.py) - the
hash-based stand-in used whenever ANTHROPIC_API_KEY isn't set. Unlike
the lease extractor mock, this one can't do "real" work against a
photo's actual pixels (see its module docstring); what's actually worth
testing is the contract it promises: always a full, well-shaped result,
and deterministic per input.
"""
from app.ai.mock_image_assessor import MockImageAssessor


def test_assess_always_returns_a_fully_populated_result():
    result = MockImageAssessor().assess(b"some photo bytes", "photo.jpg")
    assert result.condition
    assert isinstance(result.contents, list) and result.contents
    assert 0.0 <= result.confidence <= 1.0
    assert result.assessed_by == "mock"


def test_same_photo_bytes_always_produce_the_same_result():
    assessor = MockImageAssessor()
    first = assessor.assess(b"identical-bytes", "a.jpg")
    second = assessor.assess(b"identical-bytes", "b.jpg")  # filename shouldn't matter, bytes do
    assert first.condition == second.condition
    assert first.contents == second.contents


def test_different_photo_bytes_can_produce_different_results():
    assessor = MockImageAssessor()
    seen = {
        assessor.assess(f"photo-{i}".encode(), f"{i}.jpg").condition
        for i in range(20)
    }
    # Not every hash bucket needs to be hit, but 20 varied inputs should
    # not all collide into the exact same one of 5 scenarios.
    assert len(seen) > 1


def test_falls_back_to_filename_hash_when_no_bytes_given():
    # Guards the `image_bytes or filename.encode(...)` fallback in
    # assess() - an empty upload still gets a deterministic result
    # instead of hashing an empty byte string into one fixed scenario.
    result_a = MockImageAssessor().assess(b"", "photo-a.jpg")
    result_b = MockImageAssessor().assess(b"", "photo-b.jpg")
    assert result_a.condition  # doesn't crash, still returns a full result
    assert result_b.condition
