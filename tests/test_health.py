def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_check(client):
    r = client.get("/api/v1/check")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
