from __future__ import annotations

from app.core import database


class _DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_init_db_runs_backfill_and_integrations_without_migrations(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(database.settings, "DATABASE_URL", "postgresql://example/db")
    monkeypatch.setattr(database, "Session", lambda engine: _DummySession())

    from app.core import phase2_migrate

    monkeypatch.setattr(
        phase2_migrate,
        "backfill_uuids",
        lambda session: calls.append("backfill"),
    )
    monkeypatch.setattr(
        phase2_migrate,
        "ensure_default_integrations",
        lambda session: calls.append("integrations"),
    )

    database.init_db()

    assert calls == ["backfill", "integrations"]
