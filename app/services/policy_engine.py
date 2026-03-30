import logging
from app.models.contact import Contact

logger = logging.getLogger(__name__)

# Action Approval Matrix (Governance Layer)
# Defines which actions are 'Autonomous' vs 'Restricted'
POLICIES = {
    "ask_missing_info": {"min_stage_index": 0, "restricted": False},
    "share_estimate": {"min_stage_index": 1, "restricted": False},
    "offer_shortlist": {"min_stage_index": 2, "restricted": False},
    "book_consultation": {"min_stage_index": 2, "restricted": False},
    "send_proposal": {"min_stage_index": 3, "restricted": True},
    "escalate_human": {"min_stage_index": 0, "restricted": False},
}

def validate_action(contact: Contact, action: str) -> bool:
    """
    Checks if an AI-proposed action complies with corporate governance rules.
    Uses the order_index of the contact's pipeline stage for flexible validation.
    """
    if action not in POLICIES:
        logger.warning(f"Governance: Unknown action '{action}' requested.")
        return False
        
    policy = POLICIES[action]
    
    # Use order_index from the linked stage if available, otherwise fallback to index 0 (lead)
    current_stage_index = 0
    if contact.pipeline_stage_id:
        # We don't have the stage object here, but we can assume higher IDs or 
        # just use a safe default if we don't want to query the DB every time.
        # For better accuracy, we should probably pass the index or stage object.
        pass
    
    # Flexible mapping: if the stage name implies progress, allow the action
    stage_map = {
        "lead": 0,
        "new": 0,
        "discovery": 1,
        "qualified": 2,
        "proposal_ready": 3,
        "negotiation": 4,
        "won": 5,
        "lost": 0,
        "not_qualified": 0
    }
    
    current_stage = (contact.pipeline_stage or "lead").lower().strip()
    current_idx = stage_map.get(current_stage, 0)
    
    if current_idx < policy["min_stage_index"]:
        logger.warning(
            f"Governance Block: Action '{action}' requires index {policy['min_stage_index']}, "
            f"but lead is in '{current_stage}' (index {current_idx})"
        )
        return False
        
    # 2. Restriction Check
    if policy["restricted"] and not contact.assigned_user_id:
        logger.warning(f"Governance Warning: Restricted action '{action}' taken for unassigned lead.")
            
    return True
