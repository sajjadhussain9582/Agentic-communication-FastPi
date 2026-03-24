# Form-driven AI agent API (Phase 1)

## Env

- `OPENAI_API_KEY` — required for RAG + LangGraph replies.
- `FORM_SUBMIT_API_KEY` — optional; if set, `POST .../submit` requires header `X-API-Key: <value>`.

## Endpoints (prefix `/api/v1`)

| Method | Path | Auth |
|--------|------|------|
| POST | `/forms` | Bearer (staff) — create form |
| GET | `/forms` | Bearer |
| GET | `/forms/{id}` | Bearer |
| POST | `/forms/{id}/submit` | Optional `X-API-Key` if configured |
| GET | `/knowledge-base` | Bearer |
| POST | `/knowledge-base` | Bearer — creates FAQ + embedding |
| PATCH | `/knowledge-base/{id}` | Bearer |
| DELETE | `/knowledge-base/{id}` | Bearer |
| GET | `/conversations/{id}` | Bearer |

## Submit body example

```json
{
  "channel": "website",
  "name": "Jane",
  "email": "jane@example.com",
  "message": "What are your hours and how do I book a call?"
}
```

Response includes `ai_reply`, `conversation_id`, `rag_kb_ids` (FAQ rows used).

On first startup, a default form (id usually `1`) and sample FAQ rows are seeded if tables are empty.
