import logging
from datetime import datetime
from typing import Any, Dict, List
from sqlmodel import Session
from app.models.contact import Contact

logger = logging.getLogger(__name__)

# Master Pipeline Configuration (Business Logic Layer)
# Each stage has 'required_slots' that must be filled before entry.
PIPELINE_STAGES = {
    "lead": {"required_slots": []},
    "discovery": {"required_slots": ["project_type"]},
    "qualified": {"required_slots": ["project_type", "budget_signal", "timeline_signal"]},
    "consultation": {"required_slots": ["project_type", "budget_signal", "timeline_signal", "scope"]},
    "proposal": {"required_slots": ["project_type", "budget_signal", "timeline_signal", "scope", "stakeholders"]},
}

PIPELINE_ORDER = ["lead", "discovery", "qualified", "consultation", "proposal"]

def sync_pipeline_stage(session: Session, contact: Contact, ai_decision: Dict[str, Any]):
    """
    Orchestrates lead transitions based on governed business rules.
    This ensures the AI doesn't skip stages or 'guess' its way into 
    a qualified state without the minimum required evidence.
    """
    current_stage = contact.pipeline_stage or "lead"
    if current_stage not in PIPELINE_STAGES:
        current_stage = "lead"

    # 1. Gather all filled slots (from contact history + current turn)
    # We prioritize the AI's 'filled_fields' but fallback to DB fields.
    ai_filled = ai_decision.get("filled_fields", [])
    if isinstance(ai_filled, str):
        ai_filled = [s.strip() for s in ai_filled.split(",") if s.strip()]
    
    # Aggregate slots for a comprehensive check
    all_slots = set(ai_filled)
    if contact.project_type: all_slots.add("project_type")
    if contact.budget: all_slots.add("budget_signal")
    if contact.timeline: all_slots.add("timeline_signal")
    
    # Also check external_ids/slots
    ext = contact.external_ids or {}
    slots = ext.get("slots", {})
    for k, v in slots.items():
        if v:
            all_slots.add(k)
    
    # 2. Determine target stage based on meeting ALL requirements
    target_stage = "lead"
    for stage_name in PIPELINE_ORDER:
        reqs = PIPELINE_STAGES[stage_name]["required_slots"]
        if all(s in all_slots for s in reqs):
            target_stage = stage_name
        else:
            # Cannot advance further than the first failing set of requirements
            break
            
    # 3. Apply Transition Logic
    if target_stage != current_stage:
        idx_current = PIPELINE_ORDER.index(current_stage)
        idx_target = PIPELINE_ORDER.index(target_stage)
        
        # We only advance automatically. Regressions (moving back) 
        # usually require a human or specific 'lost' event.
        if idx_target > idx_current:
            logger.info(f"Pipeline [Lead {contact.id}]: Advancing {current_stage} -> {target_stage}")
            contact.pipeline_stage = target_stage
            contact.stage_entered_at = datetime.utcnow()
            
            # Sync with legacy 'stage' field to prevent UI breakage
            contact.stage = target_stage
            
            # Sync status field for worker sync
            if target_stage == "qualified":
                contact.status = "qualified"
            
            # Update evidence log
            evidence = contact.qualification_evidence or {}
            evidence[target_stage] = {
                "achieved_at": datetime.utcnow().isoformat(),
                "trigger": "automated_rule_engine",
                "missing_at_time": ai_decision.get("missing_fields", [])
            }
            contact.qualification_evidence = evidence
            
    session.add(contact)

def detect_sla_violation(contact: Contact) -> str | None:
    """
    Checks if a lead is 'stuck' in a stage longer than allowed.
    Returns the violation type if true.
    """
    if not contact.stage_entered_at:
        return None
        
    idle_time = (datetime.utcnow() - contact.stage_entered_at).total_seconds()
    stage = contact.pipeline_stage or "lead"
    
    # SLA Config (seconds)
    SLAS = {
        "lead": 3600 * 2,         # 2 hours
        "discovery": 3600 * 24,   # 1 day
        "qualified": 3600 * 48,   # 2 days
    }
    
    if stage in SLAS and idle_time > SLAS[stage]:
        return f"SLA_STUCK_{stage.upper()}"
        
    return None
