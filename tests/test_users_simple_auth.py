from tests.conftest import bearer, register_and_login, unique_email


def test_register_login_me_all(client):
    email, _, token = register_and_login(client)

    r = client.get("/api/v1/users/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["email"] == email

    r = client.get("/api/v1/users/all", headers=bearer(token))
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert email in emails


def test_login_wrong_password(client):
    email = unique_email()
    client.post("/api/v1/users/register", json={"email": email, "password": "right"})
    r = client.post("/api/v1/users/login", json={"email": email, "password": "wrong"})
    assert r.status_code == 401


def test_me_without_token(client):
    r = client.get("/api/v1/users/me")
    assert r.status_code == 401


def test_duplicate_register(client):
    email = unique_email()
    r = client.post("/api/v1/users/register", json={"email": email, "password": "x"})
    assert r.status_code == 200
    r2 = client.post("/api/v1/users/register", json={"email": email, "password": "y"})
    assert r2.status_code == 409


def test_user_put_delete(client):
    _, _, admin_tok = register_and_login(client)
    victim_email = unique_email()
    client.post("/api/v1/users/register", json={"email": victim_email, "password": "victimpass1"})

    r = client.get("/api/v1/users/all", headers=bearer(admin_tok))
    assert r.status_code == 200
    victim = next(u for u in r.json() if u["email"] == victim_email)
    vid = victim["id"]

    r = client.put(
        f"/api/v1/users/{vid}",
        headers=bearer(admin_tok),
        json={"email": victim_email, "password": "newpass123", "status": "active"},
    )
    assert r.status_code == 200

    r = client.post("/api/v1/users/login", json={"email": victim_email, "password": "newpass123"})
    assert r.status_code == 200

    r = client.delete(f"/api/v1/users/{vid}", headers=bearer(admin_tok))
    assert r.status_code == 200

    r = client.post("/api/v1/users/login", json={"email": victim_email, "password": "newpass123"})
    assert r.status_code == 401
