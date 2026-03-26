import logging
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select
from app.core.database import engine
from app.models.campaign import CampaignMessageRow, CampaignTarget
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.services.channel_delivery import deliver_message
from app.services.pipeline_manager import detect_sla_violation
from app.services.nurture import run_nurture_audit

logger = logging.getLogger(__name__)

def check_queued_messages():
    """Find and send queued campaign messages."""
    with Session(engine) as session:
        # Check for messages older than 2 minutes for demo/safety
        threshold = datetime.utcnow() - timedelta(days=1)
        queued = session.exec(
            select(CampaignMessageRow)
            .where(CampaignMessageRow.send_status == "queued")
            .where(CampaignMessageRow.created_at <= threshold)
        ).all()
        
        if not queued:
            return

        logger.info(f"Worker: Found {len(queued)} queued campaign messages to process.")
        for row in queued:
            target = session.get(CampaignTarget, row.target_id)
            if not target or not (target.email or target.phone):
                logger.warning(f"Worker: No recipient for campaign row {row.id}")
                row.send_status = "failed"
                session.add(row)
                continue
                
            if target.status == "replied":
                logger.info(f"Worker: Target {target.id} already replied. Skipping follow-up {row.id}.")
                row.send_status = "cancelled"
                session.add(row)
                continue
            
            try:
                # Start an event loop for the async delivery
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                recipient = target.email or target.phone
                # We use 'email' as default channel for campaign rows unless target specifies
                res = loop.run_until_complete(
                    deliver_message(
                        session,
                        channel="email", 
                        recipient=recipient,
                        message=row.message_text,
                        metadata={
                            "campaign_id": row.campaign_id, 
                            "target_id": row.target_id,
                            "subject": "Follow-up: Partnership Opportunity"
                        }
                    )
                )
                loop.close()
                
                if res.get("ok"):
                    row.send_status = "sent"
                    logger.info(f"Worker: Successfully sent queued message to {recipient}")
                else:
                    row.send_status = "failed"
                    logger.error(f"Worker: Delivery failed for {recipient}: {res.get('reason')}")
            except Exception as e:
                logger.error(f"Worker: Error processing queued message {row.id}: {e}")
                row.send_status = "failed"
            
            session.add(row)
        session.commit()

def check_stale_leads():
    """Find leads with no response for 5+ minutes (demo) and log for re-engagement."""
    with Session(engine) as session:
        # In production: timedelta(hours=48)
        stale_threshold = datetime.utcnow() - timedelta(hours=48)
        
        stale_convs = session.exec(
            select(Conversation)
            .where(Conversation.status == "open")
            .where(Conversation.updated_at <= stale_threshold)
            .where(Conversation.is_escalated == False)
        ).all()
        
        if not stale_convs:
            return

        logger.info(f"Worker: Analyzing {len(stale_convs)} stale conversations.")
        for conv in stale_convs:
            contact = session.get(Contact, conv.contact_id)
            if not contact:
                continue
            
            # Check for Pipeline SLA Violations
            violation = detect_sla_violation(contact)
            if violation:
                logger.warning(f"Worker [SLA]: {violation} for lead {contact.email}")
            
            logger.info(f"Worker: Re-engagement triggered for stale lead {contact.email or contact.username}")
            try:
                from app.services.ai_graph import run_ai_pipeline
                run_ai_pipeline(session, conv, contact)
                # Mark outbound time on contact
                contact.last_outbound_at = datetime.utcnow()
                session.add(contact)
            except Exception as e:
                logger.error(f"Worker: Failed to run re-engagement for {contact.email}: {e}")
        
        # Also run the broader Nurture audit (covers leads without active conversations)
        run_nurture_audit(session)
        
        session.commit()

scheduler = BackgroundScheduler()

def start_worker():
    # Low frequency for stability
    scheduler.add_job(check_queued_messages, 'interval', minutes=1, id='check_queued_messages')
    scheduler.add_job(check_stale_leads, 'interval', minutes=3, id='check_stale_leads')
    scheduler.start()
    logger.info("Background worker initialized and started.")
