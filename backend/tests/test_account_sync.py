from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select

from app.core.db import session_scope
from app.models import MarketplaceAccount, SellerOrder, SellerOrderItem
from tests.conftest import make_user


def _connect(client, h):
    r = client.post("/api/v1/connections", headers=h, json={"marketplace_code": "DEMO_UK", "account_name": "My shop"})
    assert r.status_code == 201, r.text
    auth_url = r.json()["authorization_url"]
    q = parse_qs(urlsplit(auth_url).query)
    cb = client.get(f"/api/v1/connections/callback/demo?state={q['state'][0]}&code=demo-code", follow_redirects=False)
    assert cb.status_code == 302 and "connected=" in cb.headers["location"]
    # state values are single-use
    again = client.get(
        f"/api/v1/connections/callback/demo?state={q['state'][0]}&code=demo-code", follow_redirects=False
    )
    assert "invalid_state" in again.headers["location"]
    return r.json()["id"]


def test_connect_and_sync_own_account(client, admin):
    h = admin["headers"]
    account_id = _connect(client, h)
    with session_scope() as db:
        acc = db.get(MarketplaceAccount, account_id)
        assert acc.connection_status == "connected"
        assert acc.credential_reference.startswith("sec_")  # SQL holds a reference, not the secret
    r = client.post(f"/api/v1/connections/{account_id}/sync", headers=h)
    assert r.status_code == 202
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["job_status"] == "completed", job
    with session_scope() as db:
        orders = db.scalar(select(func.count()).select_from(SellerOrder))
        linked = db.scalar(
            select(func.count()).select_from(SellerOrderItem).where(SellerOrderItem.listing_id.isnot(None))
        )
    assert orders == 100 and linked > 0
    # second sync re-reads the overlap window idempotently
    client.post(f"/api/v1/connections/{account_id}/sync", headers=h)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(SellerOrder)) == 100
    conns = client.get("/api/v1/connections", headers=h).json()
    assert {c["stream"] for c in conns[0]["checkpoints"]} == {"catalogue", "orders"}
    summary = client.get("/api/v1/analytics/own-account", headers=h).json()["accounts"]
    assert summary and summary[0]["measurement_type"] == "actual"
    own = client.get("/api/v1/products?source_type=own_account", headers=h).json()["items"]
    assert own and all(i["source_type"] == "own_account" for i in own)


def test_own_account_data_hidden_without_permission(client, admin):
    h = admin["headers"]
    account_id = _connect(client, h)
    client.post(f"/api/v1/connections/{account_id}/sync", headers=h, json={"streams": ["catalogue"]})
    researcher = make_user(client, h, "r2@example.com", "researcher")
    items = client.get("/api/v1/products?limit=200", headers=researcher).json()["items"]
    assert items == []
    own_id = client.get("/api/v1/products?source_type=own_account&limit=1", headers=h).json()["items"][0]["id"]
    assert client.get(f"/api/v1/products/{own_id}", headers=researcher).status_code == 403
    assert client.get("/api/v1/connections", headers=researcher).json() == []
    assert client.get("/api/v1/analytics/own-account", headers=researcher).status_code == 403


def test_disconnect_removes_secret(client, admin):
    from app.core.secrets import SecretNotFound, get_secret_store

    h = admin["headers"]
    account_id = _connect(client, h)
    with session_scope() as db:
        ref = db.get(MarketplaceAccount, account_id).credential_reference
    assert client.delete(f"/api/v1/connections/{account_id}", headers=h).status_code == 204
    try:
        get_secret_store().get(ref)
        raise AssertionError("secret should be deleted")
    except SecretNotFound:
        pass
