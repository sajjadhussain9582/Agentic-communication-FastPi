"""One-time script: backfill the pgvector `embedding` column from existing embedding_json."""

import json
from sqlalchemy import text
from sqlmodel import Session, select
from app.core.database import engine
from app.models.knowledge_base import KnowledgeBaseEntry
from app.services.kb_rag import embed_single


def run_backfill():
    print("Backfilling vector embeddings...")
    with Session(engine) as session:
        rows = session.exec(select(KnowledgeBaseEntry)).all()
        count = 0
        for row in rows:
            # Re-embed with keywords/tags for better vectors
            combined = f"Question: {row.question}\nAnswer: {row.answer}"
            if getattr(row, "keywords", None) and isinstance(row.keywords, list):
                combined += f"\nKeywords: {', '.join(row.keywords)}"
            if getattr(row, "tags", None) and isinstance(row.tags, list):
                combined += f"\nTags: {', '.join(row.tags)}"

            vec = embed_single(combined)

            # Write to embedding_json (backward compat)
            row.embedding_json = json.dumps(vec)

            # Write to pgvector embedding column
            vec_str = "[" + ",".join(str(v) for v in vec) + "]"
            session.execute(
                text("UPDATE knowledge_base SET embedding = :vec WHERE id = :id"),
                {"vec": vec_str, "id": row.id},
            )
            session.add(row)
            count += 1
            print(f"  [{count}/{len(rows)}] Backfilled: {row.question[:50]}...")

        session.commit()
    print(f"Backfill complete! {count} rows updated.")


if __name__ == "__main__":
    run_backfill()
