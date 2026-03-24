"""RAG retrieval over knowledge_base using OpenAI embeddings and cosine similarity."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Sequence

from sqlmodel import Session, select

from app.core.config import settings
from app.models.knowledge_base import KnowledgeBaseEntry


@dataclass
class RetrievedChunk:
    id: int
    question: str
    answer: str
    category: str | None
    score: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
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


def embed_single(text: str) -> list[float]:
    return embed_texts([text])[0]


def ensure_entry_embedding(session: Session, entry: KnowledgeBaseEntry) -> None:
    combined = f"{entry.question}\n{entry.answer}"
    vec = embed_single(combined)
    entry.embedding_json = json.dumps(vec)
    session.add(entry)
    session.commit()
    session.refresh(entry)


def search_similar(
    session: Session,
    query: str,
    top_k: int = 5,
    min_score: float = 0.2,
) -> tuple[list[RetrievedChunk], list[int]]:
    """Returns ranked chunks and list of kb ids used."""
    rows = session.exec(select(KnowledgeBaseEntry)).all()
    with_embedding = [r for r in rows if r.embedding_json]
    if not with_embedding:
        return [], []

    try:
        qvec = embed_single(query)
    except Exception:
        return [], []

    scored: list[tuple[KnowledgeBaseEntry, float]] = []
    for row in with_embedding:
        try:
            ev = json.loads(row.embedding_json)
            s = _cosine(qvec, ev)
            if s >= min_score:
                scored.append((row, s))
        except (json.JSONDecodeError, TypeError):
            continue

    # Prefer higher similarity and richer entries for near ties.
    scored.sort(key=lambda x: (x[1], len(x[0].answer or "")), reverse=True)
    if not scored:
        # Fallback to best-near matches if embeddings are close but below threshold.
        near = []
        for row in with_embedding:
            try:
                ev = json.loads(row.embedding_json)
                s = _cosine(qvec, ev)
                if s >= max(0.0, min_score - 0.05):
                    near.append((row, s))
            except (json.JSONDecodeError, TypeError):
                continue
        near.sort(key=lambda x: (x[1], len(x[0].answer or "")), reverse=True)
        scored = near
    top = scored[:top_k]
    chunks = [
        RetrievedChunk(
            id=r.id,
            question=r.question,
            answer=r.answer,
            category=r.category,
            score=s,
        )
        for r, s in top
    ]
    return chunks, [c.id for c in chunks]


def format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return (
            "(No close knowledge base match was found. Keep claims factual and avoid inventing policies or prices. "
            "Present the company as a connector to vetted contractors, real estate agents, developers, architects, and home builders. "
            "Ask for scope/budget/timeline context, and offer a concrete consultation next step.)"
        )
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[FAQ {i}] (id={c.id}, category={c.category or 'general'})\nQ: {c.question}\nA: {c.answer}"
        )
    return "\n\n".join(parts)
