"""Keyword-based category router for hybrid KB retrieval."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Keyword → category mapping ─────────────────────────────────────────────
_ROUTING_RULES: list[tuple[list[str], str]] = [
    # Pricing / budget keywords
    (["price", "pricing", "cost", "how much", "expensive", "affordable", "charge", "rate"],
     "pricing"),
    # Budget flexibility
    (["budget", "flexible", "flexibility", "negotiate", "too high", "lower price", "payment plan", "discount"],
     "budget_flexibility"),
    # MVP guidance
    (["mvp", "minimum viable", "basic version", "start small", "prototype", "first version", "phase 1"],
     "mvp_guidance"),
    # Feature suggestions
    (["features", "don't know what", "guide me", "suggest", "what should", "unclear", "not sure what to build"],
     "feature_suggestions"),
    # Partnership / referrals
    (["partner", "partnership", "referral", "collaborate", "collaboration", "contractor", "architect", "builder"],
     "partnership"),
    # Real estate
    (["real estate", "property", "listings", "mls", "realty", "houses", "apartments"],
     "real_estate_platform"),
    # Consultation / booking
    (["consultation", "consult", "book", "schedule", "meeting", "call", "appointment", "next step"],
     "consultation"),
    # Timeline
    (["timeline", "how long", "duration", "weeks", "months", "deadline", "when ready"],
     "timeline"),
    # Process
    (["process", "workflow", "stages", "start to finish", "after discovery", "what happens next"],
     "process"),
    # Website vs app
    (["website", "web app", "mobile app", "need an app", "need a website", "website or app"],
     "website_guidance"),
    # Services
    (["services", "what do you do", "what do you offer", "capabilities"],
     "services_overview"),
    # Qualification
    (["proposal", "information needed", "requirements", "before we start", "intake"],
     "qualification"),
]


def detect_category(query: str) -> str | None:
    """Scan query for keywords and return the best-matching KB category, or None."""
    q_lower = query.lower()

    best_category: str | None = None
    best_count = 0

    for keywords, category in _ROUTING_RULES:
        hits = sum(1 for kw in keywords if kw in q_lower)
        if hits > best_count:
            best_count = hits
            best_category = category

    if best_category:
        logger.info("KB router: detected category='%s' (hits=%d) for query='%s'",
                     best_category, best_count, query[:60])

    return best_category
