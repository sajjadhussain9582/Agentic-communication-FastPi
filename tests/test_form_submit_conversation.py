from tests.conftest import bearer, register_and_login
from app.services.ai_graph import should_include_cta
from app.services.estimator import estimate_project


def _has_cta(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("call", "consult", "meeting", "schedule"))


def _has_qualification_prompt(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("scope", "budget", "timeline", "location", "decision"))


def test_form_submit_placeholder_ai(client):
    _, _, token = register_and_login(client)
    h = bearer(token)

    r = client.get("/api/v1/forms", headers=h)
    assert r.status_code == 200
    form_id = r.json()[0]["id"]

    r = client.post(
        f"/api/v1/forms/{form_id}/submit",
        json={
            "channel": "website",
            "name": "Lead One",
            "email": "lead@example.com",
            "message": "What are your hours?",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "submission_id" in data
    assert "contact_id" in data
    assert "conversation_id" in data
    assert "ai_reply" in data
    assert "rag_kb_ids" in data
    assert "is_escalated" in data
    assert "conversation_status" in data
    assert len(data["ai_reply"]) > 40
    assert _has_cta(data["ai_reply"])
    assert _has_qualification_prompt(data["ai_reply"])
    assert "we do not" not in data["ai_reply"].lower()
    assert "we don't" not in data["ai_reply"].lower()
    assert "contractors" in data["ai_reply"].lower() or "partners" in data["ai_reply"].lower()

    cid = data["conversation_id"]

    r = client.get(f"/api/v1/conversations/{cid}", headers=h)
    assert r.status_code == 200
    conv = r.json()
    assert conv["id"] == cid
    assert len(conv["messages"]) >= 2
    types = {m["sender_type"] for m in conv["messages"]}
    assert "client" in types
    assert "agent" in types


def test_conversation_404(client):
    _, _, token = register_and_login(client)
    r = client.get("/api/v1/conversations/999999", headers=bearer(token))
    assert r.status_code == 404


def test_conversation_unauthorized(client):
    r = client.get("/api/v1/conversations/1")
    assert r.status_code == 401


def test_dynamic_cta_policy():
    assert (
        should_include_cta(
            conversation_turn=1,
            cta_readiness_score=10,
            missing_fields=["scope", "budget"],
            conversation_stage="discovery",
        )
        is True
    )
    assert (
        should_include_cta(
            conversation_turn=3,
            cta_readiness_score=40,
            missing_fields=["scope", "budget", "timeline"],
            conversation_stage="requirements_gathering",
        )
        is False
    )
    assert (
        should_include_cta(
            conversation_turn=3,
            cta_readiness_score=85,
            missing_fields=["constraints"],
            conversation_stage="estimation_ready",
        )
        is True
    )
    assert (
        should_include_cta(
            conversation_turn=4,
            cta_readiness_score=20,
            missing_fields=["budget", "timeline"],
            conversation_stage="proposal_ready",
        )
        is True
    )


def test_rule_based_estimator_ranges():
    out = estimate_project(
        "Need ecommerce clothing platform with checkout and analytics dashboard",
        {"project_type": "ecommerce", "must_have_features": "catalog, checkout, admin dashboard"},
    )
    assert out["mvp_budget_range"]
    assert out["standard_budget_range"]
    assert out["advanced_budget_range"]
    assert "USD" in out["mvp_budget_range"]
    assert "weeks" in out["mvp_timeline"] or "months" in out["mvp_timeline"]
