# CAIRE Cover

Clinic-desk app for the CAIRE Cover micro health insurance pilot. Clinic staff use it to:

1. **Enrol** members (name, mobile money phone, Ghana Card, NHIS, recorded consent)
2. **Collect premiums** by mobile money: each 30-day cover costs GHS 15, with the member paying GHS 5 and RUG subsidising GHS 10. Paying early adds 30 days after the current cover ends.
3. **Check eligibility** by phone, member ID or Ghana Card, with the benefits left this year
4. **Submit claims**: the member confirms each visit with a 4-digit SMS code ("no code, no claim")
   - *Gap cover*: labs, scans and medicines, up to GHS 400 per member per year
   - *Hospital cash*: GHS 45 a day off work, up to 10 days per year
5. **Run the ledger**: review flagged claims, mark claims paid, draw weekly spot-check calls, export CSV

Claims are flagged for review if there is another claim within 14 days, the amount exceeds or uses 90%+ of the
remaining limit, the stay is 7+ days, or the member disputes the visit on a spot-check call. Claims without flags are
approved automatically.

Benefit levels are illustrative. Set them with `CAIRE_PREMIUM`, `CAIRE_MEMBER_SHARE`, `CAIRE_SUBSIDY`,
`CAIRE_COVER_DAYS`, `CAIRE_GAP_LIMIT`, `CAIRE_CASH_PER_DAY` and `CAIRE_CASH_MAX_DAYS`.

## Staff accounts

Everyone signs in. Roles:

| Role | Can |
|---|---|
| desk | enrol, collect premiums, check eligibility, submit claims, view the ledger |
| reviewer | the above, plus approve/reject/mark paid, spot-checks, CSV export. Never on a claim they submitted |
| admin | the above, plus add staff, change roles, reset passwords, disable accounts, view the activity log |

The first admin is created on first start from `ADMIN_USERNAME` (default `admin`) and `ADMIN_PASSWORD`. Staff added by
an admin get a temporary password and must set their own (8+ characters) when they first sign in. Five wrong passwords
lock a username for 15 minutes. Disabling an account or resetting its password signs it out everywhere. Every change is
recorded in the activity log (Staff tab).

## Run locally

```bash
pip install -r requirements.txt
ADMIN_PASSWORD=choose-a-password python app.py   # http://127.0.0.1:5000, sign in as admin
python -m pytest tests
```

Data is stored in SQLite (`caire.db`, or the path in `CAIRE_DB`).

## Deploy on Render

`render.yaml` is a Render Blueprint.

1. Merge this work into `main` (Render deploys `main`).
2. In Render: **New > Blueprint**, connect GitHub, pick this repo.
3. When asked, enter `ADMIN_PASSWORD` (a strong password; this is the first admin login).
4. Create. Render builds, attaches a 1 GB disk at `/var/data` for the database, generates `SECRET_KEY` and gives you
   an `https://caire-cover.onrender.com`-style URL. Every push to `main` redeploys.
5. Sign in as `admin`, open **Staff**, and add accounts for clinic staff.

The disk needs a paid instance (Starter); the free plan has no disk, so data would be lost on every restart. Render
snapshots disks daily. Keep one worker (`--workers 1`), as configured, while the app uses SQLite. A custom domain can
be added under the service's Settings.

## Install as an app

CAIRE Cover is an installable web app (PWA). Once it is live on its `https://` address:

- **Android (Chrome):** open the link, tap **Install app** in the header, or the ⋮ menu > **Install app** / **Add to Home screen**.
- **iPhone/iPad (Safari):** open the link, tap **Share** > **Add to Home Screen**.
- **Windows/Mac (Chrome or Edge):** click the install icon at the right of the address bar.

It then opens full-screen from its own icon. Updates arrive automatically on the next launch. Screens open on a
weak connection, but enrolling, payments and claims need the internet; member data is never stored on the device.

## Simulation mode

`CAIRE_SIMULATE=1` (the default) is for demos and training: the mobile money prompt is approved or declined on the desk
screen, the claim code is shown on a mock phone, and "Load sample data" / "Reset" are available. Before a live pilot:

- set `CAIRE_SIMULATE=0`, which hides the code from the desk and disables seed/reset
- connect `send_sms` in `app.py` to an SMS gateway
- replace `POST /api/payments/<id>/approve|decline` with the mobile money provider's verified webhook
