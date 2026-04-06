import pytest
import os
from sqlmodel import Session, select
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.contact import Contact
from app.services.ai_graph import run_ai_pipeline
from tests.conftest import bearer, register_and_login

from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage
from app.services.ai_graph import MessageClassification

from app.core.database import engine

os.environ["GROQ_API_KEY"] = "groq-dummy"

def test_escalation_brief_generation(client):
    # 1. Setup: Create a contact and conversation
    with Session(engine) as session:
        _, _, token = register_and_login(client)
        h = bearer(token)
    
    # Create a form and submit to get a conversation
    r = client.get("/api/v1/forms", headers=h)
    form_id = r.json()[0]["id"]
    
    # Mock the LLM to ensure it triggers escalation
    mock_classification = MessageClassification(
        role_type="developer",
        intent_type="service_inquiry",
        relationship_type="inbound_lead",
        decision_role="decision_maker",
        engagement_temperature="hot",
        qualification_stage="qualified",
        is_escalated=True, # TRIGGER ESCALATION
        conversation_status="pending_human",
        lead_score=85.0,
        is_qualified=True,
        budget="$50k",
        project_type="Residential",
        timeline="3 months",
        project_scope="Full build",
        decision_authority="CEO",
        geography="London",
        new_pipeline_stage="qualified",
        close_readiness_score=70.0,
        requires_action=["update_status"],
        missing_qualification_fields=[],
        next_best_questions=[]
    )
    
    mock_brief_content = """---

Lead Summary:
- Name: Test Lead
- Role / Persona: developer
- Relationship Type: inbound_lead
- Decision Role: decision_maker
- Engagement Level: hot

---

Project / Requirement:
- Project Type: Residential
- Scope: Full build
- Budget: $50k
- Timeline: 3 months
- Location: London

---

Qualification Status:
- Current Stage: qualified
- Qualification Status: qualified
- Lead Score: 85.0
- Close Readiness: 70.0

---

Key Signals:
- Buying Intent: High intent, clear budget and timeline.
- Urgency Level: high
- Constraints or Risks: None specified.

---

Conversation Highlights:
- User asked for a complex lawful answer about real estate regulations.
- User requested immediate escalation to a human manager.

---

Missing Information:
- None

---

Recommended Next Action:
- Escalate to senior team for legal review.

---

AI Notes (Internal):
- Escalation triggered due to complex legal inquiry.
"""

    with patch("app.services.ai_graph.ChatGroq") as MockLLM, patch("app.services.ai_graph.search_similar") as mock_search:
        mock_llm_instance = MockLLM.return_value
        mock_search.return_value = ([], [])
        
        # Mock for classification node
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
        
        # Mock for brief generation in human_review node
        mock_llm_instance.invoke.return_value = AIMessage(content=mock_brief_content)

        r = client.post(
            f"/api/v1/forms/{form_id}/submit",
            json={"channel": "email", "name": "Test Lead", "email": "test@example.com", "message": "I need a complex lawful answer about real estate regulations."},
        )
        assert r.status_code == 200, r.text
        conv_uuid = r.json()["conversation_uuid"]
        
        # 2. Trigger escalation via inbound
        r = client.post(
            f"/api/v1/conversations/{conv_uuid}/inbound",
            json={"message": "Please escalate this to a human manager immediately. I have a legal question about the construction permits."},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_escalated"] is True
        
    # 3. Verify the brief in the database
    conv = session.exec(select(Conversation).where(Conversation.public_uuid == conv_uuid)).first()
    assert conv is not None
    assert conv.is_escalated is True
    assert conv.escalation_brief is not None
    assert "Lead Summary" in conv.escalation_brief
    assert "developer" in conv.escalation_brief
    
    # 4. Verify the system message
    msgs = session.exec(select(Message).where(Message.conversation_id == conv.id).where(Message.message_type == "brief")).all()
    assert len(msgs) > 0
    assert "INTERNAL ESCALATION BRIEF" in msgs[0].message
    
    # 5. Verify the API returns the brief
    r = client.get(f"/api/v1/conversations/{conv_uuid}", headers=h)
    assert r.status_code == 200
    assert r.json()["is_escalated"] is True
    assert r.json()["escalation_brief"] == conv.escalation_brief
    
    print("Verification successful!")
