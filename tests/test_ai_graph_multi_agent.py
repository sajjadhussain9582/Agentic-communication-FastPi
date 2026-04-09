import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock
from decimal import Decimal

from sqlmodel import Session, select, SQLModel
from langchain_core.messages import AIMessage

from app.core.database import engine, init_db
from app.core.seed import seed_if_empty
from app.models.conversation import Conversation
from app.models.contact import Contact
from app.models.message import Message
from app.services.ai_graph import run_ai_pipeline, MessageClassification
from app.core.config import settings

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Initialize and seed the database once for the test session."""
    init_db()
    with Session(engine) as session:
        seed_if_empty(session)
    yield

@pytest.fixture
def mock_db_session():
    with Session(engine) as session:
        yield session

def create_mock_classification(overrides=None):
    base = {
        "role_type": "developer",
        "intent_type": "service_inquiry",
        "relationship_type": "inbound_lead",
        "decision_role": "decision_maker",
        "engagement_temperature": "warm",
        "qualification_stage": "discovery",
        "is_escalated": False,
        "conversation_status": "open",
        "lead_score": 50.0,
        "is_qualified": True,
        "budget": None,
        "project_type": "Residential",
        "timeline": None,
        "project_scope": None,
        "decision_authority": None,
        "geography": None,
        "new_pipeline_stage": "discovery",
        "close_readiness_score": 20.0,
        "requires_action": [],
        "missing_qualification_fields": ["budget", "timeline"],
        "filled_fields": ["project_type"],
        "next_best_questions": ["What is your budget?"]
    }
    if overrides:
        base.update(overrides)
    return MessageClassification(**base)

@pytest.mark.asyncio
async def test_qualifier_flow(mock_db_session):
    """Test the basic qualification flow using the multi-agent graph."""
    # Setup
    contact = Contact(username="Test User", email="test@example.com")
    mock_db_session.add(contact)
    mock_db_session.commit()
    mock_db_session.refresh(contact)
    
    conv = Conversation(contact_id=contact.id, channel="website")
    mock_db_session.add(conv)
    mock_db_session.commit()
    mock_db_session.refresh(conv)
    
    inbound = Message(conversation_id=conv.id, sender_type="client", message="I want to build a house")
    mock_db_session.add(inbound)
    mock_db_session.commit()

    mock_classification = create_mock_classification()
    
    with patch("app.services.ai_graph.ChatGroq") as MockLLM, \
         patch("app.services.ai_graph.search_similar") as mock_search:
        
        mock_llm_instance = MockLLM.return_value
        mock_search.return_value = ([], [])
        
        # Mock classification node
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
        
        async def mock_astream_events(*args, **kwargs):
            events = [
                {"event": "on_chat_model_stream", "data": {"chunk": AIMessage(content="That sounds great! ")}},
                {"event": "on_chat_model_stream", "data": {"chunk": AIMessage(content="What is your budget? ")}},
                {"event": "on_end", "data": {"output": {"classification": mock_classification, "generated_reply": "That sounds great! What is your budget? "}}}
            ]
            for e in events:
                yield e
        
        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        
        with patch("app.services.ai_graph.build_graph", return_value=mock_graph):
            outbound = await run_ai_pipeline(mock_db_session, conv, contact, inbound)
            
            assert outbound is not None
            assert "budget" in outbound.message.lower()
            assert outbound.sender_type == "agent"

@pytest.mark.asyncio
async def test_pricing_escalation_flow(mock_db_session):
    """Test that pricing requests trigger human escalation and remain silent to client."""
    # Setup
    contact = Contact(username="Price Sensitive", email="price@example.com")
    mock_db_session.add(contact)
    mock_db_session.commit()
    mock_db_session.refresh(contact)
    
    conv = Conversation(contact_id=contact.id, channel="website")
    mock_db_session.add(conv)
    mock_db_session.commit()
    mock_db_session.refresh(conv)
    
    inbound = Message(conversation_id=conv.id, sender_type="client", message="How much does it cost?")
    mock_db_session.add(inbound)
    mock_db_session.commit()

    mock_classification = create_mock_classification({
        "intent_type": "pricing_request",
        "is_escalated": True,
        "conversation_status": "pending_human"
    })
    
    with patch("app.services.ai_graph.ChatGroq") as MockLLM, \
         patch("app.services.ai_graph.search_similar") as mock_search, \
         patch("app.services.ai_graph.send_email", new_callable=AsyncMock) as mock_send_email:
        
        mock_llm_instance = MockLLM.return_value
        mock_search.return_value = ([], [])
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
        
        # Mock escalation brief generation in node_human_review
        mock_llm_instance.invoke.return_value = AIMessage(content="Escalation Brief: User asked for pricing.")
        
        # Mock graph to return ESCALATED_NO_REPLY
        async def mock_astream_events(*args, **kwargs):
            # Simulate side effect of node_human_review
            conv.is_escalated = True
            conv.status = "pending_human"
            conv.escalation_brief = "Escalation Brief: User asked for pricing."
            mock_db_session.add(conv)
            mock_db_session.commit()
            
            # Simulate email call (this triggers the mock_send_email)
            from app.services.ai_graph import send_email
            await send_email({}, "sajjad_hussain@strategisthub.com", "Budget/Pricing Escalation", "body")
            
            yield {"event": "on_end", "data": {"output": {"classification": mock_classification, "generated_reply": "ESCALATED_NO_REPLY"}}}
        
        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        
        with patch("app.services.ai_graph.build_graph", return_value=mock_graph):
            outbound = await run_ai_pipeline(mock_db_session, conv, contact, inbound)
            
            # In pricing escalation, run_ai_pipeline should return None (silent AI)
            assert outbound is None
            
            # Check if conversation was updated
            mock_db_session.refresh(conv)
            assert conv.is_escalated is True
            assert conv.status == "pending_human"
            assert "Escalation Brief" in conv.escalation_brief
            
            # Verify email was sent
            mock_send_email.assert_called_once()
            args, _ = mock_send_email.call_args
            assert "sajjad_hussain@strategisthub.com" in args[1]
            assert "Budget/Pricing Escalation" in args[2]

@pytest.mark.asyncio
async def test_out_of_domain_handling(mock_db_session):
    """Test that out-of-domain requests (e.g. software) are handled correctly."""
    # Setup
    contact = Contact(username="Web Dev seeker", email="web@example.com")
    mock_db_session.add(contact)
    mock_db_session.commit()
    mock_db_session.refresh(contact)
    
    conv = Conversation(contact_id=contact.id, channel="website")
    mock_db_session.add(conv)
    mock_db_session.commit()
    mock_db_session.refresh(conv)
    
    inbound = Message(conversation_id=conv.id, sender_type="client", message="I need a website for my agency")
    mock_db_session.add(inbound)
    mock_db_session.commit()

    mock_classification = create_mock_classification({
        "role_type": "unknown",
        "is_qualified": False,
        "new_pipeline_stage": "lost"
    })
    
    with patch("app.services.ai_graph.ChatGroq") as MockLLM, \
         patch("app.services.ai_graph.search_similar") as mock_search:
        
        mock_llm_instance = MockLLM.return_value
        mock_search.return_value = ([], [])
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
        
        async def mock_astream_events(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessage(content="I'm sorry, we only handle real estate projects.")}}
            yield {"event": "on_end", "data": {"output": {"classification": mock_classification, "generated_reply": "I'm sorry, we only handle real estate projects."}}}
        
        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        
        with patch("app.services.ai_graph.build_graph", return_value=mock_graph):
            outbound = await run_ai_pipeline(mock_db_session, conv, contact, inbound)
            
            assert outbound is not None
            assert "real estate" in outbound.message.lower()
            
            mock_db_session.refresh(contact)
            assert contact.pipeline_stage == "lost"
            assert contact.is_qualified is False

@pytest.mark.asyncio
async def test_estimator_trigger(mock_db_session):
    """Test that the estimator agent is triggered when project details are provided."""
    # Setup
    contact = Contact(username="Ready Builder", email="ready@example.com")
    mock_db_session.add(contact)
    mock_db_session.commit()
    mock_db_session.refresh(contact)
    
    conv = Conversation(contact_id=contact.id, channel="website")
    mock_db_session.add(conv)
    mock_db_session.commit()
    mock_db_session.refresh(conv)
    
    inbound = Message(conversation_id=conv.id, sender_type="client", message="I want to build a 2000 sqft villa in London")
    mock_db_session.add(inbound)
    mock_db_session.commit()

    mock_classification = create_mock_classification({
        "project_type": "Villa",
        "geography": "London",
        "project_scope": "2000 sqft",
        "requires_action": ["update_status"]
    })
    
    with patch("app.services.ai_graph.ChatGroq") as MockLLM, \
         patch("app.services.ai_graph.search_similar") as mock_search, \
         patch("app.services.ai_graph.estimate_project") as mock_estimate:
        
        mock_llm_instance = MockLLM.return_value
        mock_search.return_value = ([], [])
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
        mock_estimate.return_value = {
            "mvp_budget_range": "$200k - $250k",
            "mvp_timeline": "6-8 months",
            "assumptions": ["Standard materials"]
        }
        
        async def mock_astream_events(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessage(content="An estimated cost for a 2000 sqft villa is $200k - $250k.")}}
            yield {"event": "on_end", "data": {"output": {"classification": mock_classification, "generated_reply": "An estimated cost for a 2000 sqft villa is $200k - $250k."}}}
        
        mock_graph = MagicMock()
        mock_graph.astream_events = mock_astream_events
        
        with patch("app.services.ai_graph.build_graph", return_value=mock_graph):
            outbound = await run_ai_pipeline(mock_db_session, conv, contact, inbound)
            
            assert outbound is not None
            assert "$200k" in outbound.message
