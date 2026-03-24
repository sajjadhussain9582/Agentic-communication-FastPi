"""LangGraph pipeline: RAG → structured decision → FAQ-grounded reply."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from sqlmodel import Session, select

from app.core.config import settings
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.estimator import estimate_project
from app.services.kb_rag import format_rag_context, search_similar
from app.services.workflow_engine import run_decision_workflows

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    user_text: str
    channel: str
    json_snapshot: str
    rag_context: str
    rag_kb_ids: list[int]
    decision_json: str
    reply_text: str
    booking_url: str
    conversation_turn: int
    recent_context: str
    missing_fields: str
    filled_fields: str
    estimator_context: str
    should_include_cta: bool
    error: str


PERSONA_POLICIES: dict[str, dict[str, str]] = {
    "contractor": {
        "focus": "crew capacity, project volume, subcontracting model, turnaround expectations",
        "questions": "service area, project size, monthly lead capacity, preferred engagement model",
        "cta": "Offer a short partnership planning call with available time windows.",
    },
    "agent": {
        "focus": "listing pipeline, buyer/seller mix, referral collaboration, communication cadence",
        "questions": "markets served, transaction volume, referral goals, lead handoff preferences",
        "cta": "Invite them to a co-marketing or referral alignment call.",
    },
    "developer": {
        "focus": "portfolio scope, timelines, multi-project rollout, partner SLA requirements",
        "questions": "project phases, decision stakeholders, budget range, deployment timeline",
        "cta": "Propose a discovery call with implementation and partnership stakeholders.",
    },
    "architect": {
        "focus": "design collaboration flow, proposal cycles, technical coordination needs",
        "questions": "project type, design stage, collaboration expectations, timeline and budget context",
        "cta": "Offer a design-collaboration discovery call.",
    },
    "builder": {
        "focus": "build pipeline, vendor/partner coordination, scheduling and quality expectations",
        "questions": "build volume, locations, current process bottlenecks, timeline and budget",
        "cta": "Suggest a partnership kickoff call to map the first pilot process.",
    },
    "unknown": {
        "focus": "business partnership potential and qualification for next steps",
        "questions": "role, geography, project scope, budget, timeline, and decision authority",
        "cta": "Ask for a brief consult call and preferred availability.",
    },
}


DECISION_PROMPT = """You are an internal analyst for a B2B services company focused on growth partnerships.
Based on the user's message and form data, output ONLY valid JSON with these keys:
- last_intent: short string (e.g. pricing, support, new_lead, partnership, follow_up)
- persona_segment: one of: contractor, agent, developer, architect, builder, unknown
- qualification_stage: one of: discovery, qualified, not_qualified, needs_info
- is_escalated: true if a human should handle this (legal, complaints, highly complex, or user explicitly asks for human)
- conversation_status: one of: open, pending_human, closed
- lead_score: number 0-100 or null if unknown
- is_qualified: boolean or null
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
- Prefer partnership/business development framing when applicable.
- Infer persona_segment from message/context; use unknown when unclear.
- Keep values concise and factual.
- Infer a practical next_action that moves the deal forward.
- Favor moving from discovery -> estimation_ready -> proposal_ready when enough detail exists.
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
- Ground factual claims ONLY in the FAQ/knowledge excerpts below. If something is not in the excerpts, do not make up policies, prices, or guarantees.
- Always position the company as a connector that introduces vetted partners across contractors, real estate agents, developers, architects, and home builders.
- Do not claim your team directly provides architecture/design/construction execution.
- Do not use internal platform positioning as the core customer offer (avoid presenting automation/lead-qualification tooling as direct service delivery).
- If excerpts are missing or insufficient, keep confidence low but still provide a concrete next step.
- Avoid defensive phrasing such as "we do not handle this" or "we don't provide that."
- Turn behavior:
  - If conversation_turn == 1: use a short greeting and opening.
  - If conversation_turn > 1: no repeated greeting/signature; continue naturally from prior context.
  - Do not end every message with a meeting ask.
  - Ask only the next best 1-2 questions from missing_fields/next_best_questions.
  - Max 1-2 follow-up questions per turn.
  - If user asks budget/time, answer with concrete ranges in this same reply before any follow-up question.
  - Do not ask for slots already present in filled_fields.
  - If stage is proposal_ready/close_ready, prioritize close action (shortlist, proposal, or booking) over new discovery questions.
  - Briefly reference the user's latest details naturally.
- If user asks for partner contact details directly, do not dump raw personal phone/email data. Offer curated shortlist + warm intro workflow.
- Tone: executive, clear, warm, concise, and human-like (not templated).
- Channel context: {channel}
- Booking URL (if available): {booking_url}
- Persona segment: {persona_segment}
- Persona guidance: {persona_policy}
- Conversation turn: {conversation_turn}
- Recent context summary: {recent_context}
- Missing fields: {missing_fields}
- Filled fields: {filled_fields}
- Estimator guidance: {estimator_context}
- CTA policy: {cta_policy}

FAQ / knowledge excerpts:
{rag_context}

Internal notes (do not repeat verbatim; use to tailor tone): {decision_json}

User message:
{user_text}
"""


def _parse_decision(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def should_include_cta(
    *,
    conversation_turn: int,
    cta_readiness_score: int | float | None,
    missing_fields: list[str],
    conversation_stage: str | None,
) -> bool:
    if conversation_turn <= 1:
        return True
    stage = (conversation_stage or "").strip().lower()
    score = float(cta_readiness_score or 0)
    if stage in {"proposal_ready", "close_ready"}:
        return True
    if score >= 70 and len(missing_fields) <= 2:
        return True
    return False


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

    def node_retrieve(state: AgentState) -> dict[str, Any]:
        q = state.get("user_text", "")[:8000]
        chunks, kb_ids = search_similar(session, q, top_k=5, min_score=0.2)
        return {
            "rag_context": format_rag_context(chunks),
            "rag_kb_ids": kb_ids,
        }

    def node_decide(state: AgentState) -> dict[str, Any]:
        prompt = DECISION_PROMPT.format(
            json_snapshot=state.get("json_snapshot", "{}"),
            user_text=state.get("user_text", ""),
            rag_context=state.get("rag_context", ""),
        )
        out = llm.invoke([HumanMessage(content=prompt)])
        text = out.content if hasattr(out, "content") else str(out)
        try:
            d = _parse_decision(text)
            missing_fields = _safe_list(d.get("missing_fields"))
            filled_fields = _safe_list(d.get("filled_fields"))
            conversation_stage = str(d.get("conversation_stage") or "discovery")
            include_cta = should_include_cta(
                conversation_turn=int(state.get("conversation_turn", 1)),
                cta_readiness_score=d.get("cta_readiness_score"),
                missing_fields=missing_fields,
                conversation_stage=conversation_stage,
            )
            return {
                "decision_json": json.dumps(d),
                "missing_fields": ", ".join(missing_fields),
                "filled_fields": ", ".join(filled_fields),
                "should_include_cta": include_cta,
            }
        except json.JSONDecodeError:
            return {
                "decision_json": json.dumps(
                    {
                        "last_intent": "unknown",
                        "persona_segment": "unknown",
                        "qualification_stage": "needs_info",
                        "conversation_stage": "discovery",
                        "missing_fields": ["scope", "budget", "timeline", "location", "stakeholders"],
                        "next_best_questions": ["Could you share the project scope and timeline?"],
                        "cta_readiness_score": 30,
                        "is_escalated": False,
                        "conversation_status": "open",
                    }
                ),
                "missing_fields": "scope, budget, timeline, location, stakeholders",
                "filled_fields": "",
                "should_include_cta": False,
            }

    def node_reply(state: AgentState) -> dict[str, Any]:
        decision_obj: dict[str, Any] = {}
        try:
            decision_obj = json.loads(state.get("decision_json", "{}"))
        except json.JSONDecodeError:
            decision_obj = {}
        persona_segment = str(decision_obj.get("persona_segment") or "unknown")
        if persona_segment not in PERSONA_POLICIES:
            persona_segment = "unknown"
        persona_policy = PERSONA_POLICIES[persona_segment]
        conversation_turn = int(state.get("conversation_turn", 1))
        missing_fields = state.get("missing_fields", "")
        filled_fields = state.get("filled_fields", "")
        include_cta = bool(state.get("should_include_cta", True))
        cta_policy = (
            "Include CTA naturally with one concrete next step."
            if include_cta
            else "Do not include meeting CTA this turn; continue discovery naturally."
        )

        prompt = REPLY_PROMPT.format(
            channel=state.get("channel", "website"),
            rag_context=state.get("rag_context", ""),
            decision_json=state.get("decision_json", "{}"),
            user_text=state.get("user_text", ""),
            booking_url=state.get("booking_url", ""),
            persona_segment=persona_segment,
            persona_policy=json.dumps(persona_policy),
            conversation_turn=conversation_turn,
            recent_context=state.get("recent_context", ""),
            missing_fields=missing_fields,
            filled_fields=filled_fields,
            estimator_context=state.get("estimator_context", ""),
            cta_policy=cta_policy,
        )
        out = llm.invoke(
            [
                SystemMessage(
                    content="You write clear, business-professional client replies. No markdown code fences."
                ),
                HumanMessage(content=prompt),
            ]
        )
        reply = out.content if hasattr(out, "content") else str(out)
        return {"reply_text": (reply or "").strip()}

    g = StateGraph(AgentState)
    g.add_node("retrieve", node_retrieve)
    g.add_node("decide", node_decide)
    g.add_node("reply", node_reply)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "decide")
    g.add_edge("decide", "reply")
    g.add_edge("reply", END)
    return g.compile()


def run_ai_pipeline(
    session: Session,
    conversation: Conversation,
    contact: Contact,
    inbound_message: Message,
    json_response: dict,
) -> Message:
    """Runs graph, updates contact/conversation, creates outbound Message."""
    user_text = inbound_message.message
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
        "channel": channel,
        "json_snapshot": json.dumps(json_response, default=str)[:12000],
        "booking_url": settings.CALENDLY_BOOKING_URL or "",
        "conversation_turn": max(1, conversation_turn),
        "recent_context": recent_context,
        "missing_fields": "",
        "filled_fields": ", ".join(sorted(slots.keys())),
        "estimator_context": estimator_context,
        "should_include_cta": True,
    }
    final = graph.invoke(initial)

    decision = {}
    try:
        decision = json.loads(final.get("decision_json") or "{}")
    except json.JSONDecodeError:
        pass

    conversation.last_intent = str(decision.get("last_intent") or "unknown")[:500]
    conversation.qualification_stage = str(
        decision.get("qualification_stage") or "needs_info"
    )[:200]
    conversation.is_escalated = bool(decision.get("is_escalated"))
    conversation.status = str(decision.get("conversation_status") or "open")[:100]
    from datetime import datetime

    conversation.updated_at = datetime.utcnow()

    if decision.get("lead_score") is not None:
        try:
            contact.lead_score = Decimal(str(decision["lead_score"]))
        except Exception:
            pass
    if decision.get("is_qualified") is not None:
        contact.is_qualified = bool(decision["is_qualified"])
    for fld, key in (
        ("budget", "budget"),
        ("project_type", "project_type"),
        ("timeline", "timeline"),
    ):
        v = decision.get(key)
        if v is not None and str(v).strip():
            setattr(contact, fld, str(v)[:2000])

    missing_fields = _safe_list(decision.get("missing_fields"))
    conversation_stage = str(decision.get("conversation_stage") or "discovery")
    cta_readiness_score = decision.get("cta_readiness_score")
    close_readiness_score = decision.get("close_readiness_score")
    next_action = str(decision.get("next_action") or "ask_missing_info")
    include_cta = should_include_cta(
        conversation_turn=max(1, conversation_turn),
        cta_readiness_score=cta_readiness_score,
        missing_fields=missing_fields,
        conversation_stage=conversation_stage,
    )
    # Persist lightweight state for turn-aware behavior in future messages.
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    ext["ai_state"] = {
        "conversation_stage": conversation_stage,
        "missing_fields": missing_fields,
        "filled_fields": _safe_list(decision.get("filled_fields")),
        "next_best_questions": _safe_list(decision.get("next_best_questions")),
        "cta_readiness_score": cta_readiness_score,
        "close_readiness_score": close_readiness_score,
        "next_action": next_action,
    }
    ext["slots"] = slots
    contact.external_ids = ext
    contact.updated_at = datetime.utcnow()
    run_decision_workflows(
        session,
        conversation=conversation,
        contact=contact,
        decision=decision,
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
            final.get("reply_text")
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
    inbound_message.is_handled = True
    session.add(inbound_message)
    session.commit()
    session.refresh(outbound)
    _log_reply_quality(
        decision=decision,
        reply_text=outbound.message,
        conversation_turn=max(1, conversation_turn),
        include_cta=include_cta,
    )
    return outbound


def _log_reply_quality(
    decision: dict[str, Any],
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
    logger.info(
        "reply_quality persona=%s intent=%s turn=%s cta_policy=%s cta_present=%s qualification_prompts_present=%s defensive_phrase=%s",
        decision.get("persona_segment", "unknown"),
        decision.get("last_intent", "unknown"),
        conversation_turn,
        include_cta,
        has_cta,
        has_qualifier_prompt,
        has_defensive_phrase,
    )
