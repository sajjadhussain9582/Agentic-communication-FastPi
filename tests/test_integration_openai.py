"""Optional: run with OPENAI_API_KEY set and pytest -m integration."""

import os

import pytest

from tests.conftest import bearer, register_and_login

pytestmark = pytest.mark.integration


def _has_cta(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("call", "consult", "meeting", "schedule"))


def _has_qualification_prompt(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("scope", "budget", "timeline", "location", "decision"))


def _has_budget_timeline_range(text: str) -> bool:
    lower = text.lower()
    return (
        ("mvp" in lower and "standard" in lower and "advanced" in lower)
        or ("usd" in lower and ("week" in lower or "month" in lower))
    )


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_form_submit_with_openai(client):
    _, _, token = register_and_login(client)
    h = bearer(token)
    fid = client.get("/api/v1/forms", headers=h).json()[0]["id"]
    r = client.post(
        f"/api/v1/forms/{fid}/submit",
        json={
            "channel": "email",
            "name": "RAG Test",
            "email": "rag@example.com",
            "message": "What are your business hours?",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["conversation_id"]
    assert len(data["ai_reply"]) > 50
    assert _has_cta(data["ai_reply"])
    assert _has_qualification_prompt(data["ai_reply"])
    assert "we do not" not in data["ai_reply"].lower()
    assert "we don't" not in data["ai_reply"].lower()
    assert "contractors" in data["ai_reply"].lower() or "partners" in data["ai_reply"].lower()


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_budget_question_gets_concrete_ranges(client):
    _, _, token = register_and_login(client)
    h = bearer(token)
    fid = client.get("/api/v1/forms", headers=h).json()[0]["id"]
    r = client.post(
        f"/api/v1/forms/{fid}/submit",
        json={
            "channel": "email",
            "name": "Estimate Test",
            "email": "estimate@example.com",
            "message": "We need an ecommerce clothing website. What budget and timeline should we expect?",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["ai_reply"]) > 50
    assert _has_budget_timeline_range(data["ai_reply"])
