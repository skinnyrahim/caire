import os
import threading
from datetime import date, timedelta

import pytest

from app import CFG, connect, create_app

TODAY = date(2026, 6, 15)
# Set to an empty, throwaway Postgres database to also run every test on Postgres.
PG_URL = os.environ.get("CAIRE_TEST_PG_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        return str(tmp_path / "t.db")
    if not PG_URL:
        pytest.skip("CAIRE_TEST_PG_URL not set")
    c = connect(PG_URL)
    c.execute("DROP SCHEMA public CASCADE")
    c.execute("CREATE SCHEMA public")
    c.commit()
    c.close()
    return PG_URL


@pytest.fixture
def app(db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-1")
    clock = {"d": TODAY}
    a = create_app(db_url, today=lambda: clock["d"])
    a.config["SIMULATE"] = True
    a.clock = clock
    return a


def login(app, username="admin", password="admin-pass-1"):
    c = app.test_client()
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.json
    c.clock = app.clock
    return c


def make_staff(admin, username, role, password="temp-pass-1"):
    r = admin.post("/api/staff", json={"username": username, "name": username.title(), "role": role, "password": password})
    assert r.status_code == 201, r.json


@pytest.fixture
def client(app):
    return login(app)


@pytest.fixture
def reviewer(app, client):
    make_staff(client, "rita", "reviewer")
    c = login(app, "rita", "temp-pass-1")
    assert c.post("/api/me/password", json={"current": "temp-pass-1", "new": "rita-pass-22"}).status_code == 200
    return c


def enrol(c, name="Ama Mensah", phone="0241112233", **kw):
    r = c.post("/api/members", json={"name": name, "phone": phone, "consent": True, **kw})
    assert r.status_code == 201, r.json
    return r.json["id"]


def pay(c, mid):
    p = c.post("/api/payments", json={"member_id": mid, "network": "MTN MoMo"}).json
    return c.post(f"/api/payments/{p['id']}/approve", json={}).json


def claim(c, mid, **kw):
    body = {"member_id": mid, "type": "gap", "diagnosis": "Malaria", "services": "RDT, ACT", "amount": 80,
            "visit_date": c.clock["d"].isoformat(), **kw}
    r = c.post("/api/claims/start", json=body)
    assert r.status_code == 201, r.json
    r = c.post("/api/claims/confirm", json={"pending_id": r.json["pending_id"], "code": r.json["simulated_code"]})
    assert r.status_code == 201, r.json
    return r.json


def test_enrol_requires_consent_and_unique_phone(client):
    r = client.post("/api/members", json={"name": "A", "phone": "0241112233"})
    assert r.status_code == 400 and "consent" in r.json["error"]
    assert enrol(client) == "CC-0001"
    r = client.post("/api/members", json={"name": "B", "phone": "+233 24 111 2233", "consent": True})
    assert r.status_code == 409


def test_premium_starts_and_stacks_cover(client):
    mid = enrol(client)
    assert client.get("/api/members").json[0]["covered"] is False
    assert pay(client, mid)["paid_through"] == (TODAY + timedelta(days=CFG["cover_days"] - 1)).isoformat()
    assert pay(client, mid)["paid_through"] == (TODAY + timedelta(days=2 * CFG["cover_days"] - 1)).isoformat()
    s = client.get("/api/summary").json
    assert s["covered"] == 1 and s["premiums"] == 2 * CFG["premium"]


def test_declined_payment_gives_no_cover(client):
    mid = enrol(client)
    p = client.post("/api/payments", json={"member_id": mid, "network": "MTN MoMo"}).json
    assert client.post(f"/api/payments/{p['id']}/decline", json={}).status_code == 200
    assert client.post(f"/api/payments/{p['id']}/approve", json={}).status_code == 409
    assert client.get("/api/members").json[0]["covered"] is False


def test_claim_needs_cover_and_correct_code(client):
    mid = enrol(client)
    r = client.post("/api/claims/start", json={"member_id": mid, "type": "gap", "diagnosis": "x", "services": "y", "amount": 10})
    assert r.status_code == 400 and "Not covered" in r.json["error"]
    pay(client, mid)
    r = client.post("/api/claims/start", json={"member_id": mid, "type": "gap", "diagnosis": "x", "services": "y", "amount": 10}).json
    for _ in range(CFG["otp_attempts"]):
        assert client.post("/api/claims/confirm", json={"pending_id": r["pending_id"], "code": "0000" if r["simulated_code"] != "0000" else "1111"}).status_code == 400
    # locked out after max attempts, even with the right code
    assert client.post("/api/claims/confirm", json={"pending_id": r["pending_id"], "code": r["simulated_code"]}).status_code == 410


def test_clean_claim_approved_and_limits_tracked(client):
    mid = enrol(client)
    pay(client, mid)
    c = claim(client, mid)
    assert c["status"] == "Approved" and c["payable"] == 80 and c["flags"] == []
    rem = client.get(f"/api/eligibility?q={mid}").json["remaining"]
    assert rem == {"gap": CFG["gap_limit"] - 80, "days": CFG["cash_max_days"]}
    # lookup by phone in international format
    assert client.get("/api/eligibility?q=%2B233241112233").json["found"]


def test_flags(client):
    mid = enrol(client)
    pay(client, mid)
    claim(client, mid, amount=50)
    c = claim(client, mid, amount=1000)
    assert c["status"] == "Needs review"
    assert c["payable"] == CFG["gap_limit"] - 50
    assert any("Repeat" in f for f in c["flags"])
    assert "Amount above remaining limit" in c["flags"]
    cash = claim(client, mid, type="cash", days=8)
    assert cash["payable"] == 8 * CFG["cash_per_day"] and "Long stay (7+ days)" in cash["flags"]


def test_rejected_claims_free_the_limit_and_transitions(client, reviewer):
    mid = enrol(client)
    pay(client, mid)
    c = claim(client, mid, type="cash", days=8)
    assert reviewer.post(f"/api/claims/{c['id']}/status", json={"status": "Paid"}).status_code == 409
    r = reviewer.post(f"/api/claims/{c['id']}/status", json={"status": "Rejected"}).json
    assert r["status"] == "Rejected" and r["reviewed_by"] == "rita"
    assert client.get(f"/api/eligibility?q={mid}").json["remaining"]["days"] == CFG["cash_max_days"]


def test_future_visit_and_lapsed_cover(client):
    mid = enrol(client)
    pay(client, mid)
    r = client.post("/api/claims/start", json={"member_id": mid, "type": "gap", "diagnosis": "x", "services": "y",
                                                "amount": 10, "visit_date": (TODAY + timedelta(days=1)).isoformat()})
    assert r.status_code == 400
    client.clock["d"] = TODAY + timedelta(days=CFG["cover_days"] + 1)
    assert client.get("/api/members").json[0]["covered"] is False
    # a visit while cover was active can still be claimed later
    c = claim(client, mid, visit_date=TODAY.isoformat())
    assert c["status"] == "Approved"


def test_spot_check_dispute(client):
    mid = enrol(client)
    pay(client, mid)
    c = claim(client, mid)
    assert client.post("/api/spotchecks/draw", json={"count": 2}).json["picked"] == [c["id"]]
    r = client.post(f"/api/claims/{c['id']}/spot", json={"result": "disputed"}).json
    assert r["status"] == "Needs review" and "Member disputed on verification call" in r["flags"]
    assert client.post("/api/spotchecks/draw", json={}).json["picked"] == []


def test_seed_reset_and_export(client):
    assert len(client.post("/api/demo/seed", json={}).json["added"]) == 3
    assert client.get("/api/summary").json["covered"] == 2
    assert client.get("/api/export/claims.csv").data.startswith(b"id,member_id")
    client.post("/api/demo/reset", json={})
    assert client.get("/api/members").json == []


def test_api_requires_login(app):
    c = app.test_client()
    assert c.get("/api/members").status_code == 401
    assert c.post("/api/members", json={}).status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401


def test_login_lockout(app):
    c = app.test_client()
    for _ in range(5):
        c.post("/api/login", json={"username": "admin", "password": "wrong"})
    assert c.post("/api/login", json={"username": "admin", "password": "admin-pass-1"}).status_code == 429


def test_writes_must_be_json(client):
    assert client.post("/api/members", data={"name": "x"}).status_code == 415


def test_new_staff_must_change_password(app, client):
    make_staff(client, "dora", "desk")
    d = login(app, "dora", "temp-pass-1")
    assert d.get("/api/me").json["must_change"] is True
    assert d.get("/api/members").status_code == 403
    assert d.post("/api/me/password", json={"current": "temp-pass-1", "new": "short"}).status_code == 400
    assert d.post("/api/me/password", json={"current": "temp-pass-1", "new": "dora-pass-22"}).status_code == 200
    assert d.get("/api/members").status_code == 200


def test_roles_and_separation_of_duties(app, client, reviewer):
    make_staff(client, "dora", "desk", "dora-pass-1")
    d = login(app, "dora", "dora-pass-1")
    d.post("/api/me/password", json={"current": "dora-pass-1", "new": "dora-pass-22"})
    mid = enrol(d)
    pay(d, mid)
    c = claim(d, mid, type="cash", days=8)
    assert c["created_by"] == "dora"
    assert d.post(f"/api/claims/{c['id']}/status", json={"status": "Approved"}).status_code == 403
    assert d.get("/api/staff").status_code == 403
    assert d.post("/api/demo/reset", json={}).status_code == 403
    assert reviewer.get("/api/staff").status_code == 403
    assert reviewer.post(f"/api/claims/{c['id']}/status", json={"status": "Approved"}).status_code == 200
    own = claim(reviewer, mid, type="cash", days=7)
    r = reviewer.post(f"/api/claims/{own['id']}/status", json={"status": "Approved"})
    assert r.status_code == 403 and "another reviewer" in r.json["error"]
    actions = [a["action"] for a in client.get("/api/audit").json]
    assert "claim_approved" in actions and "member_enrolled" in actions


def test_disabling_staff_ends_their_session(app, client):
    make_staff(client, "dora", "desk", "dora-pass-1")
    d = login(app, "dora", "dora-pass-1")
    uid = [u for u in client.get("/api/staff").json if u["username"] == "dora"][0]["id"]
    client.post(f"/api/staff/{uid}", json={"active": False})
    assert d.get("/api/me").status_code == 401
    me = client.get("/api/me").json["id"]
    assert client.post(f"/api/staff/{me}", json={"active": False}).status_code == 400


def test_password_reset_signs_out_old_sessions(app, client):
    make_staff(client, "dora", "desk", "dora-pass-1")
    d = login(app, "dora", "dora-pass-1")
    uid = [u for u in client.get("/api/staff").json if u["username"] == "dora"][0]["id"]
    client.post(f"/api/staff/{uid}", json={"password": "new-temp-pass"})
    assert d.get("/api/me").status_code == 401
    assert login(app, "dora", "new-temp-pass").get("/api/me").json["must_change"] is True


def test_ids_unique_under_concurrent_enrolment(app, client):
    """Two desks enrolling at the same moment must not get the same member ID."""
    errors = []

    def go(i):
        r = client.application.test_client()
        with r.session_transaction() as sess, client.session_transaction() as mine:
            sess.update(mine)
        res = r.post("/api/members", json={"name": f"M{i}", "phone": f"02400000{i:02d}", "consent": True})
        if res.status_code != 201:
            errors.append(res.json)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [m["id"] for m in client.get("/api/members").json]
    assert not errors and len(ids) == 12 and len(set(ids)) == 12
    assert sorted(ids)[-1] == "CC-0012"


def test_reset_restarts_numbering(client):
    enrol(client)
    client.post("/api/demo/reset", json={})
    assert enrol(client, phone="0209998877") == "CC-0001"
