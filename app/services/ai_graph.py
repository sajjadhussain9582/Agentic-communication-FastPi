"""LangGraph pipeline: RAG → structured decision → FAQ-grounded reply."""

from __future__ import annotations

import json
import logging
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

# Global checkpointer for human-in-the-loop interrupts
_memory_saver = MemorySaver()

# Global connection pool for PostgresSaver
_pool = None

def get_checkpointer():
    global _pool
    if not settings.DATABASE_URL:
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
    if not settings.DATABASE_URL:
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
    next_best_questions: list[str] = Field(description="1-3 short questions to ask next")

class AgentState(TypedDict, total=False):
    # Inputs
    user_text: str
    is_proactive: bool
    channel: str
    json_snapshot: str
    booking_url: str
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
        "focus": "assess if they are a potential partner or service buyer; understand project pipeline, subcontracting needs, and urgency",
        "questions": "are you looking for leads or services, project ssize, service area, current challenges, timeline and budget context",
        "cta": "If partner-fit, offer a partnership alignment call; if project-fit, guide toward consultation booking with clear next steps.",
    },
    "agent": {
        "focus": "identify if this is a referral partnership or direct service need; understand transaction volume and collaboration goals",
        "questions": "markets served, monthly transactions, referral expectations, type of collaboration, decision-making role",
        "cta": "If partnership-driven, invite to co-marketing/referral call; if service-driven, guide toward consultation with clear value.",
    },
    "developer": {
        "focus": "determine if they are a serious buyer or long-term partner; assess project scope, stakeholders, and delivery expectations",
        "questions": "project type, phases, stakeholders involved, budget range, deployment timeline, decision authority",
        "cta": "If qualified, propose discovery/consultation call; if early-stage, continue structured qualification before booking.",
    },
    "architect": {
        "focus": "understand collaboration potential vs active project need; evaluate design stage, coordination needs, and seriousness",
        "questions": "project type, design stage, collaboration expectations, stakeholders, timeline and budget clarity",
        "cta": "If collaboration-fit, offer design partnership discussion; if project-fit, guide toward structured consultation.",
    },
    "builder": {
        "focus": "assess build pipeline and partnership potential; identify operational gaps and urgency of current projects",
        "questions": "build volume, locations, bottlenecks, current workflow gaps, timeline and budget signals",
        "cta": "If high intent, suggest partnership kickoff or consultation; if unclear, continue qualification before pushing meeting.",
    },
    "unknown": {
        "focus": "identify role, intent, and seriousness before deciding next step; avoid premature assumptions",
        "questions": "what is your role, what are you trying to achieve, project scope, budget, timeline, and decision authority",
        "cta": "If enough clarity, guide to consultation; otherwise continue qualification with minimal friction.",
    },
}

DECISION_PROMPT = """You are an internal analyst for a B2B services company focused on growth partnerships and client qualification.
Based on the user's message, prior context, and form data, output ONLY valid JSON with these keys:

- role_type: one of: contractor, agent, developer, architect, builder, unknown
- intent_type: one of: service_inquiry, pricing_request, booking_request, partnership_inquiry, support_request, follow_up, complaint, nurture
- relationship_type: one of: inbound_lead, outbound_prospect, referral_partner, existing_client, dormant_lead, reengaged_lead
- decision_role: one of: decision_maker, influencer, researcher, assistant, unknown
- engagement_temperature: one of: cold, warm, hot
- qualification_stage: one of: discovery, qualified, not_qualified, needs_info
- is_escalated: true if a human should handle this (legal, complaints, highly complex enterprise architectures, sensitive issues, repeatedly frustrated user, or user explicitly asks for human)
- conversation_status: one of: open, pending_human, closed
- lead_score: number 0-100 or null if unknown
- is_qualified: true if serious client with realistic budget and scope, false if obviously unrelated or low intent/spam, null otherwise
- budget: string or null
- project_type: string or null
- timeline: string or null
- project_scope: string or null
- decision_authority: string or null
- geography: string or null
- conversation_stage: one of: discovery, requirements_gathering, estimation_ready, proposal_ready, close_ready
- close_readiness_score: number 0-100
- next_action: one of: ask_missing_info, share_estimate, offer_shortlist, book_consultation, escalate_human, send_proposal
- missing_fields: array of strings chosen from: scope, budget, timeline, location, stakeholders, constraints
- filled_fields: array of strings chosen from: project_type, scope, budget_signal, timeline_signal, location, stakeholders, must_have_features
- next_best_questions: array of 1-3 short questions
- cta_readiness_score: number 0-100

Rules:
- Categorize the contact's role_type, intent_type, relationship_type, decision_role, and engagement_temperature from message/context.
- Use role_type only for industry/business-role classification.
- Use relationship_type to distinguish inbound leads, outbound prospects, referral partners, existing clients, dormant leads, and reengaged leads.
- Use decision_role to reflect whether the contact appears to be the decision maker, an influencer, a researcher, an assistant, or unknown.
- Prefer partnership/business development framing when applicable.
- Keep values concise, practical, and factual.
- Infer a practical next_action that moves the conversation forward safely.
- Favor moving from discovery -> estimation_ready -> proposal_ready when enough detail exists.
- Mark not_qualified only when there is a clear reason; otherwise prefer needs_info.
- If confidence is low, leave uncertain fields as null and include them in missing_fields instead of guessing.
- Do not include any keys beyond the schema above.

Form/channel context:
{json_snapshot}

User message:
{user_text}

Knowledge base excerpts (may be empty):
{rag_context}
"""

REPLY_PROMPT = """You are a professional client-facing partnership assistant. Write ONE polished reply email/message.

Rules:
- NEVER use generic placeholders like "[Name]", "[Your Name]", "[City]", or any text inside brackets [ ]. 
- Use the actual names provided below. If a name is unknown, use a warm but professional generic greeting (e.g., "Hi there," or just "Hi,").
- Ground factual claims ONLY in the FAQ/knowledge excerpts below. If something is not in the excerpts, do not make up policies, prices, or guarantees.
- Answer primarily from retrieved KB chunks. If the information needed is missing from the excerpts, acknowledge the gap honestly and ask a targeted follow-up question instead of guessing.
- If retrieval confidence is low (indicated by low scores or no matches), acknowledge uncertainty and suggest a consultation instead of speculating.
- Do not repeat questions that have already been answered in earlier turns.
- Position the company as a full-service provider that handles design, planning, budgeting, and project execution.
- Emphasize that we provide the architecture, design, and construction services directly through our internal team and integrated delivery model.
- Do not use internal platform positioning as the core customer offer (avoid presenting automation/lead-qualification tooling as direct service delivery).
- If excerpts are missing or insufficient, keep confidence low but still provide a concrete next step.
- Avoid defensive phrasing such as "we do not handle this" or "we don't provide that."

Turn behavior:
- If conversation_turn == 1: use a short greeting (e.g., "Hi {contact_name},") and opening.
- If conversation_turn > 1: do not repeat greeting/signature; continue naturally from prior context.
- Do not end every message with a meeting ask.
- Ask only the next best 1-2 questions from missing_fields/next_best_questions.
- Max 1-2 follow-up questions per turn.
- If user asks budget/time, answer with concrete ranges in this same reply before any follow-up question.
- Do not ask for slots already present in filled_fields.
- If stage is proposal_ready or close_ready, prioritize close action (shortlist, proposal, or booking) over new discovery questions.
- Briefly reference the user's latest details naturally.
- If user asks for partner contact details directly, do not dump raw personal phone/email data. Offer curated shortlist + warm intro workflow.

Signature:
- Sign off naturally as {agent_name} if this is the first turn or a full email format is expected. Otherwise, skip the signature.
- Do not use "[Your Name]" in the signature.

Tone:
- Executive, clear, warm, concise, and human-like.
- Adapt tone based on relationship context, decision role, and engagement temperature.
- If relationship_type is referral_partner or outbound_prospect, lean more toward business-development language.
- If relationship_type is inbound_lead or existing_client, lean more toward advisory and qualification language.
- If engagement_temperature is hot, be more direct and action-oriented.
- If engagement_temperature is cold, be more consultative and lower-pressure.
- If qualification_stage is needs_info or discovery, proactively offer the user our standard Intake Questionnaire. Provide a generic link: 'Please complete our quick [Intake Questionnaire](/client-intake) to help us prepare a custom estimate.'

Context:
- Channel context (Email vs SMS vs Website): {channel}
- Contact name: {contact_name}
- Agent/Sender name: {agent_name}
- Role type: {role_type}
- Relationship context (Inbound vs Outbound vs Referral): {relationship_type}
- Engagement temperature (Cold vs Warm vs Hot): {engagement_temperature}
- Decision role (Decision Maker vs Influencer vs Researcher vs Assistant): {decision_role}
- Booking URL (if available): {booking_url}
- Persona guidance: {persona_policy}
- Conversation turn: {conversation_turn}
- Recent context summary: {recent_context}
- Missing fields: {missing_fields}
- Filled fields: {filled_fields}
- Estimator guidance: {estimator_context}
- CTA policy: {cta_policy}

FAQ / knowledge excerpts:
{rag_context}

Internal notes (do not repeat verbatim; use only to tailor tone and next step): {decision_json}

User message:
{user_text}
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

    if re.search(r"\b(ecommerce|e-commerce|shop|store|website|app|office|renew|construction)\b", text):
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
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY required")

    llm = ChatOpenAI(
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_CHAT_MODEL,
        temperature=0.2,
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
        
        # Fetch instructions from DB for the chosen stage
        stage_record = session.exec(
            select(PipelineStage).where(PipelineStage.key == stage_key)
        ).first()
        stage_instruction = stage_record.ai_instructions if stage_record else ""

        # Stage-driven prompt logic
        persona_segment = classification.intent_type if classification else "unknown"
        if persona_segment not in PERSONA_POLICIES:
            persona_segment = "unknown"
        persona_policy = PERSONA_POLICIES[persona_segment]

        prompt = REPLY_PROMPT.format(
            channel=state.get("channel", "website"),
            contact_name=state.get("contact_name", "there"),
            agent_name=state.get("agent_name", "the Partnership Team"),
            relationship_type="inbound_lead", # Default
            engagement_temperature="warm",
            decision_role="decision_maker",
            rag_context=state.get("rag_context", ""),
            decision_json=json.dumps(classification.model_dump()) if classification else "{}",
            user_text=state.get("user_text", ""),
            booking_url=state.get("booking_url", ""),
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
        interrupt("Human intervention required. Escalated by AI.")
        return Command(goto="retrieve")

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


def run_ai_pipeline(
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

    graph = build_graph(session)
    initial: AgentState = {
        "user_text": user_text,
        "is_proactive": is_proactive,
        "channel": channel,
        "json_snapshot": json.dumps(json_response, default=str)[:12000],
        "booking_url": settings.CALENDLY_BOOKING_URL or "",
        "conversation_turn": max(1, conversation_turn),
        "recent_context": recent_context,
        "filled_fields": ", ".join(sorted(slots.keys())),
        "estimator_context": estimator_context,
        "contact_id": contact.id,
        "conversation_id": conversation.id,
        "contact_name": contact.username or "there",
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
