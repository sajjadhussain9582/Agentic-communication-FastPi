# Backend API contracts — Phase 2 + UUID handoff

This document is for frontend/mobile clients. It describes **what exists today**, **Phase 2 additions**, **UUID rules**, and **breaking / additive changes**.

Base URL prefix: **`/api/v1`** (unless noted).

---

## Auth

| Area | Rule |
|------|------|
| Staff / authenticated routes | `Authorization: Bearer <access_token>` (login/register flows as today). |
| Public form submit | `POST /forms/{form_id}/submit` — if `FORM_SUBMIT_API_KEY` is set in env, send `X-API-Key: <key>`. |
| GHL webhooks | If `GHL_WEBHOOK_SECRET` is set, send matching value as `X-GHL-Secret` or `Authorization: Bearer <secret>`. If unset, webhooks accept any caller (dev only). |

---

## UUID transition (Path A — compat)

- **Integer primary keys (`id`) remain** in the database and in many responses for backward compatibility.
- **Public UUIDs** are stored in column `uuid` (API field name **`uuid`** on forms, KB, conversations, messages, contacts, etc.).
- **Frontend should prefer `uuid`** for new features: URLs, caching, cross-service references.
- **Deprecated pattern**: relying only on numeric `id` for conversations/contacts/messages in new UI flows. Numeric IDs still work where documented.

**Form submit response** now includes (in addition to legacy int IDs):

- `submission_uuid`, `contact_uuid`, `conversation_uuid`, `inbound_message_uuid`, `ai_reply_message_uuid`

---

## Implemented today (baseline + Phase 2)

### Users / auth

- `POST /users/register`, `POST /users/login`, `GET /users/me` (unchanged semantics).

### Knowledge base

- `GET/POST /knowledge-base`, `PATCH/DELETE /knowledge-base/{entry_id}` (Bearer).
- Responses include **`uuid`** per entry (plus legacy **`id`**).

### Forms

- `POST /forms`, `GET /forms`, `GET /forms/{form_id}` (Bearer).
- Responses include **`uuid`** (plus **`id`**).
- `POST /forms/{form_id}/submit` (public + optional API key).

### Conversations / messaging (Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/conversations` | **Inbox list.** Query: `cursor`, `limit`, `channel`, `status`, `is_escalated`. |
| `GET` | `/conversations/{conversation_ref}` | **Thread.** `conversation_ref` = **UUID** *or* legacy **integer id**. |
| `POST` | `/conversations/{conversation_uuid}/messages` | **Staff reply.** Body: `message`, `sender_type` (default `human`), `channel` (optional). UUID only. |

**Inbox item shape (summary):**

- `uuid`, `contact_preview` (`uuid`, `email`, `username`, `company`), `last_message_preview`, `updated_at`, `is_escalated`, `channel`, `status`

**Thread shape:**

- `id`, `uuid`, `contact_id`, `contact_uuid`, `channel`, `status`, `is_escalated`, `last_intent`, `qualification_stage`
- `messages[]`: `id`, `uuid`, `conversation_uuid`, `sender_type`, `message`, `channel`, `is_generated`, `rag_source_kb_ids`, `created_at`

### Contacts CRM (Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/contacts` | List: `search`, `cursor`, `limit`, `channel` (filters `source`), `stage`, `tag`. |
| `GET` | `/contacts/{contact_uuid}` | Detail + **`conversation_uuids`**. |
| `PATCH` | `/contacts/{contact_uuid}` | `stage`, `tags`, `notes`, `assigned_user_id`, `external_ids` (merged), `status`. |

Contact fields include: `tags` (JSON array), `notes`, `stage`, `external_ids` (e.g. `ghl_contact_id`).

### Campaigns & templates (Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/campaigns` | List campaigns. |
| `GET` | `/campaigns/{campaign_uuid}` | Detail + target preview. |
| `POST` | `/campaigns` | Create draft (`name`, `channel`, `audience_filter`, `scheduled_at`). |
| `PATCH` | `/campaigns/{campaign_uuid}` | Update `name`, `status`, `scheduled_at`, `audience_filter`. |
| `POST` | `/campaigns/{campaign_uuid}/send` | **Stub:** enqueues logged rows; real provider delivery = future phase. |
| `GET` | `/templates` | List message templates. |
| `POST` | `/templates` | Create template (`name`, `channel`, `body`). |
| `GET` | `/templates/{template_id}` | Get by integer **id** (legacy-style; list returns `uuid` for primary use). |

### Integrations & webhooks (Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/integrations` | Status per provider (`ghl` seeded as disconnected). |
| `POST` | `/integrations/{provider}/connect` | Returns **stub** `oauth_url` + instructions. |
| `POST` | `/integrations/{provider}/disconnect` | Sets disconnected. |
| `POST` | `/webhooks/ghl/message` | Inbound; persists **`webhook_events`**. |
| `POST` | `/webhooks/ghl/contact` | Same. |

Webhook handlers **do not create contacts/messages yet** beyond storing events (translation layer can be added later).

---

## Mismatches / notes for frontend

1. **IDs vs UUIDs**: Use **`uuid`** for new navigation (`/conversations/{uuid}`, `/contacts/{uuid}`, …). Thread GET still accepts numeric id for old links.
2. **Forms/KB URLs**: Still use integer `form_id` / `entry_id` on some routes; responses expose **`uuid`** for forward-compatible storage.
3. **Templates**: List uses UUID; get-by-id endpoint still uses integer `template_id` — consider standardizing on UUID in a later release.
4. **Campaign send**: Returns `job_id` and `queued`; delivery is not wired to email/SMS providers yet.

---

## Example JSON

**POST `/conversations/{uuid}/messages`**

```json
{
  "message": "Thanks — we’ll call you tomorrow.",
  "sender_type": "human",
  "channel": "email"
}
```

**PATCH `/contacts/{uuid}`**

```json
{
  "stage": "qualified",
  "tags": ["hot", "enterprise"],
  "notes": "Called 3/18 — interested in enterprise tier.",
  "external_ids": { "ghl_contact_id": "abc123" }
}
```

**GET `/conversations` (snippet)**

```json
[
  {
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
    "contact_preview": {
      "uuid": "...",
      "email": "user@example.com",
      "username": "Jane",
      "company": "Acme"
    },
    "last_message_preview": "Thanks for reaching out…",
    "updated_at": "2025-03-18T12:00:00",
    "is_escalated": false,
    "channel": "website",
    "status": "open"
  }
]
```

---

## Environment (reference)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres (e.g. Supabase) or SQLite. |
| `GHL_WEBHOOK_SECRET` | Optional webhook auth. |
| `PUBLIC_APP_URL` | Used in integration OAuth stub URLs. |
| `FORM_SUBMIT_API_KEY` | Optional lock on public form submit. |

---

## Database (ops)

On startup the app runs **additive DDL** (where supported): `uuid` columns on core tables, CRM columns on `contacts`, `conversation_uuid` on `messages`, plus new tables (`campaigns`, `campaign_targets`, `campaign_messages`, `templates`, `integrations`, `integration_runs`, `webhook_events`). Existing rows are **backfilled** with UUIDs. For production Postgres, review migrations in staging first.
