"""LangGraph pipeline: RAG → structured decision → FAQ-grounded reply."""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
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

def _rag_debug_enabled() -> bool:
    return os.getenv("RAG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

def should_include_cta(
    *,
    conversation_turn: int,
    cta_readiness_score: int | float,
    missing_fields: list[str] | None,
    conversation_stage: str | None,
) -> bool:
    stage = (conversation_stage or "").strip().lower()
    turn = int(conversation_turn or 1)
    score = float(cta_readiness_score or 0)
    missing = [m for m in (missing_fields or []) if str(m).strip()]

    if stage in ("meeting_booked", "proposal_ready", "negotiation", "won"):
        return True
    if turn <= 1:
        return True
    if score >= 80:
        return True
    if stage in ("discovery",) and len(missing) >= 3 and score < 60:
        return False
    if score < 30 and len(missing) >= 2:
        return False
    return True

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
    role_type: str = Field(description="one of: buyer, seller, unknown")
    intent_type: str = Field(description="one of: property_search, property_listing, pricing_request, visit_request, follow_up, support_request, irrelevant")
    relationship_type: str = Field(description="one of: inbound_lead, existing_client, referral, unknown")
    decision_role: str = Field(description="one of: decision_maker, influencer, researcher, unknown")
    engagement_temperature: str = Field(description="one of: cold, warm, hot")
    qualification_stage: str = Field(description="one of: discovery, qualified, not_qualified, needs_info")
    is_escalated: bool = Field(description="True if human intervention is explicitly needed")
    conversation_status: str = Field(description="one of: open, pending_human, closed")
    lead_score: float | None = Field(description="number 0-100 or null if unknown")
    is_qualified: bool | None = Field(description="true if serious client, false if out of scope, null otherwise")
    budget: str | None = Field(description="budget signal from user")
    project_type: str | None = Field(description="property type: house, apartment, plot, commercial")
    timeline: str | None = Field(description="urgency signal from user")
    project_scope: str | None = Field(description="property details like size, bedrooms")
    decision_authority: str | None = Field(description="who makes decisions")
    geography: str | None = Field(description="area in Lahore")
    new_pipeline_stage: str = Field(description="The stage: discovery, qualified, meeting_booked, proposal_ready, negotiation, won, lost")
    close_readiness_score: float = Field(description="number 0-100")
    requires_action: list[str] = Field(description="List of actions: 'update_status', 'share_listings', 'schedule_visit', 'escalate_pricing'")
    missing_qualification_fields: list[str] = Field(description="Fields still needed: location, property_type, budget, bedrooms, purpose")
    filled_fields: list[str] = Field(default_factory=list, description="Fields already filled: location, property_type, budget, bedrooms, purpose, size, preferred_area")
    next_best_questions: list[str] = Field(description="1-3 short questions to ask next")

class AgentState(TypedDict, total=False):
    # Inputs
    user_text: str
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
    
    # Typed Outputs
    classification: MessageClassification | None
    rag_chunks: list[dict] | None
    rag_context: str
    rag_kb_ids: list[int]
    generated_reply: str | None
    error: str

PERSONA_POLICIES: dict[str, dict[str, str]] = {
    "buyer": {
        "focus": "understand exact property requirements and move toward showing options",
        "value": "we help you find the best property based on your needs and budget",
        "questions": "preferred area, budget range, rent or buy, number of bedrooms, any specific requirements",
        "cta": "once requirements are clear, offer property options or schedule a visit",
    },

    "seller": {
        "focus": "understand property details and help list or market it",
        "value": "we connect you with serious buyers or tenants quickly",
        "questions": "property type, location, demand/price expectation, condition, urgency",
        "cta": "collect details and move toward listing or discussing potential buyers",
    },

    "unknown": {
        "focus": "quickly identify whether they are buyer or seller",
        "value": "we assist with buying, selling, and renting properties in Lahore",
        "questions": "are you looking to buy, sell, or rent a property?",
        "cta": "once intent is clear, shift to buyer or seller flow",
    },
}

DECISION_PROMPT = """You are a Property Dealer .

BUSINESS SCOPE (STRICT):

You ONLY handle:
- Buying properties
- Selling properties
- Renting properties

Property types:
- Houses
- Apartments / Flats
- Plots
- Commercial properties

LOCATION:
- ONLY Lahore and nearby areas

YOU DO NOT HANDLE:
- Construction
- Renovation
- Architecture
- Interior design
- Partnerships
- Software / websites / any other services

--------------------------------------------------

YOUR ROLE:

You act like a smart property agent.

Your job is to:
- Understand client requirements
- Extract key details:
  - location
  - property type
  - budget
  - bedrooms / size
  - purpose (rent / buy / sell)
- Help them find suitable options
- Move them toward property viewing or deal closure

--------------------------------------------------

CLASSIFICATION LOGIC:

role_type:
- buyer
- seller
- unknown

intent_type:
- property_search → buyer intent (looking to buy/rent)
- property_listing → seller intent (wants to sell/rent out)
- pricing_request → asking about price
- visit_request → wants to view property
- follow_up
- support_request
- irrelevant

relationship_type:
- inbound_lead
- existing_client
- referral
- unknown

qualification_stage:
- discovery → missing key details
- qualified → location + type + budget clear
- meeting_booked → qualified AND user shows intent (wants to visit, see options, schedule)
- proposal_ready → scope clearly defined AND user wants to proceed
- negotiation → pricing/terms discussion started
- won → explicit acceptance/deal confirmed
- lost → explicit rejection OR clearly irrelevant/out of Lahore

--------------------------------------------------

IMPORTANT RULES:

- ALWAYS extract location (even if partial like "DHA", "Bahria")
- NEVER say "we don't operate there" without checking if it's Lahore area
- If location unclear → ASK for clarification
- If user gives requirements → move toward sharing options
- DO NOT ask irrelevant business questions (like stakeholders, scope, etc.)

--------------------------------------------------

SMART BEHAVIOR:

If user says:
"I want 4-5 bedroom house in Lahore"

You SHOULD:
- assume they mean 4-5 bedroom house
- ask:
  - preferred area?
  - budget range?
  - rent or buy?

NOT:
- reject
- misinterpret

--------------------------------------------------

STAGE EVIDENCE RULES (STRICT):

- discovery:
  default when key details are missing (location, type, budget, purpose)

- qualified:
  ONLY if location + property type + budget are clear

- meeting_booked:
  ONLY if qualified AND user shows intent to see options or visit

- proposal_ready:
  ONLY if scope clearly defined AND user wants to proceed

- negotiation:
  ONLY if pricing/terms discussion has started

- won:
  ONLY if explicit acceptance or deal confirmed

- lost:
  ONLY if clearly out of scope (not Lahore, irrelevant request) or explicit rejection

IMPORTANT:
- DO NOT skip stages
- DO NOT assume missing data
- If required fields are missing → stay in earlier stage

--------------------------------------------------

LEAD SCORING RULES (0-100):

- +25 → clear property type
- +25 → budget mentioned
- +20 → location specified
- +15 → urgency (ASAP, soon, actively looking)
- +15 → purpose clear (buy/sell/rent)

--------------------------------------------------

PRICING / BUDGET ESCALATION (CRITICAL):

If the user asks about:
- price
- budget
- cost
- estimated budget
- how much
- rate
- market value
- property value
- any pricing related question

You MUST:
- Set is_escalated = true
- Set conversation_status = "pending_human"
- Set requires_action = ["escalate_pricing"]
- Do NOT attempt to answer pricing questions
- Do NOT give rough estimates
- Do NOT guess market rates

The human team will respond manually with accurate pricing.

--------------------------------------------------

OUTPUT JSON with these keys:

- role_type: one of: buyer, seller, unknown
- intent_type: one of: property_search, property_listing, pricing_request, visit_request, follow_up, support_request, irrelevant
- relationship_type: one of: inbound_lead, existing_client, referral, unknown
- decision_role: one of: decision_maker, influencer, researcher, unknown
- engagement_temperature: one of: cold, warm, hot
- qualification_stage: one of: discovery, qualified, not_qualified, needs_info
- is_escalated: true or false
- conversation_status: one of: open, pending_human, closed
- lead_score: number 0-100 or null
- is_qualified: true, false, or null
- budget: string or null
- project_type: string or null (property type)
- timeline: string or null (urgency)
- project_scope: string or null (property details/size)
- decision_authority: string or null
- geography: string or null (area in Lahore)
- new_pipeline_stage: one of: discovery, qualified, meeting_booked, proposal_ready, negotiation, won, lost
- close_readiness_score: number 0-100
- requires_action: array of actions from: update_status, share_listings, schedule_visit, escalate_pricing
- missing_qualification_fields: array from: location, property_type, budget, bedrooms, purpose
- filled_fields: array from: location, property_type, budget, bedrooms, purpose, size, preferred_area
- next_best_questions: array of 1-3 short questions

CRITICAL TYPE RULES:
- is_escalated MUST be a JSON boolean: true or false (NOT a string)
- is_qualified MUST be a JSON boolean or null (NOT a string)
- lead_score MUST be a JSON number or null (NOT a string like "null" or "0")
- close_readiness_score MUST be a JSON number (NOT a string)
- requires_action MUST be a JSON array (NOT a string, NOT null — use empty array [] if none)
- missing_qualification_fields MUST be a JSON array
- filled_fields MUST be a JSON array
- next_best_questions MUST be a JSON array

Form/channel context:
{json_snapshot}

User message:
{user_text}

Knowledge base excerpts:
{rag_context}
"""

REPLY_PROMPT = """You are a professional property dealer/agent.

Your replies should:
1. Acknowledge request
2. Show understanding
3. Ask 1-2 relevant questions OR suggest next step
4. Move toward:
   - sharing listings
   - scheduling meeting

--------------------------------
RULES:
--------------------------------

- Be natural and helpful
- DO NOT sound like a corporate sales bot
- DO NOT ask irrelevant questions (like stakeholders, scope, software-style questions)
- DO NOT reject valid property queries
- NEVER use placeholders like "[Name]", "[Your Name]", "[City]"
- Use provided names; if missing, use "Hi," or "Hi there,"
- Be concise and conversational
- NEVER give price estimates, rough budgets, or market rate guesses
- If user asks about pricing/budget/cost → DO NOT answer
- Pricing queries are handled by the human team only

--------------------------------
STAGE-BASED BEHAVIOR
--------------------------------

Current stage: {stage_key}

- discovery:
  - ask 1-2 property qualification questions
  - focus on: location, type, budget, purpose
  - DO NOT provide listings yet

- qualified:
  - confirm understanding of requirements
  - offer to share matching options
  - if user shows intent → suggest visit/meeting

- meeting_booked:
  - share available options or schedule meeting
  - minimize additional questions

- proposal_ready:
  - present specific proposals or options
  - move toward closing

- negotiation:
  - address pricing, terms
  - push toward deal closure

- won:
  - confirm next steps / handover

- lost:
  - politely explain we only handle Lahore area property deals
  - do NOT re-engage qualification

--------------------------------
CTA RULES
--------------------------------

- ONLY suggest property visit if stage is meeting_booked or higher
- ONLY share listings if stage is qualified or higher
- In discovery → focus on gathering requirements

Available booking links:
{booking_links_context}

Default booking URL: {booking_url}

--------------------------------
GOOD EXAMPLE:
--------------------------------

User: "Looking for 4-5 bedroom house in Lahore"

Reply:
"Got it — you're looking for a 4-5 bedroom house in Lahore.

Could you share:
- preferred area (DHA, Bahria, etc.)?
- budget range?

I can shortlist some good options for you right away."

--------------------------------
PERSONA GUIDANCE
--------------------------------

{persona_policy}

--------------------------------
CONTEXT
--------------------------------

- Channel: {channel}
- Contact name: {contact_name}
- Agent name: {agent_name}
- Role type: {role_type}
- Relationship type: {relationship_type}
- Engagement temperature: {engagement_temperature}
- Decision role: {decision_role}
- Conversation turn: {conversation_turn}
- Recent context: {recent_context}
- Missing fields: {missing_fields}
- Filled fields: {filled_fields}
- CTA policy: {cta_policy}

--------------------------------
FAQ / KNOWLEDGE
--------------------------------

{rag_context}

--------------------------------
INTERNAL DECISION CONTEXT
--------------------------------

{decision_json}

--------------------------------
USER MESSAGE
--------------------------------

{user_text}
"""

ESCALATION_BRIEF_PROMPT = """You are an AI Property Analyst. Your goal is to summarize the entire conversation and extracted data into a clear, actionable brief that helps a human agent quickly understand the context of a lead escalation.

Your output MUST follow this EXACT format and use the provided data. If any field is unknown, write "Not specified". Do NOT include placeholders.

---

Lead Summary:
- Name: {contact_name}
- Role: {role_type}
- Relationship Type: {relationship_type}
- Decision Role: {decision_role}
- Engagement Level: {engagement_temperature}

---

Property Requirement:
- Property Type: {project_type}
- Details/Size: {project_scope}
- Budget: {budget}
- Urgency: {timeline}
- Location/Area: {geography}

---

Qualification Status:
- Current Stage: {stage_key}
- Qualification Status: {qualification_stage}
- Lead Score: {lead_score}
- Close Readiness: {close_readiness_score}

---

Key Signals:
- Buying/Selling Intent: {buying_intent_summary}
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
- Buying/Selling Intent: summarize briefly based on user language (1-2 lines)
- Urgency Level: low / medium / high (based on timeline and language)
- Constraints or Risks: mention missing info, unclear budget, or hesitation signals
- Conversation Highlights: 3-5 bullet points focusing on property preferences, decisions, and important statements
- Missing Information: Bulleted list of critical missing fields
- Recommended Next Action: Suggest ONE clear action (e.g., share listings, schedule property visit, continue qualification, escalate to senior agent)
- AI Notes (Internal): Mention why escalation happened and any inconsistencies/risks
"""
def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _build_recent_context(session: Session, conversation_id: int, limit: int = 4) -> str:
    msgs = list(
        session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        ).all()
    )
    msgs.reverse()
    parts: list[str] = []
    for m in msgs:
        parts.append(f"{m.sender_type}: {m.message[:200]}")
    return " | ".join(parts)


def _extract_slot_state(user_text: str, contact: Contact) -> dict[str, Any]:
    text = (user_text or "").lower()
    slots: dict[str, Any] = {}
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    prev_slots = ext.get("slots", {}) if isinstance(ext, dict) else {}
    if isinstance(prev_slots, dict):
        slots.update(prev_slots)

    if contact.project_type and "property_type" not in slots:
        slots["property_type"] = contact.project_type
    if contact.budget and "budget" not in slots:
        slots["budget"] = contact.budget
    if contact.timeline and "urgency" not in slots:
        slots["urgency"] = contact.timeline

    # Property type detection
    if re.search(r"\b(house|home|apartment|flat|villa|plot|commercial|shop|office|plaza|penthouse|farmhouse|bungalow|marla|kanal)\b", text):
        slots["property_type"] = user_text[:120]
    # Budget detection
    if re.search(r"\b(\$|pkr|rs|lac|lakh|crore|cr|budget|million|m\b|k\b)\b", text):
        slots["budget"] = user_text[:120]
    # Location / area detection (Lahore areas)
    if re.search(r"\b(dha|bahria|johar town|model town|gulberg|cantt|wapda town|valencia|askari|lake city|garden town|iqbal town|township|defence|raiwind|bedian|canal|mall road|lahore)\b", text):
        slots["location"] = user_text[:120]
    # Purpose detection
    if re.search(r"\b(buy|purchase|rent|lease|sell|selling|sale)\b", text):
        slots["purpose"] = user_text[:120]
    # Bedrooms detection
    if re.search(r"\b(\d+)\s*(bed|bedroom|br|room)\b", text):
        slots["bedrooms"] = user_text[:120]
    # Size detection
    if re.search(r"\b(\d+)\s*(marla|kanal|sq\s*ft|sqft|square\s*feet|yard)\b", text):
        slots["size"] = user_text[:120]
    return slots


def _wants_estimate(user_text: str) -> bool:
    # Pricing queries are always escalated to human — never auto-estimate
    return False


def _format_estimator_context(user_text: str, slots: dict[str, Any]) -> str:
    est = estimate_project(user_text, slots)
    assumptions = "; ".join(est.get("assumptions", []))
    return (
        "Use these indicative ranges if user asks budget/time: "
        f"MVP {est.get('mvp_budget_range')} ({est.get('mvp_timeline')}), "
        f"Standard {est.get('standard_budget_range')} ({est.get('standard_timeline')}), "
        f"Advanced {est.get('advanced_budget_range')} ({est.get('advanced_timeline')}). "
        f"Assumptions: {assumptions}."
    )


def build_graph(session: Session):
    if not settings.GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY required")
    if not settings.GROQ_CHAT_MODEL:
        raise ValueError("GROQ_CHAT_MODEL required")

    llm = ChatGroq(
        model=settings.GROQ_CHAT_MODEL,
        temperature=0.2,

        api_key=settings.GROQ_API_KEY,
    )

    def node_classify(state: AgentState) -> Command:
        # Fetch dynamic stages from DB
        stages = list(session.exec(select(PipelineStage).order_by(PipelineStage.order_index)).all())
        stage_info = "\n".join([f"- {s.key}: {s.ai_instructions}" for s in stages])
        valid_keys = ", ".join([s.key for s in stages])

        structured_llm = llm.with_structured_output(MessageClassification)
        prompt = DECISION_PROMPT.format(
            json_snapshot=state.get("json_snapshot", "{}"),
            user_text=state.get("user_text", ""),
            rag_context="",
        )
        # Inject dynamic stages into prompt
        prompt += f"\n\nAVAILABLE PIPELINE STAGES:\n{stage_info}\n\nRule: new_pipeline_stage MUST be one of: {valid_keys}"

        try:
            classification = structured_llm.invoke([HumanMessage(content=prompt)])
            # Force escalation for any pricing/budget query
            user_lower = state.get("user_text", "").lower()
            pricing_keywords = ["price", "budget", "cost", "how much", "rate", "estimate", "worth", "value", "kitna", "kya rate", "kitnay", "kitne"]
            is_pricing_query = any(kw in user_lower for kw in pricing_keywords) or (classification.intent_type == "pricing_request")
            if is_pricing_query:
                classification.is_escalated = True
                classification.conversation_status = "pending_human"
                classification.requires_action = ["escalate_pricing"]
                return Command(update={"classification": classification}, goto="human_review")
            if classification.is_escalated:
                return Command(update={"classification": classification}, goto="human_review")
            elif "update_status" in (classification.requires_action or []):
                return Command(update={"classification": classification}, goto="update_status")
            else:
                return Command(update={"classification": classification}, goto="retrieve")
        except Exception as e:
            logger.error(f"Classification error: {e}")
            # Provide a safe fallback classification
            fallback = MessageClassification(
                role_type="unknown",
                intent_type="follow_up",
                relationship_type="inbound_lead",
                decision_role="unknown",
                engagement_temperature="cold",
                qualification_stage="discovery",
                is_escalated=False,
                conversation_status="open",
                lead_score=None,
                is_qualified=None,
                budget=None,
                project_type=None,
                timeline=None,
                project_scope=None,
                decision_authority=None,
                geography=None,
                new_pipeline_stage="discovery",
                close_readiness_score=0.0,
                requires_action=[],
                missing_qualification_fields=["location", "property_type", "budget", "bedrooms", "purpose"],
                filled_fields=[],
                next_best_questions=["What type of property are you looking for?", "Which area in Lahore do you prefer?"],
            )
            return Command(update={"classification": fallback}, goto="retrieve")

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
        stage_key = classification.new_pipeline_stage if classification else "discovery"
        
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

        # Stage-driven prompt logic (use role_type, not intent_type)
        persona_segment = classification.role_type if classification else "unknown"
        if persona_segment not in PERSONA_POLICIES:
            persona_segment = "unknown"
        persona_policy = PERSONA_POLICIES[persona_segment]

        prompt = REPLY_PROMPT.format(
            channel=state.get("channel", "website"),
            contact_name=state.get("contact_name", "there"),
            agent_name=state.get("agent_name", "PropertyHub"),
            stage_key=stage_key,
            relationship_type="inbound_lead",
            engagement_temperature="warm",
            decision_role="decision_maker",
            rag_context=state.get("rag_context", ""),
            decision_json=json.dumps(classification.model_dump()) if classification else "{}",
            user_text=state.get("user_text", ""),
            booking_url=state.get("booking_url", ""),
            booking_links_context=booking_links_context,
            role_type=persona_segment,
            persona_policy=json.dumps(persona_policy),
            conversation_turn=state.get("conversation_turn", 1),
            recent_context=state.get("recent_context", ""),
            missing_fields=", ".join(classification.missing_qualification_fields) if classification else "",
            filled_fields=state.get("filled_fields", ""),
            cta_policy=stage_instruction,
        )
        
        out = llm.invoke([
            SystemMessage(content="You write clear, business-professional client replies. No markdown code fences."),
            HumanMessage(content=prompt)
        ])
        reply = out.content if hasattr(out, "content") else str(out)
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
                    
                    # Send email notification for escalation
                    try:
                        from app.services.email_service import send_email
                        email_subject = f"Lead Escalation - {state.get('contact_name', 'Unknown')}"
                        email_body = f"""
A lead has been escalated and requires manual response.

Contact: {state.get('contact_name', 'Unknown')}
Channel: {state.get('channel', 'Unknown')}
Conversation ID: {conversation_id}

Reason: {'Pricing/budget inquiry - DO NOT auto-respond' if 'escalate_pricing' in (classification.requires_action or []) else 'AI flagged for human review'}

--- Escalation Brief ---
{conv.escalation_brief}

--- Latest Message ---
{state.get('user_text', '')}
"""
                        import asyncio
                        if asyncio.iscoroutinefunction(send_email):
                            # If send_email is async, we need to run it - but since node_human_review is sync,
                            # we'll create a task and log any errors
                            async def _send():
                                try:
                                    # Get email config from settings
                                    from app.core.config import settings
                                    email_config = {
                                        "type": getattr(settings, "EMAIL_PROVIDER", "smtp"),
                                        "host": getattr(settings, "SMTP_HOST", ""),
                                        "port": getattr(settings, "SMTP_PORT", 587),
                                        "user": getattr(settings, "SMTP_USER", ""),
                                        "password": getattr(settings, "SMTP_PASSWORD", ""),
                                        "from_email": getattr(settings, "FROM_EMAIL", "noreply@strategisthub.com"),
                                        "use_tls": getattr(settings, "SMTP_USE_TLS", True),
                                    }
                                    await send_email(email_config, "sajjad_hussain@strategisthub.com", email_subject, email_body)
                                    logger.info(f"Escalation email sent for conversation {conversation_id}")
                                except Exception as email_err:
                                    logger.warning(f"Could not send escalation email: {email_err}")
                            # Schedule the async task
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    loop.create_task(_send())
                                else:
                                    loop.run_until_complete(_send())
                            except Exception as loop_err:
                                logger.warning(f"Could not schedule escalation email: {loop_err}")
                        else:
                            # Synchronous call
                            from app.core.config import settings
                            email_config = {
                                "type": getattr(settings, "EMAIL_PROVIDER", "smtp"),
                                "host": getattr(settings, "SMTP_HOST", ""),
                                "port": getattr(settings, "SMTP_PORT", 587),
                                "user": getattr(settings, "SMTP_USER", ""),
                                "password": getattr(settings, "SMTP_PASSWORD", ""),
                                "from_email": getattr(settings, "FROM_EMAIL", "noreply@strategisthub.com"),
                                "use_tls": getattr(settings, "SMTP_USE_TLS", True),
                            }
                            send_email(email_config, "sajjad_hussain@strategisthub.com", email_subject, email_body)
                            logger.info(f"Escalation email sent for conversation {conversation_id}")
                    except Exception as email_err:
                        logger.warning(f"Could not send escalation email: {email_err}")
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
) -> Message:
    """Runs graph, updates contact/conversation, creates outbound Message."""
    is_proactive = inbound_message is None
    if is_proactive:
        user_text = "(System nudge: Lead has been silent. Re-engage politely based on prior context.)"
    else:
        user_text = inbound_message.message

    # Check for keywords to assume defaults and provide plan
    keywords = ["immediately", "proceed", "yes"]
    if any(kw in user_text.lower() for kw in keywords):
        user_text += " Assume the client is ready to proceed. Share available property options or suggest a visit."
        # Clear recent context to avoid looping
        recent_context = ""
    else:
        recent_context = _build_recent_context(session, conversation.id, limit=5)

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
    recent_context = _build_recent_context(session, conversation.id, limit=5)
    slots = _extract_slot_state(user_text, contact)
    estimator_context = _format_estimator_context(user_text, slots) if _wants_estimate(user_text) else ""

    contact_name = contact.username or "there"
    if "<" in contact_name and ">" in contact_name:
        contact_name = contact_name.split("<")[0].strip()

    graph = build_graph(session)
    initial: AgentState = {
        "user_text": user_text,
        "is_proactive": is_proactive,
        "channel": channel,
        "json_snapshot": json.dumps(json_response, default=str)[:12000],
        "booking_url": booking_url,
        "booking_links": booking_links,
        "conversation_turn": max(1, conversation_turn),
        "recent_context": recent_context,
        "filled_fields": ", ".join(sorted(slots.keys())),
        "estimator_context": estimator_context,
        "contact_id": contact.id,
        "conversation_id": conversation.id,
        "contact_name": contact_name,
        "agent_name": settings.AGENT_NAME,
    }
    config = {"configurable": {"thread_id": str(conversation.id)}}
    
    # Execute graph
    final = graph.invoke(initial, config)
    
    classification = final.get("classification")
    
    # Update conversation based on classification
    if classification:
        conversation.last_intent = str(classification.intent_type)[:500]
        conversation.is_escalated = bool(classification.is_escalated)
        conversation.status = str(classification.conversation_status or "open")[:100]
        
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
        
        # Update contact fields
        for fld in ("budget", "project_type", "timeline"):
            v = getattr(classification, fld)
            if v is not None and str(v).strip():
                setattr(contact, fld, str(v)[:2000])

    from datetime import datetime
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

    # Persist lightweight state
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    if classification:
        ext["ai_state"] = {
            "conversation_stage": classification.new_pipeline_stage,
            "missing_fields": classification.missing_qualification_fields,
            "filled_fields": _safe_list(slots.keys()),
            "next_action": next_action,
        }
    ext["slots"] = slots
    contact.external_ids = ext
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
    
    # If pricing escalation, do NOT auto-reply to client
    is_pricing_escalation = classification and "escalate_pricing" in (classification.requires_action or [])
    
    if is_pricing_escalation:
        # Don't send AI reply — human team will respond
        outbound = Message(
            conversation_id=conversation.id,
            conversation_public_uuid=conversation.public_uuid,
            sender_type="system",
            sender_id=None,
            message_type="internal",
            message="⚠️ Pricing inquiry detected. Escalated to human team. Awaiting manual response.",
            channel=channel,
            is_generated=True,
            is_handled=False,
            rag_source_kb_ids=[],
        )
        conversation.status = "pending_human"
        conversation.is_escalated = True
    else:
        outbound = Message(
            conversation_id=conversation.id,
            conversation_public_uuid=conversation.public_uuid,
            sender_type="agent",
            sender_id=None,
            message_type="text",
            message=(
                final.get("generated_reply")
                or (
                    "Thanks for reaching out! Could you share what type of property you're looking for and your preferred area in Lahore?"
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
