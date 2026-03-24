"""Optional default form + sample FAQ rows for local dev."""

from sqlmodel import Session, select

from app.core.config import settings
from app.models.form import Form
from app.models.knowledge_base import KnowledgeBaseEntry
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
