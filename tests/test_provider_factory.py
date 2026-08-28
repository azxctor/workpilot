import pytest

from workpilot.config import build_provider


def test_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("WORKPILOT_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    provider = build_provider()

    assert type(provider).__name__ == "AnthropicProvider"


def test_openai_provider_reads_model_and_base_url_from_env(monkeypatch):
    monkeypatch.setenv("WORKPILOT_PROVIDER", "openai")
    monkeypatch.setenv("WORKPILOT_MODEL", "kimi-k2")
    monkeypatch.setenv("WORKPILOT_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("WORKPILOT_API_KEY", "sk-x")

    provider = build_provider()

    assert type(provider).__name__ == "OpenAIProvider"
    assert provider.model == "kimi-k2"
    assert str(provider.client.base_url).startswith("https://api.example.com")


def test_openai_provider_requires_a_model(monkeypatch):
    monkeypatch.setenv("WORKPILOT_PROVIDER", "openai")
    monkeypatch.delenv("WORKPILOT_MODEL", raising=False)
    monkeypatch.setenv("WORKPILOT_API_KEY", "sk-x")

    with pytest.raises(ValueError, match="WORKPILOT_MODEL"):
        build_provider()


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("WORKPILOT_PROVIDER", "gemini")

    with pytest.raises(ValueError, match="gemini"):
        build_provider()
