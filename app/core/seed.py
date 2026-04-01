"""Optional default form + sample FAQ rows for local dev."""

from sqlmodel import Session, select

from app.core.config import settings
from app.models.form import Form
from app.models.knowledge_base import KnowledgeBaseEntry
from app.models.pipeline_stage import PipelineStage
from app.services.kb_rag import ensure_entry_embedding


DEFAULT_FORM_CONFIG = {
    "fields": [
        {"name": "channel", "type": "select", "options": ["email", "website", "message", "sms"]},
        {"name": "name", "type": "text"},
        {"name": "email", "type": "email"},
        {"name": "phone", "type": "text"},
        {"name": "company", "type": "text"},
        {"name": "message", "type": "textarea"},
    ]
}

SAMPLE_FAQ = [
    {
        "question": "What are your business hours?",
        "answer": "Our team is available Monday–Friday, 9am–6pm local time. For urgent matters, please note it in your message.",
        "category": "general",
    },
    {
        "question": "How do I schedule a consultation?",
        "answer": "Reply with your preferred times and we will send a calendar link or confirm a slot with a specialist.",
        "category": "booking",
    },
    {
        "question": "What services do you offer?",
        "answer": "We provide client communication automation, lead qualification, and partnership outreach. Share your needs and we will match you with the right program.",
        "category": "services",
    },
]


def seed_if_empty(session: Session) -> None:
    # 1. Pipeline Stages
    if not session.exec(select(PipelineStage)).first():
        stages = [
            PipelineStage(
                key="discovery",
                pipelinestage="Discovery",
                order_index=1,
                ai_instructions="New leads. Ask for missing budget, timeline, or scope. Do not send booking links yet.",
            ),
            PipelineStage(
                key="qualified",
                pipelinestage="Qualified",
                order_index=2,
                ai_instructions="Serious leads with confirmed needs. Offer a consultation and provide the booking link.",
            ),
            PipelineStage(
                key="meeting_booked",
                pipelinestage="Meeting Booked",
                order_index=3,
                ai_instructions="Lead has scheduled a meeting. Prepare for the consultation and follow up if needed.",
            ),
            PipelineStage(
                key="proposal_ready",
                pipelinestage="Proposal Ready",
                order_index=4,
                ai_instructions="All details gathered. Inform the lead that a formal proposal is being prepared.",
            ),
            PipelineStage(
                key="negotiation",
                pipelinestage="Negotiation",
                order_index=5,
                ai_instructions="Proposal sent. Address pricing, terms, or specific project adjustments.",
            ),
            PipelineStage(
                key="won",
                pipelinestage="Won",
                order_index=6,
                ai_instructions="Project accepted. Coordinate next steps for kickoff.",
            ),
            PipelineStage(
                key="lost",
                pipelinestage="Lost / Not Qualified",
                order_index=7,
                ai_instructions="Not a good fit or lead went cold. Archive and do not pursue further.",
            ),
        ]
        for s in stages:
            session.add(s)
        session.commit()

    # 2. Default Form
    if not session.exec(select(Form)).first():
        f = Form(
            title="Contact / lead intake",
            description="Default intake form with channel selector",
            json_config=DEFAULT_FORM_CONFIG,
        )
        session.add(f)
        session.commit()

    existing_kb = session.exec(select(KnowledgeBaseEntry)).all()
    if existing_kb:
        return
    for row in SAMPLE_FAQ:
        e = KnowledgeBaseEntry(
            question=row["question"],
            answer=row["answer"],
            category=row["category"],
        )
        session.add(e)
    session.commit()
    if settings.OPENAI_API_KEY:
        for e in session.exec(select(KnowledgeBaseEntry)).all():
            if not e.embedding_json:
                try:
                    ensure_entry_embedding(session, e)
                except Exception:
                    pass
