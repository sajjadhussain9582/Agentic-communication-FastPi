"""Import expanded KB data from CSV, clearing old entries and generating embeddings."""

import csv
import json
import ast
from sqlmodel import Session, select
from app.core.database import engine
from app.models.knowledge_base import KnowledgeBaseEntry
from app.services.kb_rag import ensure_entry_embedding

CSV_PATH = "docs/ai_agent_kb_expanded.csv"

def parse_json_list(value: str) -> list:
    """Safely parse a string like '["a", "b"]' into a Python list."""
    if not value or value.strip() == "":
        return []
    try:
        # Try JSON first
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            # Fallback to AST eval if it's formatted like a Python list
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, list) else []
        except:
            return []

def run_import():
    print(f"Reading from {CSV_PATH}...")
    
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} entries in CSV.")

    print("Connecting to database and clearing old KB entries...")
    with Session(engine) as session:
        old_entries = session.exec(select(KnowledgeBaseEntry)).all()
        for oe in old_entries:
            session.delete(oe)
        session.commit()
        print(f"Cleared {len(old_entries)} old entries.")

        print("Importing new entries and generating embeddings (this will take a moment)...")
        for i, row in enumerate(rows, 1):
            # Parse keywords and tags which are stringified lists in CSV
            keywords = parse_json_list(row.get("keywords", "[]"))
            tags = parse_json_list(row.get("tags", "[]"))
            
            entry = KnowledgeBaseEntry(
                question=row["question"].strip(),
                answer=row["answer"].strip(),
                category=row.get("category", "").strip() or None,
                intent_type=row.get("intent_type", "").strip() or None,
                role_type=row.get("role_type", "").strip() or None,
                priority=int(row.get("priority", 0) or 0),
                keywords=keywords,
                tags=tags
            )
            
            # This generates the OpenAI embedding, writes to JSON_column, AND writes to pgvector column
            ensure_entry_embedding(session, entry)
            print(f"  [{i}/{len(rows)}] Added: {entry.question[:50]}...")

    print("Import and embedding complete!")

if __name__ == "__main__":
    run_import()
