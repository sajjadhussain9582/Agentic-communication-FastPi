"""
Pytest setup: env and DB URL must be set before importing app.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import uuid

import pytest

_td = tempfile.mkdtemp(prefix="agentic_pytest_")
_test_db = pathlib.Path(_td) / "api.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db}"
os.environ["SECRET_KEY"] = "pytest-secret-key-min-32-chars-long!!"
os.environ["SUPABASE_URL"] = "http://localhost"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test"
os.environ["SUPABASE_ANON_KEY"] = "test"
os.environ["REDIS_HOST"] = "localhost"
os.environ["OPENAI_API_KEY"] = "sk-pytest-dummy"
os.environ["FORM_SUBMIT_API_KEY"] = ""

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    # Duplicate email etc. surface as 500 instead of raising into the test
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:12]}@example.com"


def register_and_login(client: TestClient, email: str | None = None, password: str = "TestPass123!"):
    email = email or unique_email()
    r = client.post("/api/v1/users/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/users/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return email, password, token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
