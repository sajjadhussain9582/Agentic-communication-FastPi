import logging
from datetime import datetime
from sqlmodel import Session, select
from app.models.outcome import Outcome

logger = logging.getLogger(__name__)

def record_outcome(session: Session, contact_id: int, outcome_type: str, value: float | None = None, details: dict | None = None):
    """
    Persists successful outcomes (Bookings, Wins) to build a long-term learning dataset.
    outcome_type examples: 'booking_confirmed', 'stage_advanced', 'unsubscribed'
    """
    logger.info(f"Metrics: Recording outcome '{outcome_type}' for contact {contact_id}")
    
    outcome = Outcome(
        contact_id=contact_id,
        outcome_type=outcome_type,
        value=value,
        details=details or {}
    )
    session.add(outcome)
    session.commit()

def calculate_conversion_rate(session: Session, stage: str = "qualified") -> float:
    """Example analytical function for the boss's dashboard."""
    # Placeholder for real SQL logic
    return 0.25 # 25% mock conversion
