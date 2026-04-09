import logging
import asyncio
from email.utils import parseaddr
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select, or_, exists
from app.core.database import engine
from app.models.campaign import CampaignMessageRow, CampaignTarget
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.integration import Integration
from app.services.channel_delivery import deliver_message
from app.services.pipeline_manager import detect_sla_violation
from app.services.nurture import run_nurture_audit
from app.services.imap_service import fetch_new_emails
from app.services.intake_service import process_inbound_message
from app.services.integrations.calendly_client import CalendlyClient
from app.services.integrations.hubspot_client import HubSpotClient

logger = logging.getLogger(__name__)

def sync_contacts_to_hubspot():
    """Find contacts that have been modified and push them to HubSpot."""
    with Session(engine) as session:
        # Check if HubSpot is connected
        integration = session.exec(
            select(Integration).where(Integration.provider == "hubspot", Integration.status == "connected")
        ).first()
        
        if not integration:
            return

        # Fetch contacts with status or pipeline_stage 'qualified' or 'meeting_booked'
        contacts = session.exec(
            select(Contact).where(
                or_(
                    Contact.status == "qualified",
                    Contact.status == "meeting_booked",
                    Contact.pipeline_stage == "qualified",
                    Contact.pipeline_stage == "meeting_booked",
                    Contact.stage == "qualified",
                    Contact.stage == "meeting_booked"
                )
            )
        ).all()
        if not contacts:
            return

        logger.info(f"Worker: Found {len(contacts)} leads for HubSpot sync.")
        _perform_hubspot_sync(session, contacts)

def sync_single_contact_to_hubspot_task(contact_id: int):
    """Background task to sync a single contact immediately if qualified or meeting booked."""
    with Session(engine) as session:
        contact = session.get(Contact, contact_id)
        if contact and (contact.status == "qualified" or contact.status == "meeting_booked"):
            _perform_hubspot_sync(session, [contact])
        else:
            logger.info(f"Worker: Skipping HubSpot sync for contact {contact_id} (status: {contact.status if contact else 'None'})")

def _perform_hubspot_sync(session: Session, contacts: list[Contact]):
    hubspot = HubSpotClient(session)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # HubSpot property mappings
    STATUS_MAP = {
        "new": "NEW",
        "active": "OPEN",
        "stale": "ATTEMPTED_TO_CONTACT",
        "qualified": "OPEN_DEAL",
        "unqualified": "UNQUALIFIED",
        "lost": "UNQUALIFIED"
    }
    
    STAGE_MAP = {
        "discovery": "lead",
        "qualified": "marketingqualifiedlead",
        "proposal_ready": "salesqualifiedlead",
        "negotiation": "opportunity",
        "won": "customer",
        "lost": "other"
    }
    
    try:
        for c in contacts:
            try:
                _, clean_email = parseaddr(c.email)
                if not clean_email:
                    continue

                # Translate internal values to HubSpot options
                hs_status = STATUS_MAP.get((c.status or "new").lower(), "NEW")
                hs_lifecycle = STAGE_MAP.get((c.pipeline_stage or "discovery").lower(), "lead")
                
                # If meeting_booked, ensure lifecycle stage reflects that
                if c.status == "meeting_booked" or c.pipeline_stage == "meeting_booked":
                    hs_lifecycle = "opportunity"

                props = {
                    "email": clean_email,
                    "firstname": c.username.split(" ")[0] if c.username else "Unknown",
                    "lastname": " ".join(c.username.split(" ")[1:]) if c.username and " " in c.username else "",
                    "phone": c.phone,
                    "company": c.company,
                    "lifecyclestage": hs_lifecycle,
                    "hs_lead_status": hs_status
                }
                
                res = loop.run_until_complete(hubspot.upsert_contact(clean_email, props))
                
                if res.get("ok"):
                    results = res.get("data", {}).get("results", [])
                    if results:
                        hs_id = results[0].get("id")
                        
                        # Refresh contact from DB to get the most recent external_ids
                        session.refresh(c)
                        
                        ext = dict(c.external_ids or {})
                        ext["hubspot_id"] = hs_id
                        c.external_ids = ext
                        
                        session.add(c)
                        session.commit()
                        session.refresh(c) # Refresh again after commit
                        
                        logger.info(f"HubSpot Sync: Contact {clean_email} synced (ID: {hs_id})")

                        # Create ticket for qualified or meeting_booked leads
                        is_qualified = c.status == "qualified" or c.pipeline_stage == "qualified"
                        is_meeting = c.status == "meeting_booked" or c.pipeline_stage == "meeting_booked"

                        if is_qualified or is_meeting:
                            ticket_type = "Meeting Booked" if is_meeting else "Qualified Lead"
                            ticket_key = f"hubspot_ticket_{ticket_type.lower().replace(' ', '_')}_id"
                            
                            # CRITICAL: Re-check external_ids after refreshing from DB
                            if c.external_ids and c.external_ids.get(ticket_key):
                                logger.info(f"HubSpot Sync: Ticket ({ticket_type}) already exists for {clean_email}, skipping.")
                                continue
                                
                            logger.info(f"HubSpot Sync: Creating {ticket_type} ticket for {clean_email}...")
                            
                            ticket_props = {
                                "subject": f"{ticket_type}: {c.username or clean_email}",
                                "hs_pipeline": "0",
                                "hs_pipeline_stage": "1",
                                "hs_ticket_priority": "HIGH" if is_qualified else "URGENT",
                                "content": f"Lead status: {c.status}\nProject: {c.project_type or 'N/A'}\nBudget: {c.budget or 'N/A'}\nTimeline: {c.timeline or 'N/A'}"
                            }
                            
                            ticket_res = loop.run_until_complete(hubspot.create_ticket(ticket_props, contact_id=hs_id))
                            if ticket_res.get("ok"):
                                ticket_id = ticket_res.get("data", {}).get("id")
                                
                                # Refresh again before saving ticket ID to handle any concurrent updates
                                session.refresh(c)
                                ext = dict(c.external_ids or {})
                                ext[ticket_key] = ticket_id
                                c.external_ids = ext
                                
                                session.add(c)
                                session.commit()
                                logger.info(f"HubSpot Sync: Ticket created for {clean_email} ({ticket_type}) ID: {ticket_id}")
                            else:
                                logger.error(f"HubSpot Sync: Failed to create ticket: {ticket_res.get('error')}")
                else:
                    logger.error(f"HubSpot Sync: Failed for {c.email}: {res.get('error')}")
            except Exception as e:
                logger.error(f"HubSpot Sync: Error for contact {c.id}: {e}")
        
        session.commit()
    finally:
        loop.close()

def check_incoming_emails():
    """Fetch new emails from the IMAP server and process them."""
    with Session(engine) as session:
        integration = session.exec(
            select(Integration).where(Integration.provider == "email", Integration.status == "connected")
        ).first()
        
        if not integration or not integration.config_json:
            return

        config = integration.config_json
        if config.get("type") != "smtp": # For now, only support smtp for imap
            return

        new_messages = fetch_new_emails(config)
        if not new_messages:
            return

        logger.info(f"Worker: Found {len(new_messages)} new emails to process.")
        # Process all new messages concurrently
        async def process_all():
            tasks = [
                process_inbound_message(session, "email", {"email": msg["from"], "name": msg["from"]}, msg["body"], msg["subject"])
                for msg in new_messages
            ]
            await asyncio.gather(*tasks)

        asyncio.run(process_all())

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
                # Fixed: run_ai_pipeline is async, must be run within a loop or awaited
                # Since this worker function is sync, we use a loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(run_ai_pipeline(session, conv, contact))
                finally:
                    loop.close()
                # Mark outbound time on contact
                contact.last_outbound_at = datetime.utcnow()
                session.add(contact)
            except Exception as e:
                logger.error(f"Worker: Failed to run re-engagement for {contact.email}: {e}")
        
        # Also run the broader Nurture audit (covers leads without active conversations)
        run_nurture_audit(session)
        
        session.commit()

def check_qualified_leads():
    """Check qualified leads with no recent activity and send follow-up."""
    with Session(engine) as session:
        # Find qualified conversations with no messages in last 7 days
        cutoff = datetime.utcnow() - timedelta(days=7)
        qualified_convs = session.exec(
            select(Conversation)
            .where(Conversation.status == "open")
            .where(or_(
                Conversation.stage == "qualified",
                Conversation.pipeline_stage == "qualified"
            ))
            .where(~exists(
                select(Message)
                .where(Message.conversation_id == Conversation.id)
                .where(Message.created_at > cutoff)
            ))
        ).all()
        
        logger.info(f"Worker: Found {len(qualified_convs)} qualified leads needing follow-up.")
        
        for conv in qualified_convs:
            contact = session.get(Contact, conv.contact_id)
            if not contact or not contact.email:
                continue
            
            # Generate follow-up message
            followup_msg = Message(
                conversation_id=conv.id,
                conversation_public_uuid=conv.public_uuid,
                sender_type="agent",
                message_type="text",
                message="Hi, just checking in on your project. Do you have any updates or questions?",
                channel="email",
                is_generated=True,
                is_handled=True,
            )
            session.add(followup_msg)
            session.commit()
            session.refresh(followup_msg)
            
            # Send via email
            try:
                asyncio.run(deliver_message(
                    session,
                    channel="email",
                    recipient=contact.email,
                    message=followup_msg.message,
                    metadata={"subject": "Follow-up on Your Project"}
                ))
                logger.info(f"Worker: Sent follow-up to qualified lead {contact.email}")
            except Exception as e:
                logger.error(f"Worker: Failed to send follow-up to {contact.email}: {e}")

def check_calendly_bookings():
    """Check for new Calendly bookings and update lead status."""
    logger.info("Worker: Starting check_calendly_bookings...")
    with Session(engine) as session:
        integration = session.exec(
            select(Integration).where(Integration.provider == "scheduling", Integration.status == "connected")
        ).first()
        
        if not integration:
            logger.info("Worker: No connected scheduling integration found.")
            return

        # Get last checked time from config
        last_checked = None
        if integration.config_json and "last_calendly_check" in integration.config_json:
            last_checked_str = integration.config_json["last_calendly_check"]
            last_checked = datetime.fromisoformat(last_checked_str)
            logger.info(f"Worker: Last Calendly check was at {last_checked_str}")

        client = CalendlyClient(session)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            logger.info(f"Worker: Fetching new bookings since {last_checked}...")
            new_bookings = loop.run_until_complete(client.check_new_bookings(session, last_checked))
            if new_bookings:
                logger.info(f"Worker: Found {len(new_bookings)} new Calendly bookings to process.")
                for booking in new_bookings:
                    contact = booking["contact"]
                    metadata = booking["metadata"]
                    
                    logger.info(f"Worker: Processing booking for contact {contact.email}...")
                    
                    # Update contact username if it's missing or generic
                    new_name = metadata.get("invitee_name")
                    if new_name and (not contact.username or contact.username == contact.email.split("@")[0]):
                        contact.username = new_name
                        logger.info(f"Worker: Updated username for {contact.email} to {new_name}")

                    # Update contact pipeline stage
                    contact.pipeline_stage = "meeting_booked"
                    contact.status = "meeting_booked"
                    contact.stage = "meeting_booked"
                    contact.stage_entered_at = datetime.utcnow()
                    
                    # Also update pipeline_stage_id by looking up the stage record
                    stage_record = session.exec(
                        select(PipelineStage).where(PipelineStage.key == "meeting_booked")
                    ).first()
                    if stage_record:
                        contact.pipeline_stage_id = stage_record.id
                    
                    # Store metadata in external_ids
                    ext = dict(contact.external_ids or {})
                    if "calendly" not in ext:
                        ext["calendly"] = []
                    
                    # Avoid duplicates in the list
                    invitee_uri = metadata.get("invitee_uri")
                    if invitee_uri not in [b.get("invitee_uri") for b in ext["calendly"] if isinstance(b, dict)]:
                        ext["calendly"].append(metadata)
                        logger.info(f"Worker: Added Calendly metadata for {contact.email}")
                    
                    contact.external_ids = ext
                    session.add(contact)
                    
                    # Find conversation and add a message
                    conv = session.exec(
                        select(Conversation).where(Conversation.contact_id == contact.id)
                    ).first()
                    if conv:
                        booking_msg = Message(
                            conversation_id=conv.id,
                            conversation_public_uuid=conv.public_uuid,
                            sender_type="system",
                            message_type="text",
                            message=f"Meeting booked via Calendly: {metadata['scheduled_start_time']}",
                            channel="system",
                            is_generated=True,
                            is_handled=True,
                        )
                        session.add(booking_msg)
                        logger.info(f"Worker: Added system message for booking to conversation {conv.id}")
                    
                    logger.info(f"Worker: Updated contact {contact.email} for Calendly booking.")
            else:
                logger.info("Worker: No new bookings found in this cycle.")
            
            # Update last checked time
            now = datetime.utcnow()
            config = dict(integration.config_json or {})
            config["last_calendly_check"] = now.isoformat()
            integration.config_json = config
            integration.last_sync_at = now
            session.add(integration)
            session.commit()
            logger.info(f"Worker: Updated Calendly check checkpoint to {now.isoformat()}")
            
        except Exception as e:
            logger.error(f"Worker: Error checking Calendly bookings: {e}", exc_info=True)
        finally:
            loop.close()

scheduler = BackgroundScheduler()

def start_worker():
    # Low frequency for stability
    scheduler.add_job(check_incoming_emails, 'interval', seconds=30, id='check_incoming_emails')
    scheduler.add_job(check_queued_messages, 'interval', minutes=60, id='check_queued_messages')
    scheduler.add_job(check_stale_leads, 'interval', minutes=60, id='check_stale_leads')
    scheduler.add_job(check_qualified_leads, 'interval', hours=24, id='check_qualified_leads')
    scheduler.add_job(sync_contacts_to_hubspot, 'interval', minutes=60, id='sync_contacts_to_hubspot')
    scheduler.add_job(check_calendly_bookings, 'interval', minutes=60, id='check_calendly_bookings')
    scheduler.start()
    logger.info("Background worker initialized and started.")
