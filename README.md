# Agentic Communication System — Backend

## Why `pip install` / `uvicorn` fail on Linux

Your system Python is **PEP 668 protected** (Ubuntu/Debian). Install dependencies **only inside a virtual environment**, not with global `pip`.

## One-time setup

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate    # fish: source .venv/bin/activate.fish
pip install -r requirements.txt
```

## Run the API

With the venv **activated**:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or **without** activating (uses venv’s Python directly):

```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Copy `.env` from your secrets; required vars include `DATABASE_URL`, `SECRET_KEY`, Supabase keys, and `OPENAI_API_KEY` for the AI form flow.

## Tests

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

See [docs/TESTING.md](docs/TESTING.md) for details and manual curl flows.

**Boss / stakeholder demo (curl, realistic data):** run `./scripts/demo_boss_flow.sh` or follow [docs/DEMO_BOSS_FLOW.md](docs/DEMO_BOSS_FLOW.md).

**Frontend developer handoff:** [docs/FRONTEND_INTEGRATION_SPEC.md](docs/FRONTEND_INTEGRATION_SPEC.md) — screens, APIs, gaps, env vars.

## Docs

- [docs/API_AGENT.md](docs/API_AGENT.md) — form submit & KB endpoints  
- [docs/PHASE2.md](docs/PHASE2.md) — future CRM / webhooks / campaigns  
- [docs/TESTING.md](docs/TESTING.md) — pytest + curl checklist  
