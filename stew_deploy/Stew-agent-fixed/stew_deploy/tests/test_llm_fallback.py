"""Tests for LLM fallback logic — verify second provider called when first fails."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

from server.llm_client import LLMClient


def _make_client_with_providers(providers: dict) -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client.providers = providers
    return client


def test_fallback_to_second_provider_when_first_fails():
    """When Groq fails, OpenRouter should be tried."""
    groq_mock = MagicMock()
    groq_mock.chat.completions.create.side_effect = Exception("Groq rate limit")

    openrouter_mock = MagicMock()
    success_response = MagicMock()
    success_response.choices[0].message.content = "Hello from OpenRouter"
    success_response.usage.prompt_tokens = 10
    success_response.usage.completion_tokens = 5
    success_response.usage.total_tokens = 15
    openrouter_mock.chat.completions.create.return_value = success_response

    client = _make_client_with_providers({"groq": groq_mock, "openrouter": openrouter_mock})

    result = client.chat([{"role": "user", "content": "hello"}])
    assert result["content"] == "Hello from OpenRouter"
    assert result["provider"] == "openrouter"
    groq_mock.chat.completions.create.assert_called_once()


def test_fallback_to_third_provider():
    """When Groq and OpenRouter both fail, OpenAI should be tried."""
    groq_mock = MagicMock()
    groq_mock.chat.completions.create.side_effect = Exception("Groq down")

    openrouter_mock = MagicMock()
    openrouter_mock.chat.completions.create.side_effect = Exception("OpenRouter down")

    openai_mock = MagicMock()
    success_response = MagicMock()
    success_response.choices[0].message.content = "Hello from OpenAI"
    success_response.usage.prompt_tokens = 10
    success_response.usage.completion_tokens = 5
    success_response.usage.total_tokens = 15
    openai_mock.chat.completions.create.return_value = success_response

    client = _make_client_with_providers({
        "groq": groq_mock,
        "openrouter": openrouter_mock,
        "openai": openai_mock,
    })

    result = client.chat([{"role": "user", "content": "hello"}])
    assert result["content"] == "Hello from OpenAI"
    assert result["provider"] == "openai"


def test_all_providers_fail_raises_503():
    """When all providers fail, HTTPException 503 should be raised."""
    groq_mock = MagicMock()
    groq_mock.chat.completions.create.side_effect = Exception("Groq down")
    openrouter_mock = MagicMock()
    openrouter_mock.chat.completions.create.side_effect = Exception("OpenRouter down")
    openai_mock = MagicMock()
    openai_mock.chat.completions.create.side_effect = Exception("OpenAI down")

    client = _make_client_with_providers({
        "groq": groq_mock,
        "openrouter": openrouter_mock,
        "openai": openai_mock,
    })

    with pytest.raises(HTTPException) as exc_info:
        client.chat([{"role": "user", "content": "hello"}])

    assert exc_info.value.status_code == 503


def test_first_provider_success_no_fallback():
    """If Groq succeeds, no other provider should be called."""
    groq_mock = MagicMock()
    success_response = MagicMock()
    success_response.choices[0].message.content = "Hello from Groq"
    success_response.usage.prompt_tokens = 10
    success_response.usage.completion_tokens = 5
    success_response.usage.total_tokens = 15
    groq_mock.chat.completions.create.return_value = success_response

    openrouter_mock = MagicMock()
    openai_mock = MagicMock()

    client = _make_client_with_providers({
        "groq": groq_mock,
        "openrouter": openrouter_mock,
        "openai": openai_mock,
    })

    result = client.chat([{"role": "user", "content": "hello"}])
    assert result["provider"] == "groq"
    openrouter_mock.chat.completions.create.assert_not_called()
    openai_mock.chat.completions.create.assert_not_called()


def test_empty_provider_list_raises_503():
    client = _make_client_with_providers({})
    with pytest.raises(HTTPException) as exc_info:
        client.chat([{"role": "user", "content": "test"}])
    assert exc_info.value.status_code == 503
