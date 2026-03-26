import logging
from app.models.contact import Contact

logger = logging.getLogger(__name__)

# Action Approval Matrix (Governance Layer)
# Defines which actions are 'Autonomous' vs 'Restricted'
POLICIES = {
    "ask_missing_info": {"min_stage": "lead", "restricted": False},
    "share_estimate": {"min_stage": "discovery", "restricted": False},
    "offer_shortlist": {"min_stage": "qualified", "restricted": False},
    "book_consultation": {"min_stage": "qualified", "restricted": False},
    "send_proposal": {"min_stage": "consultation", "restricted": True}, # Requires human flag or high evidence
    "escalate_human": {"min_stage": "lead", "restricted": False},
}

PIPELINE_ORDER = ["lead", "discovery", "qualified", "consultation", "proposal"]

def validate_action(contact: Contact, action: str) -> bool:
    """
    Checks if an AI-proposed action complies with corporate governance rules.
    """
    if action not in POLICIES:
        logger.warning(f"Governance: Unknown action '{action}' requested.")
        return False
        
    policy = POLICIES[action]
    current_stage = contact.pipeline_stage or "lead"
    
    # 1. Stage Gate Check
    if current_stage not in PIPELINE_ORDER:
        return False
        
    if PIPELINE_ORDER.index(current_stage) < PIPELINE_ORDER.index(policy["min_stage"]):
        logger.warning(f"Governance Block: Action '{action}' requires stage '{policy['min_stage']}', but lead is in '{current_stage}'")
        return False
        
    # 2. Restriction Check
    if policy["restricted"]:
        # In V2, we might check for contact.is_vetted or assigned_user_id
        # For now, we allow it but log a warning if no human is assigned.
        if not contact.assigned_user_id:
            logger.warning(f"Governance Warning: Restricted action '{action}' taken for unassigned lead.")
            
    return True
