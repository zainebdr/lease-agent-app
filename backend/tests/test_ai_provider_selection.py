"""
Tests for the mock/Anthropic/OpenAI provider selection - app/config.py's
AI_PROVIDER resolution (whichever API key is actually set in the
environment) and app/ai/factory.py's routing based on it. This is what
lets a reviewer use whichever key they already have (or neither) with
zero code changes - see the README's "AI: mock vs. real, and picking a
provider" section.

None of this needs the real anthropic/openai packages installed, a real
key, or network access - same constraint as the rest of the suite (see
conftest.py). The factory tests register minimal fake stand-ins for
those two packages (just enough to satisfy `import anthropic` /
`import openai` and construct a client object) so
AnthropicLeaseExtractor / OpenAILeaseExtractor etc. can be
*instantiated* to prove routing picked the right class - their
assess()/extract() methods (the actual API call) are never invoked here.
"""
import contextlib
import importlib
import sys
import types

import app.config as config


# --- app/config.py: AI_PROVIDER resolution from environment variables ------

@contextlib.contextmanager
def _temp_env_and_reload(monkeypatch, **env):
    """Sets exactly the given env vars (clearing both key vars first),
    reloads app.config so AI_PROVIDER/USE_REAL_LLM re-resolve against
    them, yields the reloaded module, then always restores the original
    environment and reloads again - so this module's real state (as
    determined by whatever's actually in the environment running
    pytest) is never left mutated for tests that run after this file."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config)
    try:
        yield config
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_no_keys_set_means_no_real_provider(monkeypatch):
    with _temp_env_and_reload(monkeypatch) as cfg:
        assert cfg.AI_PROVIDER is None
        assert cfg.USE_REAL_LLM is False


def test_anthropic_key_alone_selects_anthropic(monkeypatch):
    with _temp_env_and_reload(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test") as cfg:
        assert cfg.AI_PROVIDER == "anthropic"
        assert cfg.USE_REAL_LLM is True


def test_openai_key_alone_selects_openai(monkeypatch):
    with _temp_env_and_reload(monkeypatch, OPENAI_API_KEY="sk-test") as cfg:
        assert cfg.AI_PROVIDER == "openai"
        assert cfg.USE_REAL_LLM is True


def test_both_keys_set_anthropic_takes_precedence(monkeypatch):
    # An arbitrary but fixed tie-break (see app/config.py's comment) -
    # only relevant if someone sets both, which no normal run would.
    with _temp_env_and_reload(
        monkeypatch, ANTHROPIC_API_KEY="sk-ant-test", OPENAI_API_KEY="sk-test"
    ) as cfg:
        assert cfg.AI_PROVIDER == "anthropic"


# --- app/ai/factory.py: routing to the correct concrete implementation -----

@contextlib.contextmanager
def _fake_provider_sdks():
    """Registers no-op stand-ins for the 'anthropic' and 'openai' PyPI
    packages, just enough surface (a top-level client class taking
    api_key=...) for AnthropicLeaseExtractor/AnthropicImageAssessor/
    OpenAILeaseExtractor/OpenAIImageAssessor's __init__ to succeed
    without either package actually being installed. No API call is
    ever made through them in these tests."""
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = lambda api_key=None: object()
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda api_key=None: object()

    had_anthropic = "anthropic" in sys.modules
    had_openai = "openai" in sys.modules
    old_anthropic = sys.modules.get("anthropic")
    old_openai = sys.modules.get("openai")
    sys.modules["anthropic"] = fake_anthropic
    sys.modules["openai"] = fake_openai
    try:
        yield
    finally:
        if had_anthropic:
            sys.modules["anthropic"] = old_anthropic
        else:
            sys.modules.pop("anthropic", None)
        if had_openai:
            sys.modules["openai"] = old_openai
        else:
            sys.modules.pop("openai", None)


def test_no_provider_configured_returns_mocks(monkeypatch):
    monkeypatch.setattr("app.ai.factory.AI_PROVIDER", None)
    from app.ai.factory import get_image_assessor, get_lease_extractor

    assert type(get_lease_extractor()).__name__ == "MockLeaseExtractor"
    assert type(get_image_assessor()).__name__ == "MockImageAssessor"


def test_anthropic_provider_returns_anthropic_implementations(monkeypatch):
    monkeypatch.setattr("app.ai.factory.AI_PROVIDER", "anthropic")
    from app.ai.factory import get_image_assessor, get_lease_extractor

    with _fake_provider_sdks():
        assert type(get_lease_extractor()).__name__ == "AnthropicLeaseExtractor"
        assert type(get_image_assessor()).__name__ == "AnthropicImageAssessor"


def test_openai_provider_returns_openai_implementations(monkeypatch):
    monkeypatch.setattr("app.ai.factory.AI_PROVIDER", "openai")
    from app.ai.factory import get_image_assessor, get_lease_extractor

    with _fake_provider_sdks():
        assert type(get_lease_extractor()).__name__ == "OpenAILeaseExtractor"
        assert type(get_image_assessor()).__name__ == "OpenAIImageAssessor"
