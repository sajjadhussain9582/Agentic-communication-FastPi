# Boss demo: curl flow (realistic frontend-style data)

**Prerequisite:** Server running — `uvicorn app.main:app --reload` (port **8000**).

## Option A — One script (press Enter between steps)

```bash
cd agentic-sytem-backend
./scripts/demo_boss_flow.sh
```

If you use `FORM_SUBMIT_API_KEY` in `.env`:

```bash
export FORM_SUBMIT_API_KEY='paste-your-key'
./scripts/demo_boss_flow.sh
```

---

## Option B — Copy/paste curls one by one

Set base URL once:

```bash
export BASE=http://127.0.0.1:8000
```

### 1) Health

```bash
curl -s "$BASE/api/v1/health" | python3 -m json.tool
```

### 2) Register staff (pick a **new** email if you already ran this)

```bash
export DEMO_EMAIL="demo.boss@yourcompany.com"
export DEMO_PASS="BossDemo2025!"

curl -s -X POST "$BASE/api/v1/users/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\",\"status\":\"active\"}" | python3 -m json.tool
```

### 3) Login → save token

```bash
export TOKEN=$(curl -s -X POST "$BASE/api/v1/users/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASS\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token OK (length ${#TOKEN})"
```

### 4) Who am I?

```bash
curl -s "$BASE/api/v1/users/me" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### 5) Forms list + pick first form id

```bash
curl -s "$BASE/api/v1/forms" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

export FORM_ID=$(curl -s "$BASE/api/v1/forms" -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
echo "FORM_ID=$FORM_ID"
```

### 6) Knowledge base (FAQs for AI)

```bash
curl -s "$BASE/api/v1/knowledge-base" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### 7) **Lead submits form** (no Bearer — like public website)

```bash
# Add -H "X-API-Key: YOUR_KEY" if FORM_SUBMIT_API_KEY is set in server .env

curl -s -X POST "$BASE/api/v1/forms/$FORM_ID/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "website",
    "name": "Sarah Chen",
    "email": "sarah.chen@clientmail.example",
    "phone": "+1-555-0100",
    "company": "Chen Design Studio",
    "message": "We are planning a commercial renovation in Q3. What is your typical project range and how do we book a consultation? What are your business hours?"
  }' | python3 -m json.tool
```

Copy **`conversation_id`** from the JSON, then:

```bash
export CONV_ID=1   # replace with the number from the response
```

### 8) Staff opens full thread

```bash
curl -s "$BASE/api/v1/conversations/$CONV_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

**Talking points for your boss**

1. Staff signs up / logs in like a dashboard user.  
2. Forms + KB are configured once.  
3. A lead fills the form **without** logging in (realistic widget).  
4. System stores contact, conversation, and **AI reply** (RAG when `OPENAI_API_KEY` is set).  
5. Staff reviews the full thread in one API call (maps to an inbox UI later).
