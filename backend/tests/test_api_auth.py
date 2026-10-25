from tests.conftest import auth, make_user, register


def test_register_login_me(client):
    s = register(client)
    assert s["user"]["role"] == "admin"
    assert "manage_users" in s["permissions"]
    assert s["refresh_token"] is None  # web clients get an httpOnly cookie instead
    assert "mpi_refresh" in client.cookies
    me = client.get("/api/v1/auth/me", headers=auth(s["access_token"]))
    assert me.status_code == 200 and me.json()["organisation_name"] == "Acme Research"


def test_login_failures_are_generic_and_rate_limited(client):
    register(client)
    r1 = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong password!"})
    r2 = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong password!"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]
    codes = [
        client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "nope nope nope"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes


def test_refresh_rotation_and_reuse_detection(client):
    register(client)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct horse battery", "client_type": "desktop"},
    )
    first = r.json()["refresh_token"]
    assert first
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert r2.status_code == 200
    second = r2.json()["refresh_token"]
    assert second and second != first
    # Reusing the rotated token revokes the whole family, including the new token.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": second}).status_code == 401


def test_unauthenticated_and_bad_tokens(client):
    assert client.get("/api/v1/products").status_code == 401
    r = client.get("/api/v1/products", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_role_permissions(client, admin):
    viewer = make_user(client, admin["headers"], "viewer@example.com", "viewer")
    researcher = make_user(client, admin["headers"], "r@example.com", "researcher")
    body = {"marketplace_code": "DEMO_UK", "input_type": "keyword", "keyword": "mat", "result_limit": 5}
    assert client.post("/api/v1/jobs", headers=viewer, json=body).status_code == 403
    assert client.post("/api/v1/jobs", headers=researcher, json=body).status_code == 202
    assert client.get("/api/v1/users", headers=researcher).status_code == 403
    assert (
        client.post(
            "/api/v1/connections", headers=researcher, json={"marketplace_code": "DEMO_UK", "account_name": "x"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/audit-logs", headers=admin["headers"]).status_code == 200


def test_organisation_isolation(client, admin):
    from tests.conftest import run_keyword_job

    job = run_keyword_job(client, admin["headers"], limit=5)
    other = register(client, email="rival@example.com", org="Rival Ltd")
    h2 = auth(other["access_token"])
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=h2).status_code == 404
    products = client.get("/api/v1/products", headers=admin["headers"]).json()["items"]
    assert products
    assert client.get(f"/api/v1/products/{products[0]['id']}", headers=h2).status_code == 404
    assert client.get("/api/v1/products", headers=h2).json()["items"] == []


def test_deactivated_user_loses_access(client, admin):
    users = client.get("/api/v1/users", headers=admin["headers"]).json()
    assert len(users) == 1
    h = make_user(client, admin["headers"], "temp@example.com", "researcher")
    uid = next(
        u["id"]
        for u in client.get("/api/v1/users", headers=admin["headers"]).json()
        if u["email"] == "temp@example.com"
    )
    assert client.patch(f"/api/v1/users/{uid}", headers=admin["headers"], json={"is_active": False}).status_code == 200
    assert client.get("/api/v1/products", headers=h).status_code == 401
