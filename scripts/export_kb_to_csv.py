"""
Export Knowledge Base (RAG) entries from the DB to a CSV file in docs/.

Usage:
  python scripts/export_kb_to_csv.py

Notes:
  - Uses DATABASE_URL from your existing `.env` via app.core.config.settings.
  - Writes to: docs/knowledge_base_export.csv
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

from sqlmodel import Session, select

repo_root = Path(__file__).resolve().parents[1]
# Allow running as a script without needing PYTHONPATH=.
sys.path.insert(0, str(repo_root))

from app.core.database import engine  # noqa: E402
from app.models.knowledge_base import KnowledgeBaseEntry  # noqa: E402


def _json_cell(value) -> str:
    """Serialize lists/dicts for CSV cells."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def export_kb_to_csv(out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with Session(engine) as session:
        rows = list(session.exec(select(KnowledgeBaseEntry).order_by(KnowledgeBaseEntry.id)).all())

    fieldnames = [
        "id",
        "uuid",
        "question",
        "answer",
        "category",
        "keywords",
        "tags",
        "intent_type",
        "role_type",
        "priority",
        "has_embedding_json",
        "created_at",
    ]

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "id": r.id,
                    "uuid": r.public_uuid,
                    "question": r.question,
                    "answer": r.answer,
                    "category": r.category or "",
                    "keywords": _json_cell(getattr(r, "keywords", None)),
                    "tags": _json_cell(getattr(r, "tags", None)),
                    "intent_type": r.intent_type or "",
                    "role_type": r.role_type or "",
                    "priority": r.priority,
                    "has_embedding_json": bool(r.embedding_json),
                    "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else "",
                }
            )

    return len(rows)


if __name__ == "__main__":
    out = repo_root / "docs" / "knowledge_base_export.csv"
    count = export_kb_to_csv(out)
    print(f"Exported {count} knowledge_base rows to {out}")
