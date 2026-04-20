"""LangGraph pipeline: RAG → structured decision → FAQ-grounded reply."""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.pipeline_stage import PipelineStage
from app.services.estimator import estimate_project
from app.services.kb_router import detect_category
from app.services.kb_rag import format_rag_context, search_similar
from app.services.metrics import record_outcome
from app.services.pipeline_manager import sync_pipeline_stage
from app.services.policy_engine import validate_action
from app.services.workflow_engine import run_decision_workflows

logger = logging.getLogger(__name__)

_LOG_PREVIEW_LIMIT = 160

def _rag_debug_enabled() -> bool:
    return os.getenv("RAG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _preview_text(value: Any, limit: int = _LOG_PREVIEW_LIMIT) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _structured_log(event: str, **payload: Any) -> None:
    try:
        logger.info("%s %s", event, json.dumps(payload, default=str, ensure_ascii=False))
    except Exception:
        logger.info("%s %s", event, payload)

# Global checkpointer for human-in-the-loop interrupts
_memory_saver = MemorySaver()

# Global connection pool for PostgresSaver
_pool = None

def get_checkpointer():
    global _pool
    if not settings.DATABASE_URL or "sqlite" in settings.DATABASE_URL:
        return _memory_saver
    
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": None}
        )
    
    return PostgresSaver(_pool)

def setup_checkpointer():
    """Initializes the checkpoints table if it doesn't exist."""
    if not settings.DATABASE_URL or "sqlite" in settings.DATABASE_URL:
        return
    
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=1,
            max_size=2,
            kwargs={"autocommit": True, "prepare_threshold": None}
        )
    
    saver = PostgresSaver(_pool)
    saver.setup()
    logger.info("LangGraph checkpoints table initialized.")


class MessageClassification(BaseModel):
    """Structured output from the classification node."""
    role_type: str = Field(
        description="one of: buyer, seller, landlord, tenant, investor, agent, developer, contractor, architect, builder, unknown"
    )
    intent_type: str = Field(
        description="one of: property_search, buying_request, selling_request, rent_inquiry, service_inquiry, pricing_request, booking_request, support_request, follow_up, complaint, nurture, partnership_inquiry"
    )
    relationship_type: str = Field(description="one of: inbound_lead, outbound_prospect, referral_partner, existing_client, dormant_lead, reengaged_lead")
    decision_role: str = Field(description="one of: decision_maker, influencer, researcher, assistant, family_member, broker, unknown")
    engagement_temperature: str = Field(description="one of: cold, warm, hot")
    qualification_stage: str = Field(description="one of: discovery, qualified, not_qualified, needs_info")
    is_escalated: bool = Field(description="True if human intervention is explicitly needed")
    conversation_status: str = Field(description="one of: open, pending_human, closed")
    lead_score: float | None = Field(description="number 0-100 or null if unknown")
    is_qualified: bool | None = Field(description="true if serious client, false if obviously unrelated/spam, null otherwise")
    budget: str | None = Field(description="budget signal from user")
    project_type: str | None = Field(description="project type signal from user")
    timeline: str | None = Field(description="timeline signal from user")
    project_scope: str | None = Field(description="scope details from user")
    decision_authority: str | None = Field(description="who makes decisions")
    geography: str | None = Field(description="location of project")
    transaction_type: str | None = Field(description="buy, sell, rent, let, or invest")
    must_have_features: list[str] = Field(default_factory=list, description="important property requirements")
    out_of_scope: bool = Field(default=False, description="true if request is outside the Lahore property domain")
    new_pipeline_stage: str = Field(description="The stage the AI decides to move them to (discovery, qualified, proposal_ready, etc.)")
    close_readiness_score: float = Field(description="number 0-100")
    requires_action: list[str] = Field(description="List of actions: 'update_status', 'send_calendly', 'send_proposal'")
    missing_qualification_fields: list[str] = Field(description="Fields still needed: scope, budget, timeline, location, stakeholders")
    filled_fields: list[str] = Field(default_factory=list, description="Fields already filled: project_type, scope, budget_signal, timeline_signal, location, stakeholders, must_have_features")
    next_best_questions: list[str] = Field(description="1-3 short questions to ask next")


DOMAIN_PROFILE: dict[str, Any] = {
    "name": "lahore_property",
    "service_area": "Lahore",
    "default_stage": "discovery",
    "stage_order": ["lead", "discovery", "qualified", "consultation", "proposal_ready", "negotiation", "won", "lost"],
    "out_of_scope_keywords": [
        "website",
        "web app",
        "mobile app",
        "software",
        "saas",
        "crm system",
        "erp",
        "api integration",
        "app development",
        "it project",
    ],
    "property_keywords": [
        "house",
        "home",
        "apartment",
        "flat",
        "plot",
        "villa",
        "shop",
        "office",
        "commercial",
        "residential",
        "rent",
        "purchase",
        "buy",
        "sell",
        "lahore",
        "dha",
        "bahria",
        "gulberg",
        "johar town",
        "cantt",
        "model town",
        "askari",
        "wapda town",
        "garden town",
        "lake city",
        "park view",
    ],
}

_PROPERTY_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(4|five|5)\s*-?\s*bed(room)?\b", "4-5 bedroom house"),
    (r"\b(3|4|5)\s*-?bed\b", "multi-bedroom home"),
    (r"\bhouse\b", "house"),
    (r"\bflat\b|\bapartment\b", "apartment"),
    (r"\bplot\b", "plot"),
    (r"\bvilla\b", "villa"),
    (r"\bshop\b", "shop"),
    (r"\boffice\b", "office"),
    (r"\bcommercial\b", "commercial property"),
    (r"\bresidential\b", "residential property"),
]

_THREAD_ECHO_PHRASES: tuple[str, ...] = (
    "thank you for your prompt response",
    "thank you for your response",
    "i will begin searching",
    "i would be happy to assist",
    "to better assist you",
    "i will prioritize finding",
    "i understand the urgency",
)

_VAGUE_LOCATION_TERMS: tuple[str, ...] = (
    "there",
    "here",
    "my place",
    "my area",
    "near by",
    "nearabout",
    "near about",
    "near around",
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _domain_stage_rank(stage: str | None) -> int:
    if not stage:
        return 1
    stage = stage.lower().strip()
    stage_order = DOMAIN_PROFILE["stage_order"]
    if stage in stage_order:
        return stage_order.index(stage)
    if "proposal" in stage or "estimation" in stage:
        return 4
    if "consult" in stage:
        return 3
    if "qualified" in stage:
        return 2
    if "discovery" in stage or "requirements" in stage:
        return 1
    if "lost" in stage:
        return 7
    return 1


def _is_property_related(text: str) -> bool:
    lower = (text or "").lower()
    return any(keyword in lower for keyword in DOMAIN_PROFILE["property_keywords"])


def _is_out_of_scope(text: str) -> bool:
    lower = (text or "").lower()
    if any(keyword in lower for keyword in DOMAIN_PROFILE["property_keywords"]):
        return False
    return any(keyword in lower for keyword in DOMAIN_PROFILE["out_of_scope_keywords"])


def _extract_location(text: str) -> str | None:
    lower = text.lower()
    for keyword in (
        "dha",
        "bahria",
        "gulberg",
        "johar town",
        "cantt",
        "model town",
        "askari",
        "wapda town",
        "garden town",
        "lake city",
        "park view",
    ):
        if keyword in lower:
            return keyword.title()
    location_match = re.search(r"\b(?:in|at|near)\s+([A-Za-z][A-Za-z\s\-]{2,40})", text)
    if location_match:
        candidate = location_match.group(1).strip().rstrip(".,;:!?")
        candidate_lower = candidate.lower()
        if candidate_lower in _VAGUE_LOCATION_TERMS:
            return None
        if any(term in candidate_lower for term in _VAGUE_LOCATION_TERMS):
            return None
        if len(candidate.split()) > 5 and not any(
            keyword in candidate_lower for keyword in DOMAIN_PROFILE["property_keywords"]
        ):
            return None
        return candidate
    return None


def _extract_property_type(text: str) -> str | None:
    lower = text.lower()
    for pattern, label in _PROPERTY_TYPE_PATTERNS:
        if re.search(pattern, lower, flags=re.I):
            return label
    return None


def _extract_slot_state(user_text: str, contact: Contact) -> dict[str, Any]:
    text = (user_text or "").lower()
    slots: dict[str, Any] = {}
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    prev_slots = ext.get("slots", {}) if isinstance(ext, dict) else {}
    if isinstance(prev_slots, dict):
        slots.update(prev_slots)

    if contact.project_type and "project_type" not in slots:
        slots["project_type"] = contact.project_type
    if contact.budget and "budget_signal" not in slots:
        slots["budget_signal"] = contact.budget
    if contact.timeline and "timeline_signal" not in slots:
        slots["timeline_signal"] = contact.timeline

    property_type = _extract_property_type(text)
    if property_type:
        slots["project_type"] = property_type

    location = _extract_location(user_text)
    if location:
        slots["location"] = location

    if re.search(r"\b(?:buy|buying|purchase|purchasing|own|invest|investment)\b", text):
        slots["transaction_type"] = "buy"
    elif re.search(r"\b(?:rent|rental|lease|leasing)\b", text):
        slots["transaction_type"] = "rent"
    elif re.search(r"\b(?:sell|selling|list|listing)\b", text):
        slots["transaction_type"] = "sell"

    if re.search(r"\b(?:decision maker|decision-maker|final decision|final say|owner|family|spouse|partner|parents)\b", text):
        slots["decision_authority"] = user_text[:160]

    if re.search(r"\b(?:budget|pk r|pkr|rupees?|lakhs?|lac|crore|million|m)\b", text):
        slots["budget_signal"] = user_text[:200]

    if re.search(r"\b(?:week|weeks|month|months|quarter|deadline|asap|soon|immediately|urgent|within)\b", text):
        slots["timeline_signal"] = user_text[:200]

    if re.search(r"\b(?:bedroom|bed rooms?|parking|garage|garden|corner|park facing|main road|furnished|sqm|marla|kanal)\b", text):
        slots["must_have_features"] = user_text[:200]

    if re.search(r"\b(?:house|home|apartment|flat|plot|villa|shop|office|commercial|residential)\b", text) and "project_type" not in slots:
        slots["project_type"] = user_text[:160]

    return slots


def _merge_contact_state(contact: Contact, extracted_slots: dict[str, Any]) -> dict[str, Any]:
    merged = dict(extracted_slots)
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    prev_slots = ext.get("slots", {}) if isinstance(ext, dict) else {}
    if isinstance(prev_slots, dict):
        for key, value in prev_slots.items():
            if value and key not in merged:
                merged[key] = value

    for key, value in (
        ("project_type", contact.project_type),
        ("budget_signal", contact.budget),
        ("timeline_signal", contact.timeline),
    ):
        if value and key not in merged:
            merged[key] = value

    return merged


def _missing_fields_from_slots(slots: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _normalize_text(slots.get("budget_signal")):
        missing.append("budget")
    if not _normalize_text(slots.get("project_type")):
        missing.append("property_type")
    if not _normalize_text(slots.get("location")):
        missing.append("location")
    if not _normalize_text(slots.get("transaction_type")):
        missing.append("transaction_type")
    if not _normalize_text(slots.get("timeline_signal")):
        missing.append("timeline")
    # Note: decision_authority is inferred automatically, not asked
    return missing


def _fallback_next_question(slots: dict[str, Any], missing_fields: list[str], user_text: str) -> str:
    if _is_property_related(user_text) and not _normalize_text(slots.get("location")):
        return "Which area of Lahore are you focused on?"
    if not _normalize_text(slots.get("transaction_type")):
        return "Are you looking to buy, rent, or sell?"
    if not _normalize_text(slots.get("budget_signal")):
        return "What budget range are you working with?"
    if not _normalize_text(slots.get("timeline_signal")):
        return "When are you planning to move ahead?"
    return "Could you share a bit more detail so I can help you better?"


def _first_or_fallback_question(
    *,
    classification: MessageClassification | None,
    qualification_view: dict[str, Any],
    user_text: str,
) -> str:
    if classification and classification.next_best_questions:
        return classification.next_best_questions[0]
    slots = qualification_view.get("slots") if isinstance(qualification_view, dict) else {}
    missing_fields = qualification_view.get("missing_fields") if isinstance(qualification_view, dict) else []
    if isinstance(slots, dict):
        return _fallback_next_question(slots, missing_fields or [], user_text)
    return "Could you share a bit more detail so I can help you better?"


def _build_questionnaire(missing_fields: list[str]) -> str:
    """Generate a compact questionnaire when multiple core fields are missing."""
    if not missing_fields:
        return ""
    
    field_questions = {
        "transaction_type": "Are you looking to buy, sell, or rent?",
        "budget": "What budget range are you working with?",
        "location": "Which area of Lahore are you focused on?",
        "timeline": "When are you planning to move ahead?",
        "property_type": "What type of property are you interested in (house, apartment, plot, etc.)?",
    }
    
    questions = []
    for field in missing_fields[:4]:  # Limit to 4 questions max
        if field in field_questions:
            questions.append(field_questions[field])
    
    if not questions:
        return ""
    
    if len(questions) == 1:
        return questions[0]
    
    # Format as a compact numbered list
    questionnaire = "To help you better, could you please share:\n" + "\n".join(
        f"{i+1}. {q}" for i, q in enumerate(questions)
    )
    return questionnaire


def _build_qualification_view(user_text: str, contact: Contact) -> dict[str, Any]:
    extracted = _extract_slot_state(user_text, contact)
    slots = _merge_contact_state(contact, extracted)
    missing = _missing_fields_from_slots(slots)
    readiness = 0
    for key, weight in (
        ("project_type", 25),
        ("budget_signal", 25),
        ("transaction_type", 20),
        ("timeline_signal", 15),
        ("location", 15),
    ):
        if _normalize_text(slots.get(key)):
            readiness += weight
    readiness = min(readiness, 100)
    if readiness >= 75 and not missing:
        stage = "consultation"
    elif readiness >= 55 and len(missing) <= 2:
        stage = "qualified"
    elif readiness >= 35:
        stage = "discovery"
    else:
        stage = "discovery"
    return {
        "slots": slots,
        "missing_fields": missing,
        "next_best_questions": [],
        "readiness": readiness,
        "stage": stage,
    }


def _latest_user_text(user_text: str, email_subject: str | None = None) -> str:
    body = _normalize_text(user_text)
    subject = _normalize_text(email_subject)
    if body:
        return body
    return subject


def _is_thread_echo_reply(reply_text: str, user_text: str) -> bool:
    reply = (reply_text or "").lower()
    if not reply:
        return True
    if any(phrase in reply for phrase in _THREAD_ECHO_PHRASES):
        return True
    latest = (user_text or "").lower()
    if "prompt response" in reply and "prompt response" not in latest:
        return True
    return False


def _build_fallback_reply(
    *,
    contact_name: str | None,
    next_best_question: str,
    missing_fields: list[str],
    booking_link_allowed: bool,
    booking_url: str,
    use_questionnaire: bool = False,
) -> str:
    name = _normalize_text(contact_name)
    greeting = f"Hi {name}," if name else "Hi there,"
    if not next_best_question:
        next_best_question = "Could you share a bit more detail so I can help you better?"
    if use_questionnaire and len(missing_fields) >= 3:
        body = next_best_question
    elif "location" in (missing_fields or []):
        body = next_best_question
    else:
        body = next_best_question
    reply = f"{greeting}\n\nUnderstood. {body}"
    if booking_link_allowed and booking_url:
        reply += f"\n\nBooking link: {booking_url}"
    return reply


def should_include_cta(
    *,
    conversation_turn: int,
    cta_readiness_score: float | int | None,
    missing_fields: list[str] | None,
    conversation_stage: str | None,
) -> bool:
    stage_rank = _domain_stage_rank(conversation_stage)
    score = float(cta_readiness_score or 0)
    missing = set(missing_fields or [])

    if stage_rank >= 3:
        return True
    return score >= 80 and len(missing) <= 1

class AgentState(TypedDict, total=False):
    # Inputs
    user_text: str
    email_subject: str
    is_proactive: bool
    channel: str
    json_snapshot: str
    booking_url: str
    booking_links: list[dict[str, str]]
    conversation_turn: int
    recent_context: str
    contact_id: int
    conversation_id: int
    contact_name: str
    agent_name: str
    qualification_view: dict[str, Any]
    slot_context: str
    
    # Typed Outputs
    classification: MessageClassification | None
    rag_chunks: list[dict] | None
    rag_context: str
    rag_kb_ids: list[int]
    generated_reply: str | None
    error: str

PERSONA_POLICIES: dict[str, dict[str, str]] = {
    "buyer": {
        "focus": "qualify the buyer's budget, preferred location, property type, timing, and who should be involved",
        "value": "we help buyers find the right Lahore property options and move quickly to a consultation when they are ready",
        "questions": "budget range, preferred area in Lahore, property type, timeline, who should be involved, must-have features",
        "cta": "If the lead is ready, offer a consultation and share the booking link",
    },
    "seller": {
        "focus": "understand the asset, asking price, location, timeline, and who should be included",
        "value": "we help sellers position and move property efficiently with a professional response flow",
        "questions": "property type, location, asking price, desired selling timeline, who should be included",
        "cta": "If details are clear, offer consultation or human handoff",
    },
    "landlord": {
        "focus": "qualify the rental property, expected rent, location, occupancy timeline, and who should be involved",
        "value": "we help landlords handle rental inquiries and move qualified conversations forward",
        "questions": "property type, location, rent target, availability timeline, who should be involved",
        "cta": "If the lead is ready, move toward a consultation or booking link",
    },
    "tenant": {
        "focus": "understand rental budget, area, lease timeline, and property requirements",
        "value": "we help tenants find suitable rental options in Lahore without wasting their time",
        "questions": "budget, area, move-in date, bedrooms, and must-have features",
        "cta": "If qualified, offer a consultation or send suitable options",
    },
    "investor": {
        "focus": "identify yield goals, budget, area preference, holding period, and readiness",
        "value": "we help investors evaluate property opportunities that match their timeline and return goals",
        "questions": "budget, area, goal, holding period, and who should be involved",
        "cta": "If qualified, propose consultation",
    },
    "agent": {
        "focus": "determine if they have a client needing property support or want a referral partnership",
        "value": "we help agents move client opportunities faster and keep communication professional",
        "questions": "client requirement, area, budget, timeline, and who should be involved",
        "cta": "If qualified, move toward consultation or partnership discussion",
    },
    "developer": {
        "focus": "assess project scope, stakeholders, location, budget, and deployment timeline",
        "value": "we help developers and property teams qualify opportunities and coordinate next steps",
        "questions": "project type, budget, timeline, stakeholders, and area",
        "cta": "If qualified, propose a consultation",
    },
    "contractor": {
        "focus": "retain compatibility with legacy leads while still qualifying property-related needs",
        "value": "we support contractors involved in property work and related project needs",
        "questions": "project type, budget, timeline, and location",
        "cta": "If qualified, move toward consultation",
    },
    "architect": {
        "focus": "retain compatibility with legacy leads while still qualifying property-related needs",
        "value": "we support architects involved in property projects",
        "questions": "project type, budget, timeline, and location",
        "cta": "If qualified, move toward consultation",
    },
    "builder": {
        "focus": "retain compatibility with legacy leads while still qualifying property-related needs",
        "value": "we support builders involved in property projects",
        "questions": "project type, budget, timeline, and location",
        "cta": "If qualified, move toward consultation",
    },
    "unknown": {
        "focus": "identify the buyer, seller, landlord, tenant, or investor role quickly and politely",
        "value": "we help with Lahore property conversations and move serious leads to the right next step",
        "questions": "budget, location, property type, timeline, and who should be involved",
        "cta": "If in-scope and clear, guide to consultation; otherwise request clarification politely",
    },
}

DECISION_PROMPT = """You are a Lahore property qualification engine.

Goal:
- classify the incoming message
- extract dynamic BANT-style property signals from arbitrary phrasing
- decide the safest next stage
- decide whether the lead should be booked, qualified, or escalated

You must output ONLY valid JSON.

Use this domain profile:
{domain_profile}

Observed contact state:
{slot_context}

Conversation context:
{recent_context}

FAQ / knowledge hints:
{rag_context}

User message:
{user_text}

Email subject:
{email_subject}

Output schema:
- role_type
- intent_type
- relationship_type
- decision_role
- engagement_temperature
- qualification_stage
- is_escalated
- conversation_status
- lead_score
- is_qualified
- budget
- project_type
- timeline
- project_scope
- decision_authority
- geography
- transaction_type
- must_have_features
- out_of_scope
- new_pipeline_stage
- close_readiness_score
- requires_action
- missing_qualification_fields
- filled_fields
- next_best_questions

Rules:
- Treat Lahore property as the default domain.
- Support any natural phrasing; do not rely on scripted example wording.
- Extract BANT-style signals: budget, authority, need/property type, timing, location, transaction type, and must-have features.
- Choose the single best next question dynamically from the current turn and slot state.
- Do not follow a fixed question order or ladder.
- Do not require must-have features before qualifying the lead.
- If buy/rent/sell is unclear, ask that when it blocks the next step.
- If the user asks something that can be answered from the knowledge base, keep the answer grounded and continue qualification.
- If the message is clearly software/web/app/IT related, mark it out of scope, set is_qualified=false, and move toward lost or closed.
- If budget, property type, and timeline are present but location is missing, stay in discovery or qualified until location is known.
- If the lead is ready and asks next steps, booking, consultation, or pricing, move toward consultation and include send_calendly when appropriate.
- Only move to proposal_ready when scope and decision authority/stakeholders are clear.
- Do not skip stages.
- Decision Authority: AUTOMATICALLY INFER from context:
  * If user mentions "family", "partner", "spouse", "parents", "we" → set decision_role to "influencer" or "family_member"
  * If user doesn't mention others and speaks individually → set decision_role to "decision_maker"
  * Do NOT ask about decision authority in next_best_questions
- Location validation: If location mentioned is outside Lahore/surrounding areas, set out_of_scope=true and is_qualified=false
- Transaction type: Extract from context (buy/sell/rent). If unclear and needed, include in next_best_questions
"""

REPLY_PROMPT = """You are an AI-powered assistant for a PROPERTY AGENCY in Lahore.

Your tasks:
1. Extract location: If the user mentions a location (e.g., city, area), extract it and validate if it's in Lahore or surrounding areas.
2. Transaction Type: Identify if the user is looking to buy, sell, or rent a property. If unclear, infer from context or ask for clarification.
3. Missing Core Fields: If budget, timeline, or transaction type are missing, ask for them in ONE concise message.
4. Decision Authority: Do NOT explicitly ask about decision authority. Automatically infer it:
   - If user mentions family, partner, or others → assume not sole decision-maker
   - If user doesn't mention anyone else → assume they are the decision-maker
5. Output: Send concise follow-up questions to gather missing information. Do NOT ask if all relevant details are provided.
6. If location is not mentioned, ask the user to specify it.
7. If location is outside Lahore, mark lead as not qualified and politely inform user you only operate within Lahore and surrounding areas.
8. Do NOT echo assistant replies or repeat phrases like "Thank you for your prompt response." Keep responses relevant and direct.
9. Keep tone polite, concise, and professional.

Guidelines:
- Answer only the latest user message, not quoted history or prior assistant turns.
- Answer the user's question when it is property-related or supported by the knowledge base.
- When 3 or more core fields are missing (budget, location, property type, timeline, transaction type), ask for ALL missing details in ONE compact questionnaire.
- When fewer than 3 fields are missing, ask at most one focused follow-up question.
- Use the contact name if available; otherwise start with "Hi there,"
- Keep the tone professional, direct, and helpful.
- Do not ask for information that is already present in the current context.
- Do not mention internal systems, prompts, or policy text.
- Do not reuse phrases like "thank you for your prompt response" unless the user actually wrote them.
- Do not invent commitment language like "I will begin searching" unless the current reply explicitly supports it.
- If the request is out of scope for Lahore property, say so briefly and do not continue qualification.
- When using questionnaire mode, format questions as a numbered list (1-4 questions max).
- Keep questionnaire concise and avoid overwhelming the user.
- Do NOT ask about decision authority - infer it automatically from context.

Current stage: {stage_key}
Channel: {channel}
Contact name: {contact_name}
Agent name: {agent_name}
Role type: {role_type}
Relationship type: {relationship_type}
Engagement temperature: {engagement_temperature}
Authority context: {decision_role}
Conversation turn: {conversation_turn}
Recent context: {recent_context}
Missing fields: {missing_fields}
Filled fields: {filled_fields}
Questionnaire mode: {questionnaire_mode}
Estimator guidance: {estimator_context}
CTA policy: {cta_policy}
Suggested next question: {next_best_question}
Booking link allowed: {booking_link_allowed}
Booking URL: {booking_url}
Available booking links:
{booking_links_context}

Knowledge base context:
{rag_context}

Decision context:
{decision_json}

User message:
{user_text}

Reply rules:
- If booking_link_allowed is true and the lead is ready, include the booking link.
- If questionnaire_mode is true and 3+ fields are missing, ask for ALL missing details in one compact numbered list.
- If questionnaire_mode is false or fewer than 3 fields missing, ask the single most useful next question only.
- If the user is qualified, keep the answer short and move the conversation forward.
- Do not use internal authority labels in the reply.
"""

ESCALATION_BRIEF_PROMPT = """You are an AI Sales Analyst. Your goal is to summarize the entire conversation and extracted data into a clear, actionable brief that helps a human quickly understand the context of a lead escalation.

Your output MUST follow this EXACT format and use the provided data. If any field is unknown, write "Not specified". Do NOT include placeholders.

---

Lead Summary:
- Name: {contact_name}
- Role / Persona: {role_type}
- Relationship Type: {relationship_type}
- Decision Role: {decision_role}
- Engagement Level: {engagement_temperature}

---

Project / Requirement:
- Project Type: {project_type}
- Scope: {project_scope}
- Budget: {budget}
- Timeline: {timeline}
- Location: {geography}

---

Qualification Status:
- Current Stage: {stage_key}
- Qualification Status: {qualification_stage}
- Lead Score: {lead_score}
- Close Readiness: {close_readiness_score}

---

Key Signals:
- Buying Intent: {buying_intent_summary}
- Urgency Level: {urgency_level}
- Constraints or Risks: {constraints_risks}

---

Conversation Highlights:
{conversation_highlights}

---

Missing Information:
{missing_info_list}

---

Recommended Next Action:
{recommended_action}

---

AI Notes (Internal):
- {ai_internal_notes}

-----------------------------
DATA CONTEXT:
Classification: {classification_json}
Recent Context: {recent_context}
User Message: {user_text}
-----------------------------

Instructions for fields:
- Buying Intent: summarize briefly based on user language (1-2 lines)
- Urgency Level: low / medium / high (based on timeline and language)
- Constraints or Risks: mention missing info, unclear budget, or hesitation signals
- Conversation Highlights: 3-5 bullet points focusing on decisions, preferences, and important statements. Avoid generic phrases.
- Missing Information: Bulleted list of critical missing fields.
- Recommended Next Action: Suggest ONE clear action (e.g., book consultation, send proposal, continue qualification, escalate to senior team).
- AI Notes (Internal): Mention why escalation happened and any inconsistencies/risks.
"""
def _safe_list(value: Any) -> list[str]:
    return _safe_str_list(value)


def _build_recent_context(session: Session, conversation_id: int, limit: int = 4) -> str:
    msgs = list(
        session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.sender_type == "client")
            .order_by(Message.id.desc())
            .limit(limit)
        ).all()
    )
    msgs.reverse()
    parts: list[str] = []
    for m in msgs:
        parts.append(f"{m.sender_type}: {m.message[:200]}")
    return " | ".join(parts)


def _wants_estimate(user_text: str) -> bool:
    text = (user_text or "").lower()
    return any(
        token in text
        for token in (
            "budget",
            "cost",
            "price",
            "pricing",
            "how much",
            "estimate",
            "timeline",
            "how long",
            "monthly",
            "rent",
            "purchase",
        )
    )


def _format_estimator_context(user_text: str, slots: dict[str, Any]) -> str:
    est = estimate_project(user_text, slots)
    assumptions = "; ".join(est.get("assumptions", []))
    return (
        "Use these indicative ranges if the lead asks for pricing or timeline guidance: "
        f"MVP {est.get('mvp_budget_range')} ({est.get('mvp_timeline')}), "
        f"Standard {est.get('standard_budget_range')} ({est.get('standard_timeline')}), "
        f"Advanced {est.get('advanced_budget_range')} ({est.get('advanced_timeline')}). "
        f"Assumptions: {assumptions}."
    )


def build_graph(session: Session):
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY required")

    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_CHAT_MODEL,
        temperature=0.2,
    )

    def node_classify(state: AgentState) -> Command:
        contact_id = state.get("contact_id")
        contact = session.get(Contact, contact_id) if contact_id else None
        qualification_view = state.get("qualification_view") or {}
        if not qualification_view and contact:
            qualification_view = _build_qualification_view(state.get("user_text", ""), contact)
        slot_context = json.dumps(qualification_view, default=str)[:12000]

        user_text = state.get("user_text", "")
        email_subject = state.get("email_subject", "")
        if _is_out_of_scope(user_text):
            classification = MessageClassification(
                role_type="unknown",
                intent_type="support_request",
                relationship_type="inbound_lead",
                decision_role="unknown",
                engagement_temperature="cold",
                qualification_stage="not_qualified",
                is_escalated=False,
                conversation_status="closed",
                lead_score=0.0,
                is_qualified=False,
                budget=None,
                project_type=None,
                timeline=None,
                project_scope=None,
                decision_authority=None,
                geography=None,
                transaction_type=None,
                must_have_features=[],
                out_of_scope=True,
                new_pipeline_stage="lost",
                close_readiness_score=0.0,
                requires_action=["update_status"],
                missing_qualification_fields=[],
                filled_fields=[],
                next_best_questions=[],
            )
            return Command(update={"classification": classification, "qualification_view": qualification_view, "slot_context": slot_context}, goto="draft")

        # Fetch dynamic stages from DB
        stages = list(session.exec(select(PipelineStage).order_by(PipelineStage.order_index)).all())
        valid_keys = ", ".join([s.key for s in stages])
        structured_llm = llm.with_structured_output(MessageClassification)
        prompt = DECISION_PROMPT.format(
            domain_profile=json.dumps(DOMAIN_PROFILE, indent=2),
            json_snapshot=state.get("json_snapshot", "{}"),
            recent_context=state.get("recent_context", ""),
            slot_context=slot_context,
            rag_context=state.get("rag_context", ""),
            user_text=user_text,
            email_subject=email_subject,
        )
        prompt += f"\n\nAVAILABLE PIPELINE STAGES:\n{valid_keys}\nRule: new_pipeline_stage MUST be one of these values."

        try:
            classification = structured_llm.invoke([HumanMessage(content=prompt)])
            if contact and qualification_view:
                slots = qualification_view.get("slots") or {}
                if not classification.budget and slots.get("budget_signal"):
                    classification.budget = str(slots.get("budget_signal"))[:2000]
                if not classification.project_type and slots.get("project_type"):
                    classification.project_type = str(slots.get("project_type"))[:2000]
                if not classification.timeline and slots.get("timeline_signal"):
                    classification.timeline = str(slots.get("timeline_signal"))[:2000]
                if not classification.project_scope and slots.get("must_have_features"):
                    mf = slots.get("must_have_features")
                    classification.project_scope = ", ".join(_safe_str_list(mf)) if isinstance(mf, list) else str(mf)[:2000]
                if not classification.decision_authority and slots.get("decision_authority"):
                    classification.decision_authority = str(slots.get("decision_authority"))[:2000]
                if not classification.geography and slots.get("location"):
                    classification.geography = str(slots.get("location"))[:2000]
                if not classification.transaction_type and slots.get("transaction_type"):
                    classification.transaction_type = str(slots.get("transaction_type"))[:2000]
                if not classification.must_have_features:
                    classification.must_have_features = _safe_str_list(slots.get("must_have_features"))
                if not classification.missing_qualification_fields:
                    classification.missing_qualification_fields = qualification_view.get("missing_fields", [])
                if not classification.close_readiness_score:
                    classification.close_readiness_score = float(qualification_view.get("readiness", 0))
                if not classification.new_pipeline_stage:
                    classification.new_pipeline_stage = str(qualification_view.get("stage", "discovery"))
                if not classification.lead_score:
                    classification.lead_score = float(qualification_view.get("readiness", 0))
                if classification.new_pipeline_stage not in {s.key for s in stages}:
                    classification.new_pipeline_stage = "discovery"
                if classification.new_pipeline_stage in {"qualified", "consultation", "proposal_ready"} and "send_calendly" not in (classification.requires_action or []):
                    classification.requires_action = list(classification.requires_action or []) + ["send_calendly"]
            _structured_log(
                "AI classification",
                conversation_id=state.get("conversation_id"),
                contact_id=contact_id,
                role_type=classification.role_type,
                intent_type=classification.intent_type,
                decision_role=classification.decision_role,
                qualification_stage=classification.qualification_stage,
                new_pipeline_stage=classification.new_pipeline_stage,
                readiness=classification.close_readiness_score,
                lead_score=classification.lead_score,
                is_qualified=classification.is_qualified,
                requires_action=classification.requires_action,
                missing_fields=classification.missing_qualification_fields,
                filled_fields=classification.filled_fields,
                out_of_scope=classification.out_of_scope,
                slot_keys=sorted(list((qualification_view.get("slots") or {}).keys())),
            )
            if classification.is_escalated:
                return Command(update={"classification": classification, "qualification_view": qualification_view, "slot_context": slot_context}, goto="human_review")
            elif "update_status" in (classification.requires_action or []):
                return Command(update={"classification": classification, "qualification_view": qualification_view, "slot_context": slot_context}, goto="update_status")
            else:
                return Command(update={"classification": classification, "qualification_view": qualification_view, "slot_context": slot_context}, goto="retrieve")
        except Exception as e:
            logger.error(f"Classification error: {e}")
            return Command(update={"qualification_view": qualification_view, "slot_context": slot_context}, goto="retrieve")

    def node_retrieve(state: AgentState) -> Command:
        q = state.get("user_text", "")[:8000]
        routed_category = detect_category(q)
        chunks, kb_ids = search_similar(
            session, q, top_k=5, min_score=0.35, category=routed_category,
        )
        if _rag_debug_enabled():
            logger.info(
                "RAG debug: conversation_id=%s contact_id=%s category=%s kb_ids=%s",
                state.get("conversation_id"),
                state.get("contact_id"),
                routed_category,
                kb_ids,
            )
            if chunks:
                logger.info(
                    "RAG debug: top_matches=%s",
                    [
                        {
                            "id": c.id,
                            "score": round(float(c.score), 3),
                            "category": c.category,
                            "intent_type": c.intent_type,
                            "role_type": c.role_type,
                            "question": (c.question or "")[:120],
                        }
                        for c in chunks[:5]
                    ],
                )
        _structured_log(
            "AI retrieval",
            conversation_id=state.get("conversation_id"),
            contact_id=state.get("contact_id"),
            query_preview=_preview_text(q),
            detected_category=routed_category,
            matched=bool(chunks),
            top_matches=[
                {"id": c.id, "score": round(float(c.score), 3), "category": c.category}
                for c in chunks[:3]
            ],
            matched_kb_ids=kb_ids[:5],
        )
        rag_ctx = format_rag_context(chunks)
        return Command(
            update={"rag_context": rag_ctx, "rag_kb_ids": kb_ids},
            goto="draft"
        )

    def node_update_status(state: AgentState) -> Command:
        classification = state.get("classification")
        if classification and classification.new_pipeline_stage:
            contact_id = state.get("contact_id")
            if contact_id:
                contact = session.get(Contact, contact_id)
                if contact:
                    # Update both the string field and the foreign key
                    contact.pipeline_stage = classification.new_pipeline_stage
                    
                    # Find the stage record to link by ID
                    stage_record = session.exec(
                        select(PipelineStage).where(PipelineStage.key == classification.new_pipeline_stage)
                    ).first()
                    if stage_record:
                        contact.pipeline_stage_id = stage_record.id
                        
                    session.add(contact)
                    session.commit()
                    logger.info(f"Updated contact {contact_id} stage to {classification.new_pipeline_stage}")
        return Command(goto="retrieve")

    def node_draft(state: AgentState) -> Command:
        classification = state.get("classification")
        qualification_view = state.get("qualification_view") or {}
        stage_key = classification.new_pipeline_stage if classification else qualification_view.get("stage", "discovery")
        email_subject = state.get("email_subject", "")

        # Format booking links context from state
        booking_links = state.get("booking_links", [])
        booking_links_context = ""
        if booking_links:
            booking_links_context = "\nAvailable booking links:\n" + json.dumps(booking_links, indent=2)
        
        # Fetch instructions from DB for the chosen stage
        stage_record = session.exec(
            select(PipelineStage).where(PipelineStage.key == stage_key)
        ).first()
        stage_instruction = stage_record.ai_instructions if stage_record else ""

        persona_segment = classification.role_type if classification and classification.role_type in PERSONA_POLICIES else "unknown"
        if persona_segment not in PERSONA_POLICIES:
            persona_segment = "unknown"
        persona_policy = PERSONA_POLICIES[persona_segment]
        missing_fields = classification.missing_qualification_fields if classification else qualification_view.get("missing_fields", [])
        
        # Determine if we should use questionnaire mode (3+ missing fields)
        use_questionnaire = len(missing_fields) >= 3
        questionnaire_mode = "true" if use_questionnaire else "false"
        
        # Generate questionnaire text if needed
        if use_questionnaire:
            next_best_question = _build_questionnaire(missing_fields)
            if not next_best_question:
                next_best_question = _first_or_fallback_question(
                    classification=classification,
                    qualification_view=qualification_view,
                    user_text=state.get("user_text", ""),
                )
        else:
            next_best_question = _first_or_fallback_question(
                classification=classification,
                qualification_view=qualification_view,
                user_text=state.get("user_text", ""),
            )
        readiness = classification.close_readiness_score if classification else qualification_view.get("readiness", 0)
        booking_link_allowed = should_include_cta(
            conversation_turn=state.get("conversation_turn", 1),
            cta_readiness_score=readiness,
            missing_fields=missing_fields,
            conversation_stage=stage_key,
        )

        if classification and getattr(classification, "out_of_scope", False):
            reply = (
                f"Hi there, this request is outside our Lahore property scope. "
                f"If you need help with property-related matters in Lahore, I can help."
            )
            return Command(update={"generated_reply": reply}, goto=END)

        prompt = REPLY_PROMPT.format(
            channel=state.get("channel", "website"),
            contact_name=state.get("contact_name", "there"),
            agent_name=state.get("agent_name", "StrategistHub"),
            stage_key=stage_key,
            relationship_type=classification.relationship_type if classification else "inbound_lead",
            engagement_temperature=classification.engagement_temperature if classification else "warm",
            decision_role=classification.decision_role if classification else "unknown",
            rag_context=state.get("rag_context", ""),
            decision_json=json.dumps(classification.model_dump()) if classification else "{}",
            user_text=state.get("user_text", ""),
            email_subject=email_subject,
            booking_url=state.get("booking_url", ""),
            booking_links_context=booking_links_context,
            role_type=persona_segment,
            persona_policy=json.dumps(persona_policy),
            conversation_turn=state.get("conversation_turn", 1),
            recent_context=state.get("recent_context", ""),
            missing_fields=", ".join(missing_fields) if missing_fields else "",
            filled_fields=state.get("filled_fields", ""),
            questionnaire_mode=questionnaire_mode,
            estimator_context=state.get("estimator_context", ""),
            cta_policy=stage_instruction,
            next_best_question=next_best_question,
            booking_link_allowed=str(booking_link_allowed).lower(),
        )
        
        out = llm.invoke([
            SystemMessage(content="You write clear, business-professional client replies. No markdown code fences."),
            HumanMessage(content=prompt)
        ])
        reply = out.content if hasattr(out, "content") else str(out)
        if _is_thread_echo_reply(reply, state.get("user_text", "")):
            reply = _build_fallback_reply(
                contact_name=state.get("contact_name", "there"),
                next_best_question=next_best_question,
                missing_fields=missing_fields,
                booking_link_allowed=booking_link_allowed,
                booking_url=state.get("booking_url", ""),
                use_questionnaire=use_questionnaire,
            )
        include_booking_link = bool(
            booking_link_allowed
            and state.get("booking_url")
            and "http" not in reply.lower()
            and "booking" not in reply.lower()
        )
        if include_booking_link:
            reply = reply.rstrip() + f"\n\nBooking link: {state.get('booking_url')}"
        _structured_log(
            "AI reply",
            conversation_id=state.get("conversation_id"),
            contact_id=state.get("contact_id"),
            stage=stage_key,
            readiness=readiness,
            missing_fields=missing_fields,
            next_best_question=next_best_question,
            booking_link_allowed=booking_link_allowed,
            booking_link_included=include_booking_link,
            questionnaire_mode=use_questionnaire,
            reply_mode=(
                "booking_link"
                if include_booking_link
                else ("questionnaire" if use_questionnaire else ("qualification_question" if missing_fields else "answer"))
            ),
            reply_preview=_preview_text(reply),
        )
        return Command(update={"generated_reply": reply.strip()}, goto=END)

    def node_human_review(state: AgentState) -> Command:
        classification = state.get("classification")
        conversation_id = state.get("conversation_id")
        
        logger.info(f"Node human_review: conversation={conversation_id}, is_escalated={classification.is_escalated if classification else 'None'}")
        
        if classification and classification.is_escalated and conversation_id:
            try:
                conv = session.get(Conversation, conversation_id)
                if conv:
                    logger.info("Generating escalation brief...")
                    cl_dict = classification.model_dump()
                    
                    # Fetch recent messages for context
                    history_text = "\n".join([f"{m.type}: {m.content}" for m in state.get("messages", [])[-10:]])
                    
                    prompt = ESCALATION_BRIEF_PROMPT.format(
                        contact_name=state.get("contact_name") or "Not specified",
                        role_type=cl_dict.get("role_type") or "Not specified",
                        relationship_type=cl_dict.get("relationship_type") or "Not specified",
                        decision_role=cl_dict.get("decision_role") or "Not specified",
                        engagement_temperature=cl_dict.get("engagement_temperature") or "Not specified",
                        project_type=cl_dict.get("project_type") or "Not specified",
                        project_scope=cl_dict.get("project_scope") or "Not specified",
                        budget=cl_dict.get("budget") or "Not specified",
                        timeline=cl_dict.get("timeline") or "Not specified",
                        geography=cl_dict.get("geography") or "Not specified",
                        stage_key=cl_dict.get("new_pipeline_stage") or "Not specified",
                        qualification_stage=cl_dict.get("qualification_stage") or "Not specified",
                        lead_score=cl_dict.get("lead_score") if cl_dict.get("lead_score") is not None else "Not specified",
                        close_readiness_score=cl_dict.get("close_readiness_score") if cl_dict.get("close_readiness_score") is not None else "Not specified",
                        buying_intent_summary="[Analyze from history]",
                        urgency_level="[Analyze from history]",
                        constraints_risks="[Analyze from history]",
                        conversation_highlights="[Summarize salient points]",
                        missing_info_list="[Identify gaps]",
                        recommended_action="[What should human do next?]",
                        ai_internal_notes="[Your internal reasoning]",
                        classification_json=json.dumps(cl_dict),
                        recent_context=state.get("recent_context") or "Not specified",
                        user_text=state.get("user_text") or "Not specified",
                        conversation_history=history_text
                    )
                    
                    logger.info("Invoking LLM for brief...")
                    summary_out = llm.invoke([
                        SystemMessage(content="You are a professional sales analyst summarizing a lead escalation."),
                        HumanMessage(content=prompt)
                    ])
                    brief_content = summary_out.content if hasattr(summary_out, "content") else str(summary_out)
                    
                    # Update conversation record
                    conv.is_escalated = True
                    conv.status = "pending_human"
                    conv.escalation_brief = brief_content.strip()
                    session.add(conv)
                    
                    # Also add as a system message for UI visibility
                    system_msg = Message(
                        conversation_id=conv.id,
                        conversation_public_uuid=conv.public_uuid,
                        sender_type="agent",
                        message_type="brief",
                        message=f"### INTERNAL ESCALATION BRIEF\n\n{conv.escalation_brief}",
                        channel=state.get("channel", "website"),
                        is_generated=True,
                        is_handled=True,
                    )
                    session.add(system_msg)
                    session.commit()
                    logger.info(f"Escalated conversation {conversation_id} and saved brief.")
            except Exception:
                logger.exception("Error in node_human_review while generating brief")
        
        interrupt("Human intervention required. Escalated by AI.")
        return Command(goto=END)

    g = StateGraph(AgentState)
    g.add_node("classify", node_classify)
    g.add_node("retrieve", node_retrieve)
    g.add_node("update_status", node_update_status)
    g.add_node("draft", node_draft)
    g.add_node("human_review", node_human_review)
    
    g.set_entry_point("classify")
    
    return g.compile(
        checkpointer=get_checkpointer(),
    )


async def run_ai_pipeline(
    session: Session,
    conversation: Conversation,
    contact: Contact,
    inbound_message: Message | None = None,
    json_response: dict | None = None,
    inbound_subject: str | None = None,
) -> Message:
    """Runs graph, updates contact/conversation, creates outbound Message."""
    is_proactive = inbound_message is None
    if is_proactive:
        user_text = "(System nudge: Lead has been silent. Re-engage politely based on prior context.)"
    else:
        user_text = _latest_user_text(inbound_message.message, inbound_subject)

    from datetime import datetime

    if _is_out_of_scope(user_text):
        reply_text = (
            "Hi there, this request is outside our Lahore property scope. "
            "If you need help with property-related matters in Lahore, I can help."
        )
        outbound = Message(
            conversation_id=conversation.id,
            conversation_public_uuid=conversation.public_uuid,
            sender_type="agent",
            sender_id=None,
            message_type="text",
            message=reply_text,
            channel=conversation.channel,
            is_generated=True,
            is_handled=True,
            rag_source_kb_ids=[],
        )
        conversation.last_intent = "out_of_scope"
        conversation.is_escalated = False
        conversation.status = "closed"
        conversation.updated_at = datetime.utcnow()
        contact.updated_at = datetime.utcnow()
        session.add(conversation)
        session.add(contact)
        session.add(outbound)
        session.commit()
        session.refresh(outbound)
        return outbound

    # Fetch booking links if Calendly is configured
    booking_links = []
    booking_url = ""
    try:
        from app.services.integrations.calendly_client import CalendlyClient
        calendly = CalendlyClient(session)
        dynamic_url = await calendly.get_booking_url("30min")
        if dynamic_url:
            booking_url = dynamic_url
            booking_links.append({"name": "30min Consultation", "url": dynamic_url})
        else:
            booking_url = "https://calendly.com/sajjad_hussain-strategisthub/30min"  # Fallback
    except Exception as e:
        logger.warning(f"Could not fetch Calendly booking URL: {e}")
        booking_url = "https://calendly.com/sajjad_hussain-strategisthub/30min"  # Fallback

    json_response = json_response or {}
    channel = conversation.channel
    conversation_turn = len(
        list(
            session.exec(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .where(Message.sender_type == "client")
            ).all()
        )
    )
    recent_context = _build_recent_context(session, conversation.id, limit=4)
    qualification_view = _build_qualification_view(user_text, contact)
    slots = qualification_view["slots"]
    estimator_context = _format_estimator_context(user_text, slots) if _wants_estimate(user_text) else ""

    contact_name = contact.username or "there"
    if "<" in contact_name and ">" in contact_name:
        contact_name = contact_name.split("<")[0].strip()

    # Persist extracted qualification state before any graph/LLM work so
    # follow-up logic survives transient model or network failures.
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    ext["slots"] = slots
    ext["qualification_view"] = qualification_view
    ext["ai_state"] = {
        "conversation_stage": qualification_view.get("stage", "discovery"),
        "missing_fields": qualification_view.get("missing_fields", []),
        "filled_fields": _safe_list(slots.keys()),
        "next_action": "ask_missing_info",
    }
    contact.external_ids = dict(ext)
    flag_modified(contact, "external_ids")
    contact.updated_at = datetime.utcnow()
    session.add(contact)
    session.commit()
    session.refresh(contact)

    _structured_log(
        "AI inbound",
        conversation_id=conversation.id,
        contact_id=contact.id,
        channel=channel,
        user_preview=_preview_text(user_text),
        conversation_turn=max(1, conversation_turn),
        readiness=qualification_view.get("readiness", 0),
        stage=qualification_view.get("stage", "discovery"),
        missing_fields=qualification_view.get("missing_fields", []),
        slot_keys=sorted(list(slots.keys())),
    )

    graph = build_graph(session)
    initial: AgentState = {
        "user_text": user_text,
        "email_subject": inbound_subject or "",
        "is_proactive": is_proactive,
        "channel": channel,
        "json_snapshot": json.dumps(json_response, default=str)[:12000],
        "booking_url": booking_url,
        "booking_links": booking_links,
        "conversation_turn": max(1, conversation_turn),
        "recent_context": recent_context,
        "filled_fields": ", ".join(sorted(slots.keys())),
        "estimator_context": estimator_context,
        "qualification_view": qualification_view,
        "slot_context": json.dumps(qualification_view, default=str)[:12000],
        "contact_id": contact.id,
        "conversation_id": conversation.id,
        "contact_name": contact_name,
        "agent_name": settings.AGENT_NAME,
    }
    config = {"configurable": {"thread_id": str(conversation.id)}}
    
    # Execute graph
    try:
        final = graph.invoke(initial, config)
    except Exception as e:
        logger.error(f"AI graph failed; using fallback response: {e}")
        fallback_reply = (
            "Thanks for sharing that. "
            f"{_fallback_next_question(qualification_view.get('slots', {}), qualification_view.get('missing_fields', []), user_text)}"
        )
        final = {
            "classification": None,
            "generated_reply": fallback_reply,
            "rag_kb_ids": [],
        }

    classification = final.get("classification")
    
    # Update conversation based on classification
    if classification:
        conversation.last_intent = str(classification.intent_type)[:500]
        conversation.is_escalated = bool(classification.is_escalated)
        conversation.status = str(classification.conversation_status or "open")[:100]
        conversation.qualification_stage = str(classification.qualification_stage or qualification_view.get("stage") or "discovery")[:100]
        
        # Redundant sync for safety
        sync_pipeline_stage(session, contact, classification.model_dump())

        if classification.new_pipeline_stage:
            contact.pipeline_stage = classification.new_pipeline_stage
            
        if classification.lead_score is not None:
            try:
                contact.lead_score = Decimal(str(classification.lead_score))
            except Exception:
                pass
        if classification.is_qualified is not None:
            contact.is_qualified = bool(classification.is_qualified)
        
        # Update contact fields that are persisted on the Contact row.
        for fld in ("budget", "project_type", "timeline"):
            v = getattr(classification, fld)
            if v is not None and str(v).strip():
                setattr(contact, fld, str(v)[:2000])
        if classification.must_have_features:
            contact.tags = list(dict.fromkeys((contact.tags or []) + ["must_have_features"]))

    conversation.updated_at = datetime.utcnow()

    # Governance & Metrics
    next_action = "ask_missing_info"
    if classification:
        if "send_calendly" in classification.requires_action:
            next_action = "book_consultation"
        elif "send_proposal" in classification.requires_action:
            next_action = "send_proposal"
            
    if not validate_action(contact, next_action):
        logger.warning(f"Governance restricted AI from taking action: {next_action}")
        next_action = "ask_missing_info"

    if next_action == "book_consultation":
        record_outcome(session, contact.id, "booking_intent")

    _structured_log(
        "AI turn_result",
        conversation_id=conversation.id,
        contact_id=contact.id,
        role_type=getattr(classification, "role_type", None),
        intent_type=getattr(classification, "intent_type", None),
        qualification_stage=getattr(classification, "qualification_stage", None),
        next_action=next_action,
        rag_kb_ids=final.get("rag_kb_ids") or [],
        generated_reply_preview=_preview_text(final.get("generated_reply") or ""),
    )

    # Persist lightweight state
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    if classification:
        ext["ai_state"] = {
            "conversation_stage": classification.new_pipeline_stage,
            "missing_fields": classification.missing_qualification_fields,
            "filled_fields": _safe_list(slots.keys()),
            "next_action": next_action,
        }
        if classification.decision_authority:
            ext["ai_state"]["decision_authority"] = classification.decision_authority
        if classification.transaction_type:
            ext["ai_state"]["transaction_type"] = classification.transaction_type
        if classification.must_have_features:
            ext["must_have_features"] = classification.must_have_features
        ext["qualification_view"] = qualification_view
    ext["slots"] = slots
    contact.external_ids = dict(ext)
    flag_modified(contact, "external_ids")
    contact.updated_at = datetime.utcnow()

    # Run workflows
    run_decision_workflows(
        session,
        conversation=conversation,
        contact=contact,
        decision=classification.model_dump() if classification else {},
    )

    session.add(conversation)
    session.add(contact)

    kb_ids = final.get("rag_kb_ids") or []
    outbound = Message(
        conversation_id=conversation.id,
        conversation_public_uuid=conversation.public_uuid,
        sender_type="agent",
        sender_id=None,
        message_type="text",
        message=(
            final.get("generated_reply")
            or (
                "Thanks for sharing those details. "
                "Could you also share your target timeline and budget range so we can map the right partner fit?"
            )
        ),
        channel=channel,
        is_generated=True,
        is_handled=True,
        rag_source_kb_ids=kb_ids,
    )
    session.add(outbound)
    if inbound_message:
        inbound_message.is_handled = True
        session.add(inbound_message)
    session.commit()
    session.refresh(outbound)
    
    _log_reply_quality(
        classification=classification,
        reply_text=outbound.message,
        conversation_turn=max(1, conversation_turn),
        include_cta=True, # Default to true for logging
    )
    return outbound


def _log_reply_quality(
    classification: MessageClassification | None,
    reply_text: str,
    conversation_turn: int,
    include_cta: bool,
) -> None:
    lower = (reply_text or "").lower()
    has_cta = any(token in lower for token in ("call", "consult", "meeting", "schedule"))
    has_qualifier_prompt = any(
        token in lower for token in ("scope", "budget", "timeline", "location", "decision")
    )
    has_defensive_phrase = ("we don't" in lower) or ("we do not" in lower)
    
    role = classification.role_type if classification else "unknown"
    intent = classification.intent_type if classification else "unknown"
    
    logger.info(
        "reply_quality role=%s intent=%s turn=%s cta_policy=%s cta_present=%s qualification_prompts_present=%s defensive_phrase=%s",
        role,
        intent,
        conversation_turn,
        include_cta,
        has_cta,
        has_qualifier_prompt,
        has_defensive_phrase,
    )
