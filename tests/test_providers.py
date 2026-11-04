"""Paystack and Arkesel flows, with the providers replaced by fakes."""
import hashlib
import hmac
import json

import pytest

import providers
from providers import ProviderError
from test_app import claim, enrol, login  # noqa: F401  (fixtures below reuse test_app's)
from test_app import app, db_url  # noqa: F401

KEY = "sk_test_abc"


class FakePaystack:
    def __init__(self):
        self.calls, self.charge_status, self.verify_status, self.amount = [], "pay_offline", "ongoing", 500

    def __call__(self, key, method, path, body=None):
        assert key == KEY
        self.calls.append((method, path, body))
        if path == "/charge":
            if self.charge_status == "error":
                raise ProviderError("Invalid phone number")
            return {"reference": body["reference"], "status": self.charge_status,
                    "display_text": "Please complete authorization on your phone", "amount": self.amount}
        if path == "/charge/submit_otp":
            return {"reference": body["reference"], "status": "pay_offline", "display_text": "Approve on your phone"}
        if path.startswith("/transaction/verify/"):
            return {"reference": path.rsplit("/", 1)[1], "status": self.verify_status, "amount": self.amount,
                    "currency": "GHS", "gateway_response": "Declined by customer"}
        raise AssertionError(path)


@pytest.fixture
def fakes(app, monkeypatch):  # noqa: F811
    ps, sms = FakePaystack(), []
    monkeypatch.setattr(providers, "paystack_request", ps)
    monkeypatch.setattr(providers, "arkesel_send", lambda k, sender, phone, msg: sms.append((phone, msg)))
    app.config.update(PAYSTACK_KEY=KEY, ARKESEL_KEY="ark", ARKESEL_SENDER="CAIRE")
    return ps, sms


@pytest.fixture
def desk(app, fakes):  # noqa: F811
    return login(app)


def request_pay(c, mid, network="MTN MoMo"):
    r = c.post("/api/payments", json={"member_id": mid, "network": network})
    assert r.status_code == 201, r.json
    return r.json


def webhook(c, payload, key=KEY):
    raw = json.dumps(payload).encode()
    sig = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return c.post("/api/paystack/webhook", data=raw, headers={"X-Paystack-Signature": sig, "Content-Type": "application/json"})


def test_charge_sent_to_paystack(desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid, "Telecel Cash")
    method, path, body = ps.calls[0]
    assert (method, path) == ("POST", "/charge")
    assert body["amount"] == 500 and body["currency"] == "GHS"
    assert body["mobile_money"] == {"phone": "0241112233", "provider": "vod"}
    assert body["email"] == "cc-0001@example.com"
    assert p["status"] == "pending" and p["mode"] == "paystack" and p["provider_status"] == "pay_offline"
    # the desk cannot approve a real payment itself
    assert desk.post(f"/api/payments/{p['id']}/approve", json={}).status_code == 403


def test_webhook_starts_cover_and_texts_member(app, desk, fakes):
    ps, sms = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    ref = ps.calls[0][2]["reference"]
    anon = app.test_client()
    assert webhook(anon, {"event": "charge.success", "data": {"reference": ref, "amount": 500, "currency": "GHS"}}).status_code == 200
    got = desk.get(f"/api/payments/{p['id']}").json
    assert got["status"] == "approved" and got["paid_through"]
    assert desk.get("/api/members").json[0]["covered"] is True
    assert sms and sms[-1][0] == "0241112233" and "covered until" in sms[-1][1]
    # repeated notification changes nothing
    webhook(anon, {"event": "charge.success", "data": {"reference": ref, "amount": 500, "currency": "GHS"}})
    assert len(desk.get("/api/payments").json) == 1 and len(sms) == 1


def test_webhook_rejects_bad_signature(app, desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    ref = ps.calls[0][2]["reference"]
    r = webhook(app.test_client(), {"event": "charge.success", "data": {"reference": ref, "amount": 500}}, key="sk_wrong")
    assert r.status_code == 401
    assert desk.get(f"/api/payments/{p['id']}").json["status"] == "pending"


def test_wrong_amount_does_not_give_cover(app, desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    webhook(app.test_client(), {"event": "charge.success", "data": {"reference": ps.calls[0][2]["reference"], "amount": 100, "currency": "GHS"}})
    got = desk.get(f"/api/payments/{p['id']}").json
    assert got["status"] == "review" and got["provider_status"] == "amount_mismatch"
    assert "Check in Paystack" in got["message"]
    assert desk.get("/api/members").json[0]["covered"] is False


def test_status_check_settles_without_webhook(desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    assert desk.get(f"/api/payments/{p['id']}").json["status"] == "pending"
    ps.verify_status = "success"
    assert desk.get(f"/api/payments/{p['id']}").json["status"] == "approved"


def test_failed_payment(desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    ps.verify_status = "failed"
    got = desk.get(f"/api/payments/{p['id']}").json
    assert got["status"] == "declined" and got["message"] == "Declined by customer"
    assert desk.get("/api/members").json[0]["covered"] is False


def test_paystack_error_is_shown(desk, fakes):
    ps, _ = fakes
    ps.charge_status = "error"
    mid = enrol(desk)
    r = desk.post("/api/payments", json={"member_id": mid, "network": "MTN MoMo"})
    assert r.status_code == 502 and "Invalid phone number" in r.json["error"]
    assert desk.get("/api/payments").json[0]["status"] == "declined"


def test_otp_step(desk, fakes):
    ps, _ = fakes
    ps.charge_status = "send_otp"
    mid = enrol(desk)
    p = request_pay(desk, mid, "Telecel Cash")
    assert p["provider_status"] == "send_otp"
    got = desk.post(f"/api/payments/{p['id']}/otp", json={"otp": "123 456"}).json
    assert ps.calls[-1][1:] == ("/charge/submit_otp", {"otp": "123456", "reference": ps.calls[0][2]["reference"]})
    assert got["provider_status"] == "pay_offline"


def test_money_after_cancel_still_counts(app, desk, fakes):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    assert desk.post(f"/api/payments/{p['id']}/cancel", json={}).json["status"] == "expired"
    webhook(app.test_client(), {"event": "charge.success", "data": {"reference": ps.calls[0][2]["reference"], "amount": 500, "currency": "GHS"}})
    assert desk.get(f"/api/payments/{p['id']}").json["status"] == "approved"


def test_claim_code_sent_by_sms_not_shown(desk, fakes):
    ps, sms = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    ps.verify_status = "success"
    desk.get(f"/api/payments/{p['id']}")
    r = desk.post("/api/claims/start", json={"member_id": mid, "type": "gap", "diagnosis": "x", "services": "y",
                                             "amount": 10, "visit_date": desk.clock["d"].isoformat()}).json
    assert "simulated_code" not in r
    code = sms[-1][1].split("is ")[1][:4]
    assert sms[-1][0] == "0241112233"
    c = desk.post("/api/claims/confirm", json={"pending_id": r["pending_id"], "code": code})
    assert c.status_code == 201


def test_sms_failure_blocks_claim(desk, fakes, monkeypatch):
    ps, _ = fakes
    mid = enrol(desk)
    p = request_pay(desk, mid)
    ps.verify_status = "success"
    desk.get(f"/api/payments/{p['id']}")

    def boom(*a):
        raise ProviderError("Insufficient SMS balance")
    monkeypatch.setattr(providers, "arkesel_send", boom)
    r = desk.post("/api/claims/start", json={"member_id": mid, "type": "gap", "diagnosis": "x", "services": "y",
                                             "amount": 10, "visit_date": desk.clock["d"].isoformat()})
    assert r.status_code == 502 and "Insufficient SMS balance" in r.json["error"]


def test_live_mode_without_keys_refuses(app, desk):  # no fakes: keys unset
    app.config.update(SIMULATE=False, PAYSTACK_KEY="", ARKESEL_KEY="")
    mid = enrol(desk)
    r = desk.post("/api/payments", json={"member_id": mid, "network": "MTN MoMo"})
    assert r.status_code == 503 and "Paystack" in r.json["error"]


def test_signature_helper():
    raw = b'{"event":"charge.success"}'
    sig = hmac.new(b"k", raw, hashlib.sha512).hexdigest()
    assert providers.paystack_signature_ok("k", raw, sig)
    assert not providers.paystack_signature_ok("k", raw, "0" * 128)
    assert not providers.paystack_signature_ok("k", raw, None)
