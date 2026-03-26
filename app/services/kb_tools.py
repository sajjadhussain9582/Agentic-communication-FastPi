"""LangChain tool definitions for Knowledge Base retrieval."""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool
from sqlmodel import Session

from app.core.database import engine
from app.services.kb_rag import search_similar, format_rag_context

logger = logging.getLogger(__name__)


# ── Generic KB search tool ──────────────────────────────────────────────────

@tool
def search_knowledge_base(query: str, category: Optional[str] = None, top_k: int = 5) -> str:
    """Search the knowledge base for relevant information.
    Use this tool to find answers about services, pricing, MVPs, timelines,
    partnerships, consultations, and more.

    Args:
        query: The search query describing what information is needed.
        category: Optional category filter (e.g., 'pricing', 'mvp_guidance').
        top_k: Number of results to return (default 5).
    """
    with Session(engine) as session:
        chunks, _ = search_similar(session, query, top_k=top_k, category=category)
        return format_rag_context(chunks)


# ── Specialized category tools ──────────────────────────────────────────────

@tool
def search_pricing_guidance(query: str) -> str:
    """Search for pricing, budget, and cost-related guidance.
    Use this when the user asks about costs, budgets, discounts, or pricing flexibility.
    """
    with Session(engine) as session:
        chunks_p, _ = search_similar(session, query, top_k=3, category="pricing")
        chunks_b, _ = search_similar(session, query, top_k=2, category="budget_flexibility")
        all_chunks = chunks_p + chunks_b
        all_chunks.sort(key=lambda c: (c.priority, c.score), reverse=True)
        return format_rag_context(all_chunks[:5])


@tool
def search_mvp_guidance(query: str) -> str:
    """Search for MVP, feature planning, and early-stage project guidance.
    Use this when the user is unclear about features, wants to start small, or asks about MVPs.
    """
    with Session(engine) as session:
        chunks_m, _ = search_similar(session, query, top_k=3, category="mvp_guidance")
        chunks_f, _ = search_similar(session, query, top_k=2, category="feature_suggestions")
        all_chunks = chunks_m + chunks_f
        all_chunks.sort(key=lambda c: (c.priority, c.score), reverse=True)
        return format_rag_context(all_chunks[:5])


@tool
def search_partnership_guidance(query: str) -> str:
    """Search for partnership, referral, and collaboration information.
    Use this when the user asks about working together, contractor referrals, or partnerships.
    """
    with Session(engine) as session:
        chunks, _ = search_similar(session, query, top_k=5, category="partnership")
        return format_rag_context(chunks)


@tool
def search_booking_guidance(query: str) -> str:
    """Search for consultation booking and scheduling information.
    Use this when the user wants to schedule a meeting, book a consultation, or asks about next steps.
    """
    with Session(engine) as session:
        chunks_c, _ = search_similar(session, query, top_k=3, category="consultation")
        chunks_p, _ = search_similar(session, query, top_k=2, category="process")
        all_chunks = chunks_c + chunks_p
        all_chunks.sort(key=lambda c: (c.priority, c.score), reverse=True)
        return format_rag_context(all_chunks[:5])


@tool
def search_platform_guidance(query: str) -> str:
    """Search for website vs app guidance, platform type recommendations, and real estate platform info.
    Use this when the user asks about websites, apps, platforms, or real estate technology.
    """
    with Session(engine) as session:
        chunks_w, _ = search_similar(session, query, top_k=2, category="website_guidance")
        chunks_a, _ = search_similar(session, query, top_k=2, category="app_guidance")
        chunks_r, _ = search_similar(session, query, top_k=2, category="real_estate_platform")
        all_chunks = chunks_w + chunks_a + chunks_r
        all_chunks.sort(key=lambda c: (c.priority, c.score), reverse=True)
        return format_rag_context(all_chunks[:5])


# ── Tool registry ──────────────────────────────────────────────────────────

ALL_KB_TOOLS = [
    search_knowledge_base,
    search_pricing_guidance,
    search_mvp_guidance,
    search_partnership_guidance,
    search_booking_guidance,
    search_platform_guidance,
]
