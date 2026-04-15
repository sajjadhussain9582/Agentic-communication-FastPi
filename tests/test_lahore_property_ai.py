import asyncio
import logging
from unittest.mock import patch

from langchain_core.messages import AIMessage
from sqlmodel import Session, select

from app.core.database import init_db
from app.core.database import engine
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.ai_graph import _build_qualification_view
from app.services.ai_graph import MessageClassification
from app.services.ai_graph import run_ai_pipeline
from app.services.intake_service import process_inbound_message


def test_property_slot_extraction_handles_natural_phrasing():
    contact = Contact(
        username="Ali",
        email="ali@example.com",
        external_ids={},
    )

    view = _build_qualification_view(
        "I want to buy a 4-bedroom house in DHA Lahore within 3 months. Budget is around 7M PKR and I am the final decision maker.",
        contact,
    )

    slots = view["slots"]
    assert slots["project_type"] in {"4-5 bedroom house", "house"}
    assert "Dha" in str(slots["location"]) or "DHA" in str(slots["location"])
    assert "7M" in str(slots["budget_signal"]) or "budget" in str(slots["budget_signal"]).lower()
    assert "3 months" in str(slots["timeline_signal"]).lower()
    assert "decision" in str(slots["decision_authority"]).lower()
    assert "budget" not in view["missing_fields"]
    assert "location" not in view["missing_fields"]
    assert view["readiness"] >= 70


def test_location_is_persisted_in_json_state_not_contact_column():
    init_db()
    with Session(engine) as session:
        contact = Contact(username="Ali", email="ali2@example.com")
        session.add(contact)
        session.commit()
        session.refresh(contact)
        contact_id = contact.id

        conversation = Conversation(contact_id=contact.id, channel="website")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        response_conv = asyncio.run(
            process_inbound_message(
                session,
                "website",
                {"email": contact.email, "name": contact.username},
                "I want to buy a house in DHA Lahore with a 7M budget and move within 3 months.",
            )
        )

        assert response_conv.id == conversation.id

    with Session(engine) as verify_session:
        refreshed_contact = verify_session.get(Contact, contact_id)
        assert refreshed_contact is not None
        assert not hasattr(refreshed_contact, "geography")
        ext = refreshed_contact.external_ids or {}
        assert "slots" in ext
        assert "location" in ext["slots"]
        assert "DHA" in str(ext["slots"]["location"]) or "Dha" in str(ext["slots"]["location"])
        assert "qualification_view" in ext
        assert "location" in ext["qualification_view"]["slots"]


def test_out_of_scope_web_request_is_redirected_to_property_scope():
    init_db()
    with Session(engine) as session:
        contact = Contact(username="Web Lead", email="weblead@example.com")
        session.add(contact)
        session.commit()
        session.refresh(contact)

        conversation = Conversation(contact_id=contact.id, channel="website")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        asyncio.run(
            process_inbound_message(
                session,
                "website",
                {"email": contact.email, "name": contact.username},
                "Can you build me a website for my business?",
            )
        )

        refreshed = session.get(Conversation, conversation.id)
        assert refreshed is not None
        assert refreshed.status == "closed"
        assert refreshed.is_escalated is False

        msgs = list(session.exec(select(Message).where(Message.conversation_id == conversation.id)).all())
        assert any("property" in m.message.lower() for m in msgs)
        assert any("outside our property scope" in m.message.lower() for m in msgs)


def test_qualified_lead_gets_calendly_link():
    init_db()
    with Session(engine) as session:
        contact = Contact(username="Ali", email="ali_cal@example.com")
        session.add(contact)
        session.commit()
        session.refresh(contact)

        conversation = Conversation(contact_id=contact.id, channel="website")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        inbound = Message(
            conversation_id=conversation.id,
            conversation_public_uuid=conversation.public_uuid,
            sender_type="client",
            message_type="text",
            message="I want to buy a house in DHA Lahore with a 7M budget, parking, and move within 3 months.",
            channel="website",
            is_generated=False,
            is_handled=False,
        )
        session.add(inbound)
        session.commit()
        session.refresh(inbound)

        mock_classification = MessageClassification(
            role_type="buyer",
            intent_type="buying_request",
            relationship_type="inbound_lead",
            decision_role="decision_maker",
            engagement_temperature="warm",
            qualification_stage="qualified",
            is_escalated=False,
            conversation_status="open",
            lead_score=85.0,
            is_qualified=True,
            budget="7M PKR",
            project_type="house",
            timeline="3 months",
            project_scope="Family home",
            decision_authority="self",
            geography="DHA Lahore",
            transaction_type="buy",
            must_have_features=["parking"],
            out_of_scope=False,
            new_pipeline_stage="qualified",
            close_readiness_score=85.0,
            requires_action=["send_calendly"],
            missing_qualification_fields=[],
            filled_fields=[],
            next_best_questions=[],
        )

        with patch("app.services.ai_graph.ChatOpenAI") as MockLLM:
            mock_llm_instance = MockLLM.return_value
            mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
            mock_llm_instance.invoke.return_value = AIMessage(
                content="We can help with the next step."
            )

            outbound = asyncio.run(
                run_ai_pipeline(session, conversation, contact, inbound, {})
            )

        assert outbound is not None
        assert "calendly.com" in outbound.message.lower() or "booking link" in outbound.message.lower()


def test_transaction_type_from_ai_pipeline_persists_in_json_state(caplog):
    init_db()
    with Session(engine) as session:
        contact = Contact(username="Ali", email="ali_tx@example.com")
        session.add(contact)
        session.commit()
        session.refresh(contact)

        conversation = Conversation(contact_id=contact.id, channel="website")
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        inbound = Message(
            conversation_id=conversation.id,
            conversation_public_uuid=conversation.public_uuid,
            sender_type="client",
            message_type="text",
            message="I want to buy a house in DHA Lahore and I am the final decision maker.",
            channel="website",
            is_generated=False,
            is_handled=False,
        )
        session.add(inbound)
        session.commit()
        session.refresh(inbound)

        mock_classification = MessageClassification(
            role_type="buyer",
            intent_type="buying_request",
            relationship_type="inbound_lead",
            decision_role="decision_maker",
            engagement_temperature="warm",
            qualification_stage="discovery",
            is_escalated=False,
            conversation_status="open",
            lead_score=55.0,
            is_qualified=True,
            budget="7M PKR",
            project_type="house",
            timeline="3 months",
            project_scope="Family home",
            decision_authority="self",
            geography="DHA Lahore",
            transaction_type="buy",
            must_have_features=["parking"],
            out_of_scope=False,
            new_pipeline_stage="discovery",
            close_readiness_score=50.0,
            requires_action=[],
            missing_qualification_fields=[],
            filled_fields=[],
            next_best_questions=["What area of DHA are you focused on?"],
        )

        with patch("app.services.ai_graph.ChatOpenAI") as MockLLM:
            mock_llm_instance = MockLLM.return_value
            mock_llm_instance.with_structured_output.return_value.invoke.return_value = mock_classification
            mock_llm_instance.invoke.return_value = AIMessage(
                content="Thanks. Which phase are you targeting?"
            )

            with caplog.at_level(logging.INFO):
                outbound = asyncio.run(
                    run_ai_pipeline(session, conversation, contact, inbound, {})
                )

        assert outbound is not None
        refreshed_contact = session.get(Contact, contact.id)
        assert refreshed_contact is not None
        ext = refreshed_contact.external_ids or {}
        assert "ai_state" in ext
        assert ext["ai_state"]["decision_authority"] == "self"
        assert ext["ai_state"]["transaction_type"] == "buy"
        assert ext["slots"]["decision_authority"]
        assert ext["slots"]["transaction_type"]
        assert any("AI inbound" in rec.message for rec in caplog.records)
        assert any("AI retrieval" in rec.message for rec in caplog.records)
        assert any("AI classification" in rec.message for rec in caplog.records)
        assert any("AI reply" in rec.message for rec in caplog.records)
        assert any("buyer" in rec.message for rec in caplog.records)
