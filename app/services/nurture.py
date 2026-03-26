import logging
from datetime import datetime, timedelta
from sqlmodel import Session
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.services.ai_graph import run_ai_pipeline

logger = logging.getLogger(__name__)

def trigger_nurture_checkin(session: Session, contact: Contact):
    """
    Handles context-aware re-engagement for long-term leads.
    """
    logger.info(f"Nurture: Triggering context-aware check-in for {contact.email}")
    
    # 1. Find the last conversation
    from sqlalchemy import desc
    from app.models.conversation import Conversation
    conv = session.query(Conversation).filter(
        Conversation.contact_id == contact.id
    ).order_by(desc(Conversation.updated_at)).first()
    
    if not conv:
        return
        
    # 2. Trigger AI Pipeline with a 'Nurture' instruction
    # We pass a synthetic internal message that signals a long-term check-in
    try:
        from app.services.ai_graph import run_ai_pipeline
        # We pass a specific hint in the 'json_response' or just rely on 
        # the 'is_proactive' logic we added earlier.
        run_ai_pipeline(session, conv, contact)
        
        # Mark last outbound time
        contact.last_outbound_at = datetime.utcnow()
        session.add(contact)
        session.commit()
    except Exception as e:
        logger.error(f"Nurture Error: {e}")

def run_nurture_audit(session: Session):
    """
    Scans for leads that have been silent for more than 48h 
    and are currently in the 'nurture' or 'qualified' state.
    """
    threshold = datetime.utcnow() - timedelta(hours=48)
    
    # Find contacts who haven't had an outbound message in 48h
    # and are in discovery/qualified stages.
    leads = session.query(Contact).filter(
        Contact.pipeline_stage.in_(["discovery", "qualified"]),
        (Contact.last_outbound_at == None) | (Contact.last_outbound_at <= threshold),
        Contact.updated_at <= threshold
    ).all()
    
    for lead in leads:
        trigger_nurture_checkin(session, lead)
