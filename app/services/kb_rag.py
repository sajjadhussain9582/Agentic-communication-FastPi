"""RAG retrieval over knowledge_base using pgvector (Postgres) or Python fallback."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.models.knowledge_base import KnowledgeBaseEntry

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    id: int
    question: str
    answer: str
    category: str | None
    score: float
    intent_type: str | None = None
    role_type: str | None = None
    priority: int = 0


# ── Embedding helpers ───────────────────────────────────────────────────────

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Python fallback cosine similarity."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is required for embeddings")
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    resp = client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in resp.data]


def embed_single(text_str: str) -> list[float]:
    return embed_texts([text_str])[0]


def _build_combined_text(entry: KnowledgeBaseEntry) -> str:
    """Build embedding input text from entry fields."""
    combined = f"Question: {entry.question}\nAnswer: {entry.answer}"
    if getattr(entry, "keywords", None) and isinstance(entry.keywords, list):
        combined += f"\nKeywords: {', '.join(entry.keywords)}"
    if getattr(entry, "tags", None) and isinstance(entry.tags, list):
        combined += f"\nTags: {', '.join(entry.tags)}"
    return combined


def ensure_entry_embedding(session: Session, entry: KnowledgeBaseEntry) -> None:
    """Generate embedding and write to BOTH embedding_json AND pgvector column."""
    combined = _build_combined_text(entry)
    vec = embed_single(combined)

    # Legacy JSON column
    entry.embedding_json = json.dumps(vec)
    session.add(entry)
    session.commit()
    session.refresh(entry)

    # pgvector column (raw SQL because SQLModel doesn't natively map vector types)
    try:
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"
        session.execute(
            text("UPDATE knowledge_base SET embedding = :vec WHERE id = :id"),
            {"vec": vec_str, "id": entry.id},
        )
        session.commit()
    except Exception:
        pass  # pgvector column may not exist yet (first startup)


# ── Retrieval ───────────────────────────────────────────────────────────────

def search_similar(
    session: Session,
    query: str,
    top_k: int = 5,
    min_score: float = 0.35,
    category: str | None = None,
) -> tuple[list[RetrievedChunk], list[int]]:
    """Search KB via Postgres match_knowledge_base(), with Python fallback."""
    try:
        qvec = embed_single(query)
    except Exception:
        logger.warning("Failed to embed query: %s", query[:80])
        return [], []

    # ── Try pgvector path first ─────────────────────────────────────────
    try:
        vec_str = "[" + ",".join(str(v) for v in qvec) + "]"
        result = session.execute(
            text(
                "SELECT * FROM match_knowledge_base(:qe, :mc, :fc)"
            ),
            {"qe": vec_str, "mc": top_k * 2, "fc": category},
        )
        rows = result.fetchall()

        chunks = []
        for row in rows:
            sim = float(row.similarity)
            if sim < min_score:
                continue
            chunks.append(RetrievedChunk(
                id=int(row.id),
                question=row.question,
                answer=row.answer,
                category=row.category,
                score=sim,
                intent_type=row.intent_type,
                role_type=row.role_type,
                priority=row.priority or 0,
            ))

        # Sort by priority desc, then similarity desc
        chunks.sort(key=lambda c: (c.priority, c.score), reverse=True)
        chunks = chunks[:top_k]

        if chunks:
            logger.info(
                "KB retrieval (pgvector): query='%s' category=%s top_score=%.3f top_id=%s",
                query[:60], category, chunks[0].score, chunks[0].id,
            )
            return chunks, [c.id for c in chunks]

        logger.info("KB retrieval (pgvector): no results above threshold %.2f", min_score)

    except Exception as e:
        logger.warning("pgvector search failed, falling back to Python: %s", e)

    # ── Python fallback ─────────────────────────────────────────────────
    all_rows = session.exec(select(KnowledgeBaseEntry)).all()
    with_embedding = [r for r in all_rows if r.embedding_json]
    if not with_embedding:
        return [], []

    scored: list[tuple[KnowledgeBaseEntry, float]] = []
    for row in with_embedding:
        if category and getattr(row, "category", None) != category:
            continue
        try:
            ev = json.loads(row.embedding_json)
            s = _cosine(qvec, ev)
            if s >= min_score:
                scored.append((row, s))
        except (json.JSONDecodeError, TypeError):
            continue

    scored.sort(key=lambda x: (getattr(x[0], "priority", 0), x[1]), reverse=True)
    top = scored[:top_k]
    chunks = [
        RetrievedChunk(
            id=r.id,
            question=r.question,
            answer=r.answer,
            category=r.category,
            score=s,
            intent_type=getattr(r, "intent_type", None),
            role_type=getattr(r, "role_type", None),
            priority=getattr(r, "priority", 0),
        )
        for r, s in top
    ]

    if chunks:
        logger.info(
            "KB retrieval (fallback): query='%s' top_score=%.3f top_id=%s",
            query[:60], chunks[0].score, chunks[0].id,
        )

    return chunks, [c.id for c in chunks]


# ── Formatting ──────────────────────────────────────────────────────────────

def format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return (
            "(No close knowledge base match was found. Keep claims factual and avoid inventing policies or prices. "
            "Present the company as a connector to vetted contractors, real estate agents, developers, architects, and home builders. "
            "Ask for scope/budget/timeline context, and offer a concrete consultation next step.)"
        )
    parts = []
    for i, c in enumerate(chunks, 1):
        meta = f"id={c.id}, category={c.category or 'general'}, intent={c.intent_type or 'any'}, priority={c.priority}, score={c.score:.2f}"
        parts.append(
            f"[FAQ {i}] ({meta})\nQ: {c.question}\nA: {c.answer}"
        )
    return "\n\n".join(parts)
