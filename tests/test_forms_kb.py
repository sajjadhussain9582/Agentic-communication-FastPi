from tests.conftest import bearer, register_and_login


def test_forms_unauthorized(client):
    r = client.get("/api/v1/forms")
    assert r.status_code == 401


def test_forms_list_and_get(client):
    _, _, token = register_and_login(client)
    h = bearer(token)

    r = client.get("/api/v1/forms", headers=h)
    assert r.status_code == 200
    forms = r.json()
    assert len(forms) >= 1
    fid = forms[0]["id"]

    r = client.get(f"/api/v1/forms/{fid}", headers=h)
    assert r.status_code == 200
    assert "json_config" in r.json()


def test_forms_create(client):
    _, _, token = register_and_login(client)
    h = bearer(token)
    r = client.post(
        "/api/v1/forms",
        headers=h,
        json={"title": "Intake B", "description": "d", "json_config": {"fields": []}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] is not None
    assert body.get("uuid")
    assert body["title"] == "Intake B"


def test_kb_crud(client):
    _, _, token = register_and_login(client)
    h = bearer(token)

    r = client.get("/api/v1/knowledge-base", headers=h)
    assert r.status_code == 200
    initial = len(r.json())

    r = client.post(
        "/api/v1/knowledge-base",
        headers=h,
        json={
            "question": "Pytest FAQ Q?",
            "answer": "Pytest FAQ A.",
            "category": "test",
        },
    )
    assert r.status_code == 200
    kid = r.json()["id"]
    assert r.json().get("uuid")
    assert r.json()["question"] == "Pytest FAQ Q?"

    r = client.get("/api/v1/knowledge-base", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == initial + 1

    r = client.patch(
        f"/api/v1/knowledge-base/{kid}",
        headers=h,
        json={"question": "Updated Q?", "answer": "Updated A.", "category": "test"},
    )
    assert r.status_code == 200
    assert r.json()["question"] == "Updated Q?"

    r = client.delete(f"/api/v1/knowledge-base/{kid}", headers=h)
    assert r.status_code == 200

    r = client.get("/api/v1/knowledge-base", headers=h)
    assert len(r.json()) == initial
