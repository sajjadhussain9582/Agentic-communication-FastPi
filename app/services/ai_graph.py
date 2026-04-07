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

    if stage in ("proposal_ready", "meeting_booked", "qualified", "negotiation", "won"):
        return True
    if turn <= 1:
        return True
    if score >= 80:
        return True
    if stage in ("requirements_gathering", "discovery") and len(missing) >= 3 and score < 60:
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
    role_type: str = Field(description="one of: contractor, agent, developer, architect, builder, unknown")
    intent_type: str = Field(description="one of: service_inquiry, pricing_request, booking_request, partnership_inquiry, support_request, follow_up, complaint, nurture")
    relationship_type: str = Field(description="one of: inbound_lead, outbound_prospect, referral_partner, existing_client, dormant_lead, reengaged_lead")
    decision_role: str = Field(description="one of: decision_maker, influencer, researcher, assistant, unknown")
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
    new_pipeline_stage: str = Field(description="The stage the AI decides to move them to (discovery, qualified, proposal_ready, etc.)")
    close_readiness_score: float = Field(description="number 0-100")
    requires_action: list[str] = Field(description="List of actions: 'update_status', 'send_calendly', 'send_proposal'")
    missing_qualification_fields: list[str] = Field(description="Fields still needed: scope, budget, timeline, location, stakeholders")
    filled_fields: list[str] = Field(default_factory=list, description="Fields already filled: project_type, scope, budget_signal, timeline_signal, location, stakeholders, must_have_features")
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
   "contractor": {
        "focus": "identify if they need execution support or are open to partnership for handling overflow or specialized work",
        "value": "we help contractors deliver projects faster by handling design, planning, and execution through our in-house team",
        "questions": "are you looking for project support or additional leads, what type of projects do you handle, what challenges are you facing currently",
        "cta": "If they need support, move toward consultation; if partnership fit, propose a partnership alignment call",
    },

    "agent": {
        "focus": "determine if they are a referral partner or have clients needing design/build services",
        "value": "we help agents close more deals by supporting their clients with end-to-end project execution",
        "questions": "do your clients require design or construction services, what markets do you serve, how often do you get such requirements",
        "cta": "If referral potential exists, propose partnership call; if direct project, move toward consultation",
    },

    "developer": {
    "focus": "determine if they are a serious property / real-estate developer or long-term build partner; assess project scope, stakeholders, and delivery expectations",
    "questions": "project type (residential/commercial), phases, stakeholders involved, budget range, deployment timeline, decision authority",
    "cta": "If qualified, propose a discovery/consultation call about their real-estate or construction projects; if early-stage, continue structured qualification before          booking.",
    },

    "architect": {
        "focus": "identify if they need execution support or collaboration for delivering projects",
        "value": "we support architects by executing their designs and handling project delivery end-to-end",
        "questions": "are you looking for execution support, what stage is your project in, who is the end client, timeline and budget clarity",
        "cta": "If collaboration fit, propose partnership discussion; if direct project, guide to consultation",
    },

    "builder": {
        "focus": "assess if they need operational support or collaboration for project delivery",
        "value": "we help builders streamline delivery by handling planning, design coordination, and execution support",
        "questions": "what type of builds do you handle, current bottlenecks, project volume, timeline and budget signals",
        "cta": "If high intent, move toward consultation or partnership kickoff depending on context",
    },

    "unknown": {
        "focus": "identify role, intent, and seriousness quickly without making assumptions",
        "value": "we support real-estate and construction stakeholders with architecture, planning, and execution",
        "questions": "what type of property/construction project you need support with, your role, budget, timeline, and decision authority",
        "cta": "If in-scope and clear, guide to consultation; otherwise request clarification politely",
    },
}

DECISION_PROMPT = """You are an AI Sales Manager for a B2B services company of real estate (• Contractors
• Real estate agents
• Real estate developersz
• architects
• home builders)

Your responsibility is to:
- qualify leads accurately
- determine the safest valid pipeline stage
- identify blockers preventing progression
- decide the next best action to move the deal forward
- avoid premature stage advancement

You MUST output ONLY valid JSON with the following keys:

- role_type: one of: contractor, agent, developer, architect, builder, unknown
- intent_type: one of: service_inquiry, pricing_request, booking_request, partnership_inquiry, support_request, follow_up, complaint, nurture
- relationship_type: one of: inbound_lead, outbound_prospect, referral_partner, existing_client, dormant_lead, reengaged_lead
- decision_role: one of: decision_maker, influencer, researcher, assistant, unknown
- engagement_temperature: one of: cold, warm, hot
- qualification_stage: one of: discovery, qualified, not_qualified, needs_info
- is_escalated: true or false
- conversation_status: one of: open, pending_human, closed
- lead_score: number between 0-100 or null
- is_qualified: true, false, or null
- budget: string or null
- project_type: string or null
- timeline: string or null
- project_scope: string or null
- decision_authority: string or null
- geography: string or null
- new_pipeline_stage: one of: discovery, qualified, meeting_booked, proposal_ready, negotiation, won, lost
- close_readiness_score: number between 0-100
- requires_action: array of actions from: update_status, send_calendly, send_proposal
- missing_qualification_fields: array from: scope, budget, timeline, location, stakeholders, constraints
- filled_fields: array from: project_type, scope, budget_signal, timeline_signal, location, stakeholders, must_have_features
- next_best_questions: array of 1-3 short questions

-----------------------------
STAGE EVIDENCE RULES (STRICT)
-----------------------------

You MUST follow these rules when setting new_pipeline_stage:

- discovery:
  default when key qualification data is missing

- qualified:
  ONLY if ALL are present:
  - project_type
  - budget or budget_signal
  - timeline or timeline_signal

- meeting_booked:
  ONLY if:
  - qualified conditions are met
  AND
  - user shows intent (asks next steps, pricing, availability, or moving forward)

- proposal_ready:
  ONLY if:
  - scope is clearly defined
  AND
  - decision authority or stakeholders are identified
  AND
  - user shows execution intent

- negotiation:
  ONLY if:
  - pricing or terms discussion has started
  OR
  - user is comparing options

- won:
  ONLY if:
  - explicit acceptance or commitment is present

- lost:
  ONLY if:
  - explicit rejection OR clearly irrelevant/spam

IMPORTANT:
- DO NOT skip stages
- DO NOT assume missing data
- If required fields are missing → stay in earlier stage
- It is better to stay one stage behind than move too early

--------------------------------
MISSING FIELD HARD CONSTRAINTS
--------------------------------

- If budget OR timeline is missing → MUST NOT exceed "discovery"
- If scope is missing → MUST NOT exceed "qualified"
- If stakeholders/decision authority missing → MUST NOT exceed "meeting_booked"

--------------------------------
LEAD SCORING RULES (0–100)
--------------------------------

Assign lead_score based on:

- +25 → clear project_type
- +25 → budget mentioned
- +20 → timeline mentioned
- +15 → urgency (ASAP, soon, active project)
- +15 → decision authority identified

Score interpretation:
- 0–40 → cold
- 41–70 → warm
- 71–100 → hot

--------------------------------
ACTION RULES
--------------------------------

- include "send_calendly" ONLY if:
  - stage is "qualified" or higher
  AND
  - engagement_temperature is "warm" or "hot"

- include "send_proposal" ONLY if:
  - stage is "proposal_ready" or higher

- include "update_status" ONLY if:
  - new_pipeline_stage differs from current logical stage

--------------------------------
DECISION THINKING
--------------------------------

At every step determine:

1. Is this a real opportunity?
2. What information is missing?
3. What is blocking progress?
4. What is the safest next step?

--------------------------------
GENERAL RULES
--------------------------------

- Keep outputs factual and concise
- Do NOT hallucinate missing data
- If uncertain → return null and include in missing_qualification_fields
- Prefer safe progression over aggressive advancement
- Do not include any keys outside the schema

--------------------------------
OUT-OF-DOMAIN GUARDRAIL (SOFTWARE/WEB)
--------------------------------

- This system serves ONLY the real estate / construction ecosystem:
  contractors, real-estate agents, real-estate developers, architects, home builders.
- If the message is clearly about websites, web apps, SaaS platforms, or generic IT/software development,
  then treat it as OUT OF SCOPE:
  - Set role_type = "unknown".
  - Set is_qualified = false.
  - Prefer qualification_stage = "not_qualified".
  - Prefer new_pipeline_stage = "lost" or stay at "discovery".
  - In requires_action, DO NOT include send_calendly or send_proposal.

--------------------------------

Form/channel context:
{json_snapshot}

User message:
{user_text}

Knowledge base excerpts:
{rag_context}
"""

REPLY_PROMPT = """You are an AI Sales Manager executing the next best sales action.

Your job is to:
- qualify efficiently
- build trust quickly
- reduce friction
- move the deal forward toward consultation, proposal, or close

Write ONE clear, professional, human-like reply.

--------------------------------
CORE RULES
--------------------------------

- NEVER use placeholders like "[Name]", "[Your Name]", "[City]"
- Use provided names; if missing, use "Hi," or "Hi there,"
- Be concise, executive, and natural
- Do NOT repeat questions already answered
- Do NOT ask more than 1–2 questions
- Do NOT overwhelm the user

--------------------------------
OUT-OF-SCOPE HANDLING (CRITICAL)
--------------------------------

- This assistant ONLY supports real-estate / construction domain.
- If user asks for website, app, software, SaaS, IT development, or unrelated digital-product services:
  - clearly state this is outside service scope
  - do NOT ask website/app follow-up discovery questions
  - do NOT claim capability to deliver such digital services
  - offer help only for in-scope real-estate/construction services

--------------------------------
KNOWLEDGE & ACCURACY
--------------------------------

- ONLY use facts from knowledge excerpts below
- If info is missing → acknowledge and ask a targeted question
- If retrieval confidence is low → suggest consultation instead of guessing
- DO NOT hallucinate pricing, policies, or guarantees

--------------------------------
BUSINESS POSITIONING
--------------------------------

- Position the company as a full-service provider:
  architecture, planning, budgeting, and execution
- Emphasize integrated in-house delivery
- Do NOT talk about internal systems/tools

--------------------------------
SALES EXECUTION STRUCTURE
--------------------------------

Every reply should follow:

1. Acknowledge context (short)
2. Provide value or clarity
3. Move the deal forward:
   - ask 1–2 key questions OR
   - provide CTA OR
   - guide next step

--------------------------------
STAGE-BASED BEHAVIOR (CRITICAL)
--------------------------------

Current stage: {stage_key}

- discovery:
  - ask 1–2 qualification questions
  - DO NOT provide booking link
  - focus on missing_fields

- qualified:
  - confirm understanding
  - if user shows intent → include booking link
  - otherwise soft CTA or continue qualification

- meeting_booked:
  - provide booking link directly
  - minimize additional questions

- proposal_ready:
  - confirm scope or stakeholders if needed
  - move toward proposal or decision
  - DO NOT restart discovery

- negotiation:
  - address pricing, objections, or terms
  - push toward decision
  - avoid new qualification

- won:
  - confirm next steps / onboarding tone

- lost:
  - close politely
  - do NOT re-engage qualification

--------------------------------
CTA RULES
--------------------------------

- ONLY include booking link if:
  - stage is meeting_booked or higher
  OR
  - user explicitly asks for meeting/link

- NEVER include booking link in discovery

- If booking link is included:
  - use best match from available links
  - fallback to default: {booking_url}

Available booking links:
{booking_links_context}

--------------------------------
QUESTION STRATEGY
--------------------------------

- Ask only from missing_fields / next_best_questions
- PRIORITY:
  1. budget
  2. timeline
  3. scope
  4. stakeholders

--------------------------------
MOMENTUM RULE
--------------------------------

- If user shows strong intent → act immediately
- Do NOT delay next step unnecessarily
- Do NOT over-qualify a ready buyer

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
- Estimator guidance: {estimator_context}
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

    if contact.project_type and "project_type" not in slots:
        slots["project_type"] = contact.project_type
    if contact.budget and "budget_signal" not in slots:
        slots["budget_signal"] = contact.budget
    if contact.timeline and "timeline_signal" not in slots:
        slots["timeline_signal"] = contact.timeline

    # Treat only real-estate / construction terms as valid project_type triggers.
    # Explicitly exclude generic software/web dev terms from domain.
    if re.search(r"\b(house|home|apartment|flat|villa|plot|residential|commercial|building|construction|renovation|remodel)\b", text):
        slots["project_type"] = user_text[:120]
    if re.search(r"\b(\$|usd|budget|k\b|million|m\b)\b", text):
        slots["budget_signal"] = user_text[:120]
    if re.search(r"\b(week|weeks|month|months|quarter|timeline|deadline|asap)\b", text):
        slots["timeline_signal"] = user_text[:120]
    if re.search(r"\b(in|at)\s+[a-zA-Z][a-zA-Z\s]{2,30}\b", text):
        slots.setdefault("location", user_text[:120])
    if re.search(r"\b(founder|owner|decision|stakeholder|partner|team)\b", text):
        slots["stakeholders"] = user_text[:120]
    if re.search(r"\b(must|need|require|important|feature|integration)\b", text):
        slots["must_have_features"] = user_text[:200]
    if re.search(r"\b(scope|full build|mvp|revamp|redesign)\b", text):
        slots["scope"] = user_text[:200]
    return slots


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
        )
    )


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
            if classification.is_escalated:
                return Command(update={"classification": classification}, goto="human_review")
            elif "update_status" in (classification.requires_action or []):
                return Command(update={"classification": classification}, goto="update_status")
            else:
                return Command(update={"classification": classification}, goto="retrieve")
        except Exception as e:
            logger.error(f"Classification error: {e}")
            return Command(goto="retrieve")

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
            agent_name=state.get("agent_name", "StrategistHub"),
            stage_key=stage_key,
            relationship_type="inbound_lead", # Default
            engagement_temperature="warm",
            decision_role="decision_maker",
            rag_context=state.get("rag_context", ""),
            decision_json=json.dumps(classification.model_dump()) if classification else "{}",
            user_text=state.get("user_text", ""),
            booking_url=state.get("booking_url", ""),
            booking_links_context=booking_links_context,
            persona_segment=persona_segment,
            role_type=persona_segment,
            persona_policy=json.dumps(persona_policy),
            conversation_turn=state.get("conversation_turn", 1),
            recent_context=state.get("recent_context", ""),
            missing_fields=", ".join(classification.missing_qualification_fields) if classification else "",
            filled_fields=state.get("filled_fields", ""),
            estimator_context=state.get("estimator_context", ""),
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
        user_text += " Assume defaults: project type is double-storey, timeline is ASAP. Provide a detailed plan instead of asking more questions. Use new KB entries for guidance."
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
