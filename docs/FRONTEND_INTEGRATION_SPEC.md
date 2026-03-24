# Frontend integration specification

**Audience:** Frontend developer building screens against this backend.  
**Scope:** Auth (signup/login), public lead form, staff dashboard areas (conversations, FAQs, forms), and what the API supports today vs. follow-ups.

**API base URL (example):** `https://<your-api-host>/api/v1`  
(Confirm with backend; local dev often `http://127.0.0.1:8000/api/v1`.)

**CORS:** Backend must allow your frontend origin. Coordinate with backend if browser calls fail with CORS errors.

---

## 1. Authentication model

| Mechanism | Details |
|-----------|---------|
| **Staff / admin users** | Email + password. Register once, then login. |
| **Token** | Login returns `access_token` (opaque signed token, not JWT). Send on every protected request: header `Authorization: Bearer <access_token>`. |
| **Token storage** | Frontend choice: `memory`, `sessionStorage`, or `httpOnly` cookie (backend also sets `session_token` cookie on login — align with product on whether SPA uses cookie or Bearer only). |
| **Supabase / Google** | **Out of scope for this integration** unless product later adds it. |

### Screens to build

| Screen | Purpose | API |
|--------|---------|-----|
| **Sign up (register)** | Create staff account | `POST /users/register` |
| **Login** | Obtain session | `POST /users/login` |
| **Profile / session check** | Show logged-in user, gate routes | `GET /users/me` (requires Bearer) |

### Register request (conceptual fields)

- `email` (required, valid email)
- `password` (required)
- `status` (optional, e.g. `"active"`)

**Responses to handle:** `200` success; `409` email already registered; `400` missing password.

### Login request

- `email`, `password`

**Responses:** `200` + `access_token`, `token_type: "bearer"`; `401` invalid credentials.

### After login

- Persist token; redirect to dashboard/home.
- On `401` from any call: clear token and redirect to login.

### Note on `/users/me` response

Backend may return a `password` field (hash). **Do not display it.** Map only safe fields to UI (e.g. `id`, `email`, `status`).

### Optional admin screens (same auth)

- User list: `GET /users/all`
- Edit user: `PUT /users/{id}`
- Delete user: `DELETE /users/{id}`  
Use only if product needs internal user management UI.

---

## 2. Public lead form (website / widget)

**Goal:** Visitor fills contact form **without** logging in. Submit creates a contact, conversation, and **AI-generated reply** returned in the same response.

| Screen | Purpose |
|--------|---------|
| **Contact / lead form** | Channel selector + name, email, phone, company, message (or fields driven by form config — see gap below). |
| **Thank-you / reply** | Show `ai_reply` text from submit response (instant “assistant” message). Optionally show “We’ll also email you” copy. |

INFO:     127.0.0.1:37894 - "OPTIONS /api/v1/users/login HTTP/1.1" 200 OK
INFO:     127.0.0.1:37904 - "POST /api/v1/users/login HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        cursor, str_statement, effective_parameters, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
psycopg2.OperationalError: server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.
server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.


The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/uvicorn/protocols/http/h11_impl.py", line 410, in run_asgi
    result = await app(  # type: ignore[func-returns-value]
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        self.scope, self.receive, self.send
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/uvicorn/middleware/proxy_headers.py", line 60, in __call__
    return await self.app(scope, receive, send)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/applications.py", line 1160, in __call__
    await super().__call__(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/applications.py", line 107, in __call__
    await self.middleware_stack(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/middleware/errors.py", line 186, in __call__
    raise exc
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/middleware/errors.py", line 164, in __call__
    await self.app(scope, receive, _send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/middleware/cors.py", line 95, in __call__
    await self.simple_response(scope, receive, send, request_headers=headers)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/middleware/cors.py", line 153, in simple_response
    await self.app(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/middleware/exceptions.py", line 63, in __call__
    await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/middleware/asyncexitstack.py", line 18, in __call__
    await self.app(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/routing.py", line 716, in __call__
    await self.middleware_stack(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/routing.py", line 736, in app
    await route.handle(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/routing.py", line 290, in handle
    await self.app(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 130, in app
    await wrap_app_handling_exceptions(app, request)(scope, receive, send)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 116, in app
    response = await f(request)
               ^^^^^^^^^^^^^^^^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 670, in app
    raw_response = await run_endpoint_function(
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<3 lines>...
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 326, in run_endpoint_function
    return await run_in_threadpool(dependant.call, **values)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/starlette/concurrency.py", line 32, in run_in_threadpool
    return await anyio.to_thread.run_sync(func)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/anyio/to_thread.py", line 63, in run_sync
    return await get_async_backend().run_sync_in_worker_thread(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        func, args, abandon_on_cancel=abandon_on_cancel, limiter=limiter
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/anyio/_backends/_asyncio.py", line 2502, in run_sync_in_worker_thread
    return await future
           ^^^^^^^^^^^^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/anyio/_backends/_asyncio.py", line 986, in run
    result = context.run(func, *args)
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/api/routes/users.py", line 37, in login
    user = session.exec(
           ~~~~~~~~~~~~^
        select(User).where(User.email == user_in.email)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ).first()
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlmodel/orm/session.py", line 75, in exec
    results = super().execute(
        statement,
    ...<4 lines>...
        _add_event=_add_event,
    )
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/orm/session.py", line 2351, in execute
    return self._execute_internal(
           ~~~~~~~~~~~~~~~~~~~~~~^
        statement,
        ^^^^^^^^^^
    ...<4 lines>...
        _add_event=_add_event,
        ^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/orm/session.py", line 2249, in _execute_internal
    result: Result[Any] = compile_state_cls.orm_execute_statement(
                          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        self,
        ^^^^^
    ...<4 lines>...
        conn,
        ^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/orm/context.py", line 306, in orm_execute_statement
    result = conn.execute(
        statement, params or {}, execution_options=execution_options
    )
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
    return meth(
        self,
        distilled_parameters,
        execution_options or NO_OPTIONS,
    )
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
    return connection._execute_clauseelement(
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        self, distilled_params, execution_options
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1641, in _execute_clauseelement
    ret = self._execute_context(
        dialect,
    ...<8 lines>...
        cache_hit=cache_hit,
    )
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
    return self._exec_single_context(
           ~~~~~~~~~~~~~~~~~~~~~~~~~^
        dialect, context, statement, parameters
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1986, in _exec_single_context
    self._handle_dbapi_exception(
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        e, str_statement, effective_parameters, cursor, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        cursor, str_statement, effective_parameters, context
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/.venv/lib/python3.13/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.
server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.

[SQL: SELECT "user".id, "user".email, "user".password, "user"."isActive", "user".status, "user"."createdAt", "user"."updatedAt", "user".supabase_id 
FROM "user" 
WHERE "user".email = %(email_1)s]
[parameters: {'email_1': 'dagocen820@duoley.com'}]
(Background on this error at: https://sqlalche.me/e/20/e3q8)

### Submit API

- **Method/path:** `POST /forms/{form_id}/submit`
- **Auth:** Usually **none**. If backend sets `FORM_SUBMIT_API_KEY`, send header `X-API-Key: <same value as backend env>` (often from frontend env var `NEXT_PUBLIC_FORM_API_KEY` or server-side proxy only — **prefer server-side proxy** to hide the key).

### Body (align with default intake form)

Typical fields (must include **`channel`**):

- `channel` — e.g. `"website"`, `"email"`, `"message"`, `"sms"` (match options product wants)
- `name`, `email`, `phone`, `company`, `message` (or whatever marketing agrees)

### Success response (show in UI)

Conceptually includes:

- `submission_id`, `contact_id`, `conversation_id`
- **`ai_reply`** — show as the main post-submit content
- `rag_kb_ids` — optional: “Sources: FAQ #1, #2” if you want transparency
- `conversation_status`, `is_escalated` — if escalated, show “A team member will follow up”

### Gap: loading form definition without login

Today **`GET /forms` and `GET /forms/{id}` require Bearer**. Options:

1. **Hardcode** the same field layout as the default intake form (fastest; drift risk if backend form changes).
2. **Backend follow-up:** add a **public** read-only endpoint e.g. `GET /forms/{id}/public` (schema only, no secrets) so the form screen can be driven by `json_config`.
3. **Staff-only builder** fetches `json_config` and you **publish** a static copy to the marketing site.

Document this decision with backend.

---

## 3. Chat / conversation (staff inbox style)

**Goal:** Staff sees the thread: visitor message + AI reply, status, intent, escalation.

| Screen | Purpose | API today |
|--------|---------|-----------|
| **Conversation detail / thread** | Message list, metadata | `GET /conversations/{conversation_id}` (Bearer) |

### Response shape (conceptual)

- Conversation: `id`, `contact_id`, `channel`, `status`, `is_escalated`, `last_intent`, `qualification_stage`
- **messages[]:** each: `sender_type` (`client` | `agent` | `human`), `message`, `channel`, `is_generated`, `rag_source_kb_ids`, `created_at`
- Render **client** messages on one side, **agent** (AI) on the other; show badge if `is_generated`.

### Gap: conversation list (inbox)

There is **no** `GET /conversations` list endpoint yet. After public submit, the frontend only has `conversation_id` if:

- User came from submit flow (you can deep-link to `/inbox/{conversation_id}`), or
- You build a **contact-centric** list later.

**Backend follow-up (recommended):** `GET /conversations?limit=&cursor=` or `GET /contacts/{id}/conversations` for a real inbox. Until then, product options:

- Deep link from email notifications (future).
- Manual ID entry (not ideal).
- Backend adds list endpoint as next priority.

### “Chat” as ongoing thread

Today the API is **not** a live chat socket: one submit → one AI reply. **Follow-up messages** from the same visitor would require either:

- Another submit (may create new conversation depending on backend rules), or
- Future endpoint: `POST /conversations/{id}/messages` for true threaded chat.

Clarify roadmap with backend.

---

## 4. Staff: FAQ / knowledge base (RAG)

| Screen | Purpose | API |
|--------|---------|-----|
| **FAQ list** | Table of Q&A | `GET /knowledge-base` |
| **Create FAQ** | Add question/answer/category | `POST /knowledge-base` |
| **Edit FAQ** | Update + re-embed | `PATCH /knowledge-base/{id}` |
| **Delete FAQ** | Remove | `DELETE /knowledge-base/{id}` |

All require **Bearer**.  
`has_embedding` in list indicates whether AI retrieval will use that row (embeddings refresh on create/update when OpenAI is configured on server).

---

## 5. Staff: form management (optional)

| Screen | Purpose | API |
|--------|---------|-----|
| **Form list** | See intake forms | `GET /forms` |
| **Form detail** | Inspect `json_config` | `GET /forms/{id}` |
| **Create form** | New intake template | `POST /forms` |

Used to define fields for future dynamic public forms once a public schema endpoint exists.

---

## 6. Health / diagnostics

- `GET /health`, `GET /check` — no auth. Useful for “system status” admin tile or deployment checks.

---

## 7. Suggested screen map (IA)

| Area | Screens |
|------|---------|
| **Auth** | Login, Register, (optional) Forgot password — *not in API today* |
| **Public** | Lead form → Thank you + AI reply |
| **Staff dashboard** | Home, Conversation detail (by id), FAQ CRUD, (optional) Forms list, (optional) Users |
| **Settings** | Profile (`/me`), logout |

---

## 8. Environment variables (frontend)

| Variable | Use |
|----------|-----|
| `NEXT_PUBLIC_API_URL` / `VITE_API_URL` | Base URL for API (`.../api/v1` or full host) |
| `NEXT_PUBLIC_FORM_ID` | Default form id for public page (often `1`) |
| `NEXT_PUBLIC_FORM_SUBMIT_KEY` | Only if using `X-API-Key` from browser — **discouraged**; prefer BFF/proxy |

---

## 9. Error handling checklist

| Code | Typical cause |
|------|----------------|
| `401` | Missing/invalid token → login |
| `403` | Rare; treat like 401 unless backend adds roles |
| `404` | Wrong `conversation_id` / form id |
| `409` | Duplicate email on register |
| `422` | Validation (email format, etc.) |
| `5xx` | Show generic error; retry or support |

---

## 10. Summary for prioritization

**P0 — MVP frontend**

1. Login + Register + store Bearer + `/me`  
2. Public form + submit + display `ai_reply`  
3. Staff conversation detail page **if** you have `conversation_id` (from submit or link)

**P1**

4. FAQ admin (list/create/edit/delete)  
5. CORS + env wiring + optional submit API key via proxy  

**Backend dependencies for richer UX**

- Public form schema endpoint **or** hardcoded form fields  
- `GET /conversations` (inbox list)  
- Optional: websocket or `POST` new message for ongoing chat  

---

*Document version: aligned with Phase 1 backend. Update when new endpoints ship.*
