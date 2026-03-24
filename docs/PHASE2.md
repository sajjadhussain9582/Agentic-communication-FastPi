# Phase 2 (not implemented yet)

Phase 1 delivers: ERD-aligned tables, form submit → RAG + LangGraph AI → stored replies, KB CRUD, conversation read API.

**Next steps for full client requirements:**

| Area | Work |
|------|------|
| **Outbound delivery** | Send AI replies via email/SMS using provider webhooks/APIs; map `channel` to adapter. |
| **Webhooks** | Inbound email/SMS webhooks → same pipeline as form submit (create message, run AI). |
| **Meetings / Calendly** | `MEETINGS` table, booking links in replies, `REMINDERS` + scheduler. |
| **GoHighLevel / CRM** | `INTEGRATIONS` + sync jobs for contacts, deals, pipeline stages. |
| **Campaigns** | `CAMPAIGNS`, `CAMPAIGNTARGETS`, `CAMPAIGNMESSAGES` + send workers and response tracking. |
| **Automations** | `AUTOMATIONS` engine: triggers (`form_submission`, `new_message`, …) and actions. |
| **Tasks & escalation** | Create `TASK` rows when `is_escalated`; notify assigned `USERS`. |
| **Observability** | `ACTIVITYLOGS`, metrics on RAG hit rate and escalation rate. |

Configure `.env`: `OPENAI_API_KEY`, optional `FORM_SUBMIT_API_KEY` for public form protection.
