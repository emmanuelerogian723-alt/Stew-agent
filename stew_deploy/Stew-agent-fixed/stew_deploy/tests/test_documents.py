"""Tests for document generation endpoints — verify base64 decodes to valid files."""
import base64
import io
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_generate_pdf(client: AsyncClient, registered_user):
    resp = await client.post("/generate/pdf", json={
        "content": "# Test Report\n\nThis is a test.\n\n## Section 1\n\nSome content here.",
        "title": "Test PDF",
        "api_key": registered_user["api_key"],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["mime_type"] == "application/pdf"
    assert data["filename"].endswith(".pdf")

    # Verify base64 decodes to a valid PDF (starts with %PDF)
    raw = base64.b64decode(data["file"])
    assert raw[:4] == b"%PDF", "Not a valid PDF file"
    assert len(raw) > 100


@pytest.mark.asyncio
async def test_generate_docx(client: AsyncClient, registered_user):
    resp = await client.post("/generate/docx", json={
        "content": "# Document Title\n\n## Intro\n\nHello world.\n\n- Item 1\n- Item 2",
        "title": "Test DOCX",
        "api_key": registered_user["api_key"],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert "wordprocessingml" in data["mime_type"]
    assert data["filename"].endswith(".docx")

    # DOCX is a ZIP file — starts with PK magic bytes
    raw = base64.b64decode(data["file"])
    assert raw[:2] == b"PK", "Not a valid DOCX/ZIP file"
    assert len(raw) > 100


@pytest.mark.asyncio
async def test_generate_xlsx(client: AsyncClient, registered_user):
    resp = await client.post("/generate/xlsx", json={
        "data": [
            {"Name": "Alice", "Score": 95, "Grade": "A"},
            {"Name": "Bob", "Score": 82, "Grade": "B"},
            {"Name": "Charlie", "Score": 74, "Grade": "C"},
        ],
        "sheet_name": "Results",
        "title": "Test Spreadsheet",
        "api_key": registered_user["api_key"],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert "spreadsheetml" in data["mime_type"]
    assert data["filename"].endswith(".xlsx")

    # XLSX is also a ZIP
    raw = base64.b64decode(data["file"])
    assert raw[:2] == b"PK"
    assert len(raw) > 100


@pytest.mark.asyncio
async def test_generate_pptx(client: AsyncClient, registered_user):
    resp = await client.post("/generate/pptx", json={
        "slides": [
            {"title": "Welcome", "content": "This is slide 1"},
            {"title": "Agenda", "content": "- Point 1\n- Point 2\n- Point 3"},
            {"title": "Conclusion", "content": "Thanks for watching"},
        ],
        "title": "Test Presentation",
        "api_key": registered_user["api_key"],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert "presentationml" in data["mime_type"]
    assert data["filename"].endswith(".pptx")

    raw = base64.b64decode(data["file"])
    assert raw[:2] == b"PK"
    assert len(raw) > 100


@pytest.mark.asyncio
async def test_generate_html(client: AsyncClient, registered_user):
    resp = await client.post("/generate/html", json={
        "content": "# My Report\n\n## Overview\n\nThis is a test.\n\n- Point A\n- Point B",
        "title": "Test HTML Report",
        "api_key": registered_user["api_key"],
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["mime_type"] == "text/html"
    assert data["filename"].endswith(".html")

    raw = base64.b64decode(data["file"]).decode("utf-8")
    assert "<!DOCTYPE html>" in raw
    assert "Test HTML Report" in raw
    assert "S.T.E.W" in raw


@pytest.mark.asyncio
async def test_generate_with_invalid_api_key(client: AsyncClient):
    resp = await client.post("/generate/pdf", json={
        "content": "test",
        "title": "test",
        "api_key": "stew_invalidkeyhere123",
    })
    assert resp.status_code == 401
