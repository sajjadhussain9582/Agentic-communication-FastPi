# API testing (simple email/password auth)

Supabase / Google login is **not** covered here. Use **register + login** to obtain a Bearer token.

## Automated tests

```bash
cd agentic-sytem-backend
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
# Skip optional OpenAI live test (default):
python -m pytest tests/ -v -m "not integration"
# With real OpenAI (costs API usage):
OPENAI_API_KEY=sk-... python -m pytest tests/test_integration_openai.py -v -m integration
```

Tests use a **temporary SQLite** database (env is set in `tests/conftest.py` before the app loads). Your `.env` `DATABASE_URL` is overridden for pytest.

**Note:** `bcrypt` must be **&lt; 4.1** for compatibility with `passlib` (see `requirements.txt`).

## Manual checklist (curl)

Base: `http://127.0.0.1:8000`

### 1. Health

```bash
curl -s http://127.0.0.1:8000/api/v1/health
curl -s http://127.0.0.1:8000/api/v1/check
```

### 2. Register and login → `TOKEN`

```bash
EMAIL="you+test@example.com"
curl -s -X POST http://127.0.0.1:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"YourPass123!\"}"

export TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/users/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"YourPass123!\"}" | jq -r .access_token)
```

### 3. Me / users (Bearer)

```bash
curl -s http://127.0.0.1:8000/api/v1/users/me -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/v1/users/all -H "Authorization: Bearer $TOKEN"
```

### 4. Forms & knowledge base

```bash
curl -s http://127.0.0.1:8000/api/v1/forms -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/v1/forms/1 -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/v1/knowledge-base -H "Authorization: Bearer $TOKEN"
```

### 5. Form submit (public; add `X-API-Key` if `FORM_SUBMIT_API_KEY` is set)

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/forms/1/submit \
  -H "Content-Type: application/json" \
  -d '{"channel":"website","name":"Jane","email":"jane@example.com","message":"What are your hours?"}'
```

Save `conversation_id` from the JSON.

### 6. Conversation thread

```bash
CONV_ID=1   # replace with value from submit response
curl -s "http://127.0.0.1:8000/api/v1/conversations/$CONV_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Reuse **`TOKEN`** for all protected calls after login.
