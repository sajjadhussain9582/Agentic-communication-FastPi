"""Phase 2: inbox, UUID thread, contacts, campaigns, integrations, webhooks."""

from tests.conftest import bearer, register_and_login


def test_conversations_inbox_and_reply(client):
    _, _, token = register_and_login(client)
    h = bearer(token)

    r = client.get("/api/v1/forms", headers=h)
    form_id = r.json()[0]["id"]
    r = client.post(
        f"/api/v1/forms/{form_id}/submit",
        json={"channel": "email", "name": "N", "email": "n@n.com", "message": "Hi"},
    )
    assert r.status_code == 200
    conv_uuid = r.json()["conversation_uuid"]
    assert len(conv_uuid) == 36

    r = client.get("/api/v1/conversations", headers=h)
    assert r.status_code == 200
    inbox = r.json()
    assert any(x["uuid"] == conv_uuid for x in inbox)

    r = client.get(f"/api/v1/conversations/{conv_uuid}", headers=h)
    assert r.status_code == 200
    assert r.json()["uuid"] == conv_uuid
    assert r.json().get("contact_uuid")

    r = client.post(
        f"/api/v1/conversations/{conv_uuid}/messages",
        headers=h,
        json={"message": "Staff reply", "sender_type": "human", "channel": "email"},
    )
    assert r.status_code == 200
    assert r.json()["uuid"]
    assert r.json()["sender_type"] == "human"

    r = client.get(f"/api/v1/conversations/{conv_uuid}", headers=h)
    assert len(r.json()["messages"]) >= 3

    r = client.post(
        f"/api/v1/conversations/{conv_uuid}/schedule-reminder",
        headers=h,
        json={"reminder_at": "2026-03-30T10:00:00Z", "note": "follow up"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_contacts_list_and_patch(client):
    _, _, token = register_and_login(client)
    h = bearer(token)
    r = client.get("/api/v1/forms", headers=h)
    form_id = r.json()[0]["id"]
    client.post(
        f"/api/v1/forms/{form_id}/submit",
        json={"channel": "sms", "name": "C", "email": "c@c.com", "message": "x"},
    )
    r = client.get("/api/v1/contacts", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 1
    cu = r.json()[0]["uuid"]

    r = client.get(f"/api/v1/contacts/{cu}", headers=h)
    assert r.status_code == 200
    assert r.json()["uuid"] == cu
    assert "conversation_uuids" in r.json()

    r = client.patch(
        f"/api/v1/contacts/{cu}",
        headers=h,
        json={"stage": "qualified", "tags": ["vip"], "notes": "Note"},
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "qualified"
    assert "vip" in r.json()["tags"]


def test_campaigns_and_templates(client):
    _, _, token = register_and_login(client)
    h = bearer(token)

    r = client.post(
        "/api/v1/campaigns",
        headers=h,
        json={"name": "Spring", "channel": "email"},
    )
    assert r.status_code == 200
    uid = r.json()["uuid"]

    r = client.get("/api/v1/campaigns", headers=h)
    assert any(c["uuid"] == uid for c in r.json())

    r = client.post(f"/api/v1/campaigns/{uid}/send", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    r = client.post(
        "/api/v1/templates",
        headers=h,
        json={"name": "T1", "body": "Hello {{name}}"},
    )
    assert r.status_code == 200
    r = client.get("/api/v1/templates", headers=h)
    assert len(r.json()) >= 1


def test_integrations_and_webhook(client):
    r = client.get("/api/v1/integrations")
    assert r.status_code == 401

    _, _, token = register_and_login(client)
    h = bearer(token)
    r = client.get("/api/v1/integrations", headers=h)
    assert r.status_code == 200
    providers = {x["provider"] for x in r.json()}
    assert "ghl" in providers

    r = client.post("/api/v1/webhooks/ghl/message", json={"text": "x"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.post("/api/v1/webhooks/ghl/contact", json={"id": "1"})
    assert r.status_code == 200

    r = client.post("/api/v1/integrations/ghl/sync", headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/v1/integrations/ghl/runs", headers=h)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get("/api/v1/ops/quality-summary", headers=h)
    assert r.status_code == 200
    assert "generated_messages_24h" in r.json()
