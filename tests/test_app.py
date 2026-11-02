from datetime import date, timedelta

import pytest

from app import CFG, create_app

TODAY = date(2026, 6, 15)


@pytest.fixture
def client(tmp_path):
    clock = {"d": TODAY}
    app = create_app(str(tmp_path / "t.db"), today=lambda: clock["d"])
    app.config["SIMULATE"] = True
    c = app.test_client()
    c.clock = clock
    return c


def enrol(c, name="Ama Mensah", phone="0241112233", **kw):
    r = c.post("/api/members", json={"name": name, "phone": phone, "consent": True, **kw})
    assert r.status_code == 201, r.json
    return r.json["id"]


def pay(c, mid):
    p = c.post("/api/payments", json={"member_id": mid, "network": "MTN MoMo"}).json
    return c.post(f"/api/payments/{p['id']}/approve").json


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
    assert client.post(f"/api/payments/{p['id']}/decline").status_code == 200
    assert client.post(f"/api/payments/{p['id']}/approve").status_code == 409
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


def test_rejected_claims_free_the_limit_and_transitions(client):
    mid = enrol(client)
    pay(client, mid)
    c = claim(client, mid, type="cash", days=8)
    assert client.post(f"/api/claims/{c['id']}/status", json={"status": "Paid"}).status_code == 409
    assert client.post(f"/api/claims/{c['id']}/status", json={"status": "Rejected"}).json["status"] == "Rejected"
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
    assert len(client.post("/api/demo/seed").json["added"]) == 3
    assert client.get("/api/summary").json["covered"] == 2
    assert client.get("/api/export/claims.csv").data.startswith(b"id,member_id")
    client.post("/api/demo/reset")
    assert client.get("/api/members").json == []
