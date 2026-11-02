"""CAIRE Cover: clinic-desk app for a micro health cover pilot.

Flask + SQLite. The clinic desk enrols members, collects premiums by mobile
money, checks eligibility, captures OTP-confirmed claims and manages the
claims ledger (review, payment, weekly spot-checks).

Staff sign in with a role: desk (enrol, premiums, claims), reviewer (also
approves, pays and spot-checks claims, but never their own) or admin (also
manages staff). Every change is written to the audit log.

Mobile money and SMS are simulated: the payment prompt is approved or declined
from the desk screen, and the claim code is returned to the desk so it can be
shown on a mock phone. Swap `send_sms` and the payment endpoints for real
provider integrations before going live.
"""
import csv
import io
import json
import os
import random
import re
import sqlite3
from datetime import date, datetime, timedelta

import secrets
import time
from functools import wraps

from flask import Flask, Response, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

# Benefit levels are illustrative (GHS). Override with env vars for a pilot.
CFG = {
    "premium": int(os.environ.get("CAIRE_PREMIUM", 15)),  # total per cover period
    "member_share": int(os.environ.get("CAIRE_MEMBER_SHARE", 5)),
    "subsidy": int(os.environ.get("CAIRE_SUBSIDY", 10)),  # paid by RUG
    "cover_days": int(os.environ.get("CAIRE_COVER_DAYS", 30)),
    "gap_limit": int(os.environ.get("CAIRE_GAP_LIMIT", 400)),  # per member per year
    "cash_per_day": int(os.environ.get("CAIRE_CASH_PER_DAY", 45)),
    "cash_max_days": int(os.environ.get("CAIRE_CASH_MAX_DAYS", 10)),  # per year
    "repeat_window_days": 14,
    "otp_minutes": 10,
    "otp_attempts": 3,
    "networks": ["MTN MoMo", "Telecel Cash", "AirtelTigo Money"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL, name TEXT NOT NULL,
  phone TEXT UNIQUE NOT NULL, ghana_card TEXT, nhis TEXT,
  enrolled_on TEXT NOT NULL, consent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT NOT NULL REFERENCES members(id),
  network TEXT NOT NULL, member_amount INTEGER NOT NULL, subsidy_amount INTEGER NOT NULL,
  status TEXT NOT NULL, requested_at TEXT NOT NULL, settled_at TEXT,
  cover_from TEXT, cover_to TEXT);
CREATE TABLE IF NOT EXISTS pending_claims(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL, otp TEXT NOT NULL,
  expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, seq INTEGER UNIQUE NOT NULL, member_id TEXT NOT NULL REFERENCES members(id),
  visit_date TEXT NOT NULL, type TEXT NOT NULL, diagnosis TEXT NOT NULL, services TEXT,
  amount_billed INTEGER, days_requested INTEGER, days INTEGER NOT NULL DEFAULT 0,
  payable INTEGER NOT NULL, flags TEXT NOT NULL, status TEXT NOT NULL, spot TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 1, pw_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, username TEXT,
  action TEXT NOT NULL, ref TEXT, detail TEXT);
"""

# Columns added after the first release; created on start-up if missing.
MIGRATIONS = [("members", "enrolled_by", "TEXT"), ("payments", "requested_by", "TEXT"),
              ("pending_claims", "created_by", "TEXT"), ("claims", "created_by", "TEXT"),
              ("claims", "reviewed_by", "TEXT")]

ROLES = {"desk": 1, "reviewer": 2, "admin": 3}
LOGIN_MAX_FAILS, LOGIN_LOCK_SECONDS = 5, 15 * 60

TRANSITIONS = {"Needs review": {"Approved", "Rejected"}, "Approved": {"Paid", "Rejected"}}


class Bad(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


def create_app(db_path=None, today=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config["DB"] = db_path or os.environ.get("CAIRE_DB", "caire.db")
    app.config["SIMULATE"] = os.environ.get("CAIRE_SIMULATE", "1") == "1"
    # `today` can be a callable returning a date, for tests.
    app.config["TODAY"] = today or date.today
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("CAIRE_SECURE_COOKIES") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if not os.environ.get("SECRET_KEY"):
        app.logger.warning("SECRET_KEY not set: staff will be signed out whenever the app restarts.")
    fails = {}  # username -> recent failed login timestamps (single process)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    @app.teardown_appcontext
    def close(_):
        c = g.pop("db", None)
        if c:
            c.close()

    with app.app_context():
        db().executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = [r[1] for r in db().execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                db().execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # First start: create the admin account from env vars.
        au, ap = os.environ.get("ADMIN_USERNAME", "admin").strip().lower(), os.environ.get("ADMIN_PASSWORD")
        if ap and not db().execute("SELECT 1 FROM users").fetchone():
            db().execute(
                "INSERT INTO users(username,name,role,pw_hash,must_change,created_at) VALUES(?,?,?,?,0,?)",
                (au, "Administrator", "admin", generate_password_hash(ap), datetime.now().isoformat(timespec="seconds")))
            app.logger.info("Created admin account '%s'.", au)
        db().commit()

    def td():
        return app.config["TODAY"]()

    def now():
        return datetime.now().isoformat(timespec="seconds")

    def parse_date(s, field="date"):
        try:
            return date.fromisoformat(s)
        except (TypeError, ValueError):
            raise Bad(f"Enter a valid {field}.")

    def body():
        return request.get_json(silent=True) or {}

    # ---------- staff auth ----------
    PUBLIC = {"static", "index", "healthz", "login", "me", "logout"}
    PW_CHANGE_OK = {"me", "logout", "change_password", "config"}

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        u = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if not u or u["pw_version"] != session.get("pv"):
            session.clear()
            return None
        return u

    def me_name():
        return g.user["username"] if g.get("user") else None

    def audit(action, ref=None, detail=None):
        db().execute("INSERT INTO audit(ts,username,action,ref,detail) VALUES(?,?,?,?,?)",
                     (now(), me_name(), action, ref, detail))

    def role(min_role):
        def deco(fn):
            @wraps(fn)
            def wrapped(*a, **kw):
                if ROLES[g.user["role"]] < ROLES[min_role]:
                    raise Bad("Your account does not have permission to do this.", 403)
                return fn(*a, **kw)
            return wrapped
        return deco

    def check_password(pw):
        if len(pw or "") < 8:
            raise Bad("Use a password of at least 8 characters.")

    @app.before_request
    def guard():
        g.user = current_user()
        if not request.path.startswith("/api/") or request.endpoint in PUBLIC:
            return
        if not g.user:
            raise Bad("Please sign in.", 401)
        # JSON-only writes: a cross-site form cannot send these, which blocks CSRF.
        if request.method == "POST" and not request.is_json:
            raise Bad("Send JSON.", 415)
        if g.user["must_change"] and request.endpoint not in PW_CHANGE_OK:
            raise Bad("Set a new password first.", 403)

    def user_json(u):
        return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
                "active": bool(u["active"]), "must_change": bool(u["must_change"])}

    # ---------- domain helpers ----------
    def member(mid):
        r = db().execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if not r:
            raise Bad("Member not found.", 404)
        return r

    def paid_through(mid):
        r = db().execute(
            "SELECT MAX(cover_to) FROM payments WHERE member_id=? AND status='approved'", (mid,)
        ).fetchone()[0]
        return r

    def covered_on(mid, d):
        ds = d.isoformat()
        return db().execute(
            "SELECT 1 FROM payments WHERE member_id=? AND status='approved' AND cover_from<=? AND cover_to>=?",
            (mid, ds, ds),
        ).fetchone() is not None

    def remaining(mid, year):
        rows = db().execute(
            "SELECT type, payable, days FROM claims WHERE member_id=? AND status!='Rejected' AND substr(visit_date,1,4)=?",
            (mid, str(year)),
        ).fetchall()
        gap = sum(r["payable"] for r in rows if r["type"] == "gap")
        days = sum(r["days"] for r in rows if r["type"] == "cash")
        return {"gap": CFG["gap_limit"] - gap, "days": CFG["cash_max_days"] - days}

    def member_json(r):
        pt = paid_through(r["id"])
        return {
            "id": r["id"], "name": r["name"], "phone": r["phone"], "ghana_card": r["ghana_card"],
            "nhis": r["nhis"], "enrolled_on": r["enrolled_on"], "paid_through": pt,
            "covered": covered_on(r["id"], td()),
        }

    def claim_json(r):
        d = dict(r)
        d["flags"] = json.loads(d["flags"])
        d.pop("seq", None)
        m = db().execute("SELECT name, phone FROM members WHERE id=?", (r["member_id"],)).fetchone()
        d["member_name"], d["member_phone"] = (m["name"], m["phone"]) if m else (None, None)
        return d

    def next_seq(table):
        return db().execute(f"SELECT COALESCE(MAX(seq),0)+1 FROM {table}").fetchone()[0]

    def norm_phone(p):
        p = re.sub(r"\D", "", p or "")
        if p.startswith("233") and len(p) == 12:
            p = "0" + p[3:]
        return p

    def send_sms(phone, text):
        # Integration point for a real SMS gateway. Simulated by default.
        app.logger.info("SMS to %s: %s", phone, text)

    def do_enrol(name, phone, card="", nhis=""):
        seq = next_seq("members")
        mid = f"CC-{seq:04d}"
        db().execute(
            "INSERT INTO members(id,seq,name,phone,ghana_card,nhis,enrolled_on,consent_at,enrolled_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (mid, seq, name, phone, card, nhis, td().isoformat(), now(), me_name()),
        )
        audit("member_enrolled", mid)
        return mid

    def do_approve(pid):
        p = db().execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if not p:
            raise Bad("Payment not found.", 404)
        if p["status"] != "pending":
            raise Bad("This payment prompt has already been answered.", 409)
        pt = paid_through(p["member_id"])
        start = td()
        if pt and date.fromisoformat(pt) >= start:
            start = date.fromisoformat(pt) + timedelta(days=1)  # renewals stack
        end = start + timedelta(days=CFG["cover_days"] - 1)
        db().execute(
            "UPDATE payments SET status='approved', settled_at=?, cover_from=?, cover_to=? WHERE id=?",
            (now(), start.isoformat(), end.isoformat(), pid),
        )
        audit("payment_approved", p["member_id"], f"payment {pid}, cover to {end.isoformat()}")
        return end

    # ---------- errors ----------
    @app.errorhandler(Bad)
    def bad(e):
        return jsonify(error=e.msg), e.code

    # ---------- routes ----------
    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/healthz")
    def healthz():
        db().execute("SELECT 1")
        return "ok"

    @app.post("/api/login")
    def login():
        if not request.is_json:
            raise Bad("Send JSON.", 415)
        b = body()
        un = (b.get("username") or "").strip().lower()
        t = time.time()
        recent = [x for x in fails.get(un, []) if t - x < LOGIN_LOCK_SECONDS]
        if len(recent) >= LOGIN_MAX_FAILS:
            raise Bad("Too many failed attempts. Try again in 15 minutes.", 429)
        u = db().execute("SELECT * FROM users WHERE username=? AND active=1", (un,)).fetchone()
        if not u or not check_password_hash(u["pw_hash"], b.get("password") or ""):
            fails[un] = recent + [t]
            raise Bad("Wrong username or password.", 401)
        fails.pop(un, None)
        session.clear()
        session.permanent = True
        session["uid"], session["pv"] = u["id"], u["pw_version"]
        g.user = u
        audit("login")
        db().commit()
        return jsonify(user_json(u))

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    def me():
        if not g.user:
            raise Bad("Please sign in.", 401)
        return jsonify(user_json(g.user))

    @app.post("/api/me/password")
    def change_password():
        b = body()
        if not check_password_hash(g.user["pw_hash"], b.get("current") or ""):
            raise Bad("Your current password is wrong.")
        check_password(b.get("new"))
        if b.get("new") == b.get("current"):
            raise Bad("Choose a password different from the current one.")
        pv = g.user["pw_version"] + 1
        db().execute("UPDATE users SET pw_hash=?, must_change=0, pw_version=? WHERE id=?",
                     (generate_password_hash(b["new"]), pv, g.user["id"]))
        session["pv"] = pv
        audit("password_changed", g.user["username"])
        db().commit()
        return jsonify({"ok": True})

    @app.get("/api/staff")
    @role("admin")
    def staff():
        return jsonify([user_json(u) for u in db().execute("SELECT * FROM users ORDER BY name").fetchall()])

    @app.post("/api/staff")
    @role("admin")
    def add_staff():
        b = body()
        un = (b.get("username") or "").strip().lower()
        name = (b.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9._-]{3,32}", un) or not name:
            raise Bad("Enter a name and a username of 3 to 32 letters, numbers, dots or dashes.")
        if b.get("role") not in ROLES:
            raise Bad("Pick a role.")
        check_password(b.get("password"))
        if db().execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
            raise Bad("That username is taken.", 409)
        cur = db().execute("INSERT INTO users(username,name,role,pw_hash,created_at) VALUES(?,?,?,?,?)",
                           (un, name, b["role"], generate_password_hash(b["password"]), now()))
        audit("staff_added", un, b["role"])
        db().commit()
        return jsonify(user_json(db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())), 201

    @app.post("/api/staff/<int:uid>")
    @role("admin")
    def edit_staff(uid):
        b = body()
        u = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            raise Bad("Staff account not found.", 404)
        if uid == g.user["id"] and (("active" in b and not b["active"]) or b.get("role", "admin") != "admin"):
            raise Bad("You cannot disable or demote your own account.")
        if "role" in b:
            if b["role"] not in ROLES:
                raise Bad("Pick a role.")
            db().execute("UPDATE users SET role=? WHERE id=?", (b["role"], uid))
            audit("staff_role", u["username"], b["role"])
        if "active" in b:
            db().execute("UPDATE users SET active=? WHERE id=?", (1 if b["active"] else 0, uid))
            audit("staff_enabled" if b["active"] else "staff_disabled", u["username"])
        if b.get("password"):
            check_password(b["password"])
            db().execute("UPDATE users SET pw_hash=?, must_change=1, pw_version=pw_version+1 WHERE id=?",
                         (generate_password_hash(b["password"]), uid))
            audit("staff_password_reset", u["username"])
        db().commit()
        return jsonify(user_json(db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()))

    @app.get("/api/audit")
    @role("admin")
    def audit_log():
        rows = db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/config")
    def config():
        return jsonify({**CFG, "simulate": app.config["SIMULATE"], "today": td().isoformat()})

    @app.get("/api/members")
    def members():
        rows = db().execute("SELECT * FROM members ORDER BY seq").fetchall()
        return jsonify([member_json(r) for r in rows])

    @app.post("/api/members")
    def enrol():
        b = body()
        name = (b.get("name") or "").strip()
        phone = norm_phone(b.get("phone"))
        if not name or len(phone) != 10:
            raise Bad("Enter the member's name and a valid 10-digit phone number.")
        if not b.get("consent"):
            raise Bad("Record the member's consent before enrolling.")
        if db().execute("SELECT 1 FROM members WHERE phone=?", (phone,)).fetchone():
            raise Bad("A member with this phone number already exists.", 409)
        card = (b.get("ghana_card") or "").strip().upper()
        if card and db().execute("SELECT 1 FROM members WHERE ghana_card=?", (card,)).fetchone():
            raise Bad("A member with this Ghana Card number already exists.", 409)
        mid = do_enrol(name, phone, card, (b.get("nhis") or "").strip())
        db().commit()
        return jsonify(member_json(member(mid))), 201

    @app.get("/api/payments")
    def payments():
        rows = db().execute(
            "SELECT p.*, m.name member_name FROM payments p JOIN members m ON m.id=p.member_id ORDER BY p.id DESC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/payments")
    def request_payment():
        b = body()
        m = member(b.get("member_id"))
        net = b.get("network")
        if net not in CFG["networks"]:
            raise Bad("Pick a mobile money network.")
        db().execute("UPDATE payments SET status='expired' WHERE member_id=? AND status='pending'", (m["id"],))
        cur = db().execute(
            "INSERT INTO payments(member_id,network,member_amount,subsidy_amount,status,requested_at,requested_by) VALUES(?,?,?,?, 'pending', ?, ?)",
            (m["id"], net, CFG["member_share"], CFG["subsidy"], now(), me_name()),
        )
        db().commit()
        return jsonify({"id": cur.lastrowid, "member_id": m["id"], "member_name": m["name"],
                        "phone": m["phone"], "network": net, "amount": CFG["member_share"]}), 201

    # Simulated mobile money callback. A real integration would call this
    # from the provider's webhook after verifying its signature.
    @app.post("/api/payments/<int:pid>/<action>")
    def settle_payment(pid, action):
        if not app.config["SIMULATE"]:
            raise Bad("Payments are confirmed by the mobile money provider.", 403)
        if action == "approve":
            end = do_approve(pid)
            db().commit()
            return jsonify({"status": "approved", "paid_through": end.isoformat()})
        if action == "decline":
            n = db().execute("UPDATE payments SET status='declined', settled_at=? WHERE id=? AND status='pending'",
                             (now(), pid)).rowcount
            if not n:
                raise Bad("This payment prompt is no longer pending.", 409)
            audit("payment_declined", None, f"payment {pid}")
            db().commit()
            return jsonify({"status": "declined"})
        raise Bad("Unknown action.", 404)

    @app.get("/api/eligibility")
    def eligibility():
        q = (request.args.get("q") or "").strip()
        if not q:
            raise Bad("Enter a phone number, member ID or Ghana Card number.")
        r = db().execute(
            "SELECT * FROM members WHERE upper(id)=? OR phone=? OR (ghana_card!='' AND upper(ghana_card)=?)",
            (q.upper().replace(" ", ""), norm_phone(q), q.upper().replace(" ", "")),
        ).fetchone()
        if not r:
            return jsonify({"found": False})
        m = member_json(r)
        return jsonify({"found": True, "member": m, "remaining": remaining(r["id"], td().year) if m["covered"] else None})

    @app.post("/api/claims/start")
    def claim_start():
        b = body()
        m = member(b.get("member_id"))
        visit = parse_date(b.get("visit_date") or td().isoformat(), "visit date")
        if visit > td():
            raise Bad("The visit date cannot be in the future.")
        if not covered_on(m["id"], visit):
            raise Bad("Not covered on the visit date: premium was not up to date.")
        ctype = b.get("type")
        dx = (b.get("diagnosis") or "").strip()
        if ctype not in ("gap", "cash"):
            raise Bad("Pick a benefit.")
        if not dx:
            raise Bad("Enter the diagnosis.")
        data = {"member_id": m["id"], "visit_date": visit.isoformat(), "type": ctype, "diagnosis": dx}
        rem = remaining(m["id"], visit.year)
        if ctype == "gap":
            svc = (b.get("services") or "").strip()
            try:
                amt = int(round(float(b.get("amount") or 0)))
            except ValueError:
                amt = 0
            if amt <= 0 or not svc:
                raise Bad("Enter the services and the amount.")
            if rem["gap"] <= 0:
                raise Bad("This benefit is used up for the year.")
            data.update(services=svc, amount=amt)
        else:
            try:
                days = int(b.get("days") or 0)
            except ValueError:
                days = 0
            if days < 1 or days > 60:
                raise Bad("Enter the number of days off work (1 to 60).")
            if rem["days"] <= 0:
                raise Bad("This benefit is used up for the year.")
            data.update(days=days)
        otp = f"{random.SystemRandom().randint(1000, 9999)}"
        exp = (datetime.now() + timedelta(minutes=CFG["otp_minutes"])).isoformat(timespec="seconds")
        cur = db().execute("INSERT INTO pending_claims(data,otp,expires_at,created_by) VALUES(?,?,?,?)",
                           (json.dumps(data), otp, exp, me_name()))
        db().commit()
        send_sms(m["phone"], f"CAIRE Cover: your code to confirm today's visit is {otp}. "
                             "Share it only if you are at the clinic.")
        out = {"pending_id": cur.lastrowid, "member_name": m["name"], "phone": m["phone"], **data}
        if app.config["SIMULATE"]:
            out["simulated_code"] = otp
        return jsonify(out), 201

    @app.post("/api/claims/confirm")
    def claim_confirm():
        b = body()
        p = db().execute("SELECT * FROM pending_claims WHERE id=?", (b.get("pending_id"),)).fetchone()
        if not p:
            raise Bad("This claim has expired. Start again.", 404)
        if datetime.fromisoformat(p["expires_at"]) < datetime.now() or p["attempts"] >= CFG["otp_attempts"]:
            db().execute("DELETE FROM pending_claims WHERE id=?", (p["id"],))
            db().commit()
            raise Bad("The code has expired. Start the claim again.", 410)
        if (b.get("code") or "").strip() != p["otp"]:
            db().execute("UPDATE pending_claims SET attempts=attempts+1 WHERE id=?", (p["id"],))
            db().commit()
            left = CFG["otp_attempts"] - p["attempts"] - 1
            raise Bad(f"Code does not match. No code, no claim. {left} attempt(s) left.")
        d = json.loads(p["data"])
        visit = date.fromisoformat(d["visit_date"])
        rem = remaining(d["member_id"], visit.year)
        flags = []
        lo = (visit - timedelta(days=CFG["repeat_window_days"])).isoformat()
        hi = (visit + timedelta(days=CFG["repeat_window_days"])).isoformat()
        if db().execute(
            "SELECT 1 FROM claims WHERE member_id=? AND status!='Rejected' AND visit_date BETWEEN ? AND ?",
            (d["member_id"], lo, hi),
        ).fetchone():
            flags.append(f"Repeat claim within {CFG['repeat_window_days']} days")
        days = 0
        if d["type"] == "gap":
            if rem["gap"] <= 0:
                raise Bad("This benefit is used up for the year.")
            payable = min(d["amount"], rem["gap"])
            if d["amount"] > rem["gap"]:
                flags.append("Amount above remaining limit")
            if payable >= 0.9 * rem["gap"]:
                flags.append("Uses 90% or more of remaining limit")
        else:
            if rem["days"] <= 0:
                raise Bad("This benefit is used up for the year.")
            days = min(d["days"], rem["days"])
            payable = days * CFG["cash_per_day"]
            if d["days"] > rem["days"]:
                flags.append("Days above remaining limit")
            if d["days"] >= 7:
                flags.append("Long stay (7+ days)")
        seq = next_seq("claims")
        cid = f"CL-{seq:04d}"
        status = "Needs review" if flags else "Approved"
        db().execute(
            "INSERT INTO claims(id,seq,member_id,visit_date,type,diagnosis,services,amount_billed,days_requested,days,payable,flags,status,created_at,updated_at,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, seq, d["member_id"], d["visit_date"], d["type"], d["diagnosis"], d.get("services"),
             d.get("amount"), d.get("days"), days, payable, json.dumps(flags), status, now(), now(), p["created_by"]),
        )
        audit("claim_submitted", cid, f"{status}, GHS {payable}")
        db().execute("DELETE FROM pending_claims WHERE id=?", (p["id"],))
        db().commit()
        return jsonify(claim_json(db().execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone())), 201

    @app.get("/api/claims")
    def claims():
        rows = db().execute("SELECT * FROM claims ORDER BY seq DESC").fetchall()
        return jsonify([claim_json(r) for r in rows])

    def get_claim(cid):
        r = db().execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
        if not r:
            raise Bad("Claim not found.", 404)
        return r

    @app.post("/api/claims/<cid>/status")
    @role("reviewer")
    def set_status(cid):
        c = get_claim(cid)
        s = body().get("status")
        if s not in TRANSITIONS.get(c["status"], set()):
            raise Bad(f"A claim that is '{c['status']}' cannot be set to '{s}'.", 409)
        if c["created_by"] and c["created_by"] == me_name():
            raise Bad("You submitted this claim, so another reviewer must decide it.", 403)
        db().execute("UPDATE claims SET status=?, reviewed_by=?, updated_at=? WHERE id=?", (s, me_name(), now(), cid))
        audit("claim_" + s.lower(), cid)
        db().commit()
        return jsonify(claim_json(get_claim(cid)))

    @app.post("/api/spotchecks/draw")
    @role("reviewer")
    def spot_draw():
        n = int(body().get("count") or 2)
        rows = db().execute(
            "SELECT id FROM claims WHERE status IN ('Approved','Paid') AND spot IS NULL"
        ).fetchall()
        pick = random.sample([r["id"] for r in rows], min(n, len(rows)))
        for cid in pick:
            db().execute("UPDATE claims SET spot='pending', updated_at=? WHERE id=?", (now(), cid))
            audit("spotcheck_drawn", cid)
        db().commit()
        return jsonify({"picked": pick})

    @app.post("/api/claims/<cid>/spot")
    @role("reviewer")
    def spot_result(cid):
        c = get_claim(cid)
        v = body().get("result")
        if c["spot"] != "pending" or v not in ("confirmed", "disputed"):
            raise Bad("This claim is not waiting for a verification call.", 409)
        status, flags = c["status"], json.loads(c["flags"])
        if v == "disputed":
            flags.append("Member disputed on verification call")
            if status == "Approved":
                status = "Needs review"
            elif status == "Paid":
                flags.append("Already paid: recover from clinic if confirmed false")
        db().execute("UPDATE claims SET spot=?, status=?, flags=?, updated_at=? WHERE id=?",
                     (v, status, json.dumps(flags), now(), cid))
        audit("spotcheck_" + v, cid)
        db().commit()
        return jsonify(claim_json(get_claim(cid)))

    @app.get("/api/summary")
    def summary():
        x = db().execute
        members_n = x("SELECT COUNT(*) FROM members").fetchone()[0]
        covered = sum(1 for r in x("SELECT id FROM members").fetchall() if covered_on(r["id"], td()))
        prem = x("SELECT COALESCE(SUM(member_amount),0), COALESCE(SUM(subsidy_amount),0) FROM payments WHERE status='approved'").fetchone()
        payable = x("SELECT COALESCE(SUM(payable),0) FROM claims WHERE status!='Rejected'").fetchone()[0]
        paid = x("SELECT COALESCE(SUM(payable),0) FROM claims WHERE status='Paid'").fetchone()[0]
        review = x("SELECT COUNT(*) FROM claims WHERE status='Needs review'").fetchone()[0]
        total = prem[0] + prem[1]
        return jsonify({
            "members": members_n, "covered": covered, "premiums_member": prem[0], "premiums_subsidy": prem[1],
            "premiums": total, "claims_payable": payable, "claims_paid": paid, "needs_review": review,
            "claims_ratio": round(payable / total * 100) if total else None,
        })

    @app.get("/api/export/claims.csv")
    @role("reviewer")
    def export_claims():
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["id", "member_id", "member_name", "visit_date", "type", "diagnosis", "services",
                "amount_billed", "days", "payable", "status", "flags", "spot", "created_at", "created_by", "reviewed_by"]
        w.writerow(cols)
        for r in db().execute("SELECT * FROM claims ORDER BY seq").fetchall():
            c = claim_json(r)
            c["flags"] = "; ".join(c["flags"])
            w.writerow([c.get(k) for k in cols])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=caire-claims.csv"})

    @app.post("/api/demo/seed")
    @role("admin")
    def seed():
        if not app.config["SIMULATE"]:
            raise Bad("Sample data is only available in simulation mode.", 403)
        added = []
        for i, (n, p) in enumerate([("Ama Mensah", "0241112233"), ("Kofi Boateng", "0202223344"),
                                     ("Abena Owusu", "0553334455")]):
            if db().execute("SELECT 1 FROM members WHERE phone=?", (p,)).fetchone():
                continue
            mid = do_enrol(n, p)
            added.append(mid)
            if i < 2:
                cur = db().execute(
                    "INSERT INTO payments(member_id,network,member_amount,subsidy_amount,status,requested_at) VALUES(?,?,?,?,'pending',?)",
                    (mid, "MTN MoMo", CFG["member_share"], CFG["subsidy"], now()))
                do_approve(cur.lastrowid)
        db().commit()
        return jsonify({"added": added})

    @app.post("/api/demo/reset")
    @role("admin")
    def reset():
        if not app.config["SIMULATE"]:
            raise Bad("Reset is only available in simulation mode.", 403)
        for t in ("claims", "pending_claims", "payments", "members"):
            db().execute(f"DELETE FROM {t}")
        audit("demo_reset")
        db().commit()
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
