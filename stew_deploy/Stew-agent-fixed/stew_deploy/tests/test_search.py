"""Tests for web search — anti-hallucination and real API behaviour."""
import pytest
from unittest.mock import patch, MagicMock

from server.search import WebSearch


def test_search_disabled_when_no_api_key():
    """When SERPER_API_KEY is not set, search returns grounded=False and no fake results."""
    searcher = WebSearch.__new__(WebSearch)
    searcher.api_key = ""
    searcher.base_url = "https://google.serper.dev/search"
    searcher.news_url = "https://google.serper.dev/news"

    result = searcher.search("World Cup 2026 winner")
    assert result["grounded"] is False
    assert result["organic"] == []
    assert "error" in result
    # Most importantly — NO fabricated results
    assert len(result["organic"]) == 0


def test_search_returns_real_structure():
    """When API responds, verify structure contains source URLs."""
    fake_response = {
        "organic": [
            {"title": "FIFA World Cup 2026", "link": "https://fifa.com/wc2026", "snippet": "Official site", "position": 1},
            {"title": "World Cup Schedule", "link": "https://espn.com/wc2026", "snippet": "Match schedule", "position": 2},
        ],
        "answerBox": {"answer": "USA, Canada, Mexico"},
    }

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: fake_response,
            raise_for_status=lambda: None,
        )
        searcher = WebSearch.__new__(WebSearch)
        searcher.api_key = "fake-key"
        searcher.base_url = "https://google.serper.dev/search"
        searcher.news_url = "https://google.serper.dev/news"

        result = searcher.search("World Cup 2026")

    assert result["grounded"] is True
    assert len(result["organic"]) == 2
    # Verify source URLs are present
    for item in result["organic"]:
        assert "link" in item
        assert item["link"].startswith("http")
    assert result["answer_box"]["answer"] == "USA, Canada, Mexico"
    assert "timestamp" in result


def test_format_results_for_llm_includes_urls():
    """format_results_for_llm must include source URLs for citation."""
    fake_results = {
        "grounded": True,
        "query": "test query",
        "timestamp": "2026-06-26T12:00:00Z",
        "organic": [
            {"title": "Test Page", "link": "https://example.com/test", "snippet": "A test page"},
        ],
        "answer_box": {},
    }
    searcher = WebSearch.__new__(WebSearch)
    searcher.api_key = "key"
    formatted = searcher.format_results_for_llm(fake_results)

    assert "https://example.com/test" in formatted
    assert "test query" in formatted


def test_format_results_empty_when_not_grounded():
    """format_results_for_llm returns empty string when not grounded — prevents fake citations."""
    searcher = WebSearch.__new__(WebSearch)
    searcher.api_key = ""
    result = searcher.format_results_for_llm({"grounded": False, "query": "test"})
    assert result == ""
