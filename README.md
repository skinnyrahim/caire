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

Locally, data is stored in SQLite (`caire.db`, or the path in `CAIRE_DB`). When `DATABASE_URL` is set, the app uses
that Postgres database instead.

To also run the tests on Postgres, point `CAIRE_TEST_PG_URL` at an empty, throwaway database (the tests wipe it):
`CAIRE_TEST_PG_URL=postgresql://user@localhost/caire_test python -m pytest tests`.

## Deploy on Render

`render.yaml` is a Render Blueprint: a web service plus a Render Postgres database (`caire-db`) in the same region.

1. In Render: **New > Blueprint**, connect GitHub, pick this repo.
2. When asked, enter `ADMIN_PASSWORD` (a strong password; this is the first admin login).
3. Create. Render creates the database, builds the app, connects them over Render's private network
   (`DATABASE_URL`), generates `SECRET_KEY` and gives you an `https://caire-cover.onrender.com`-style URL. Every push to
   `main` redeploys.
4. Sign in as `admin`, open **Staff**, and add accounts for clinic staff.

The database only accepts connections from Render services, not from the internet. Paid Render Postgres plans include
automatic backups (restore them from the database's **Recovery** page); free databases expire after 30 days, so don't
use one for real data. To copy the data elsewhere, allow your IP address in the database's access control settings, then
run `pg_dump` with the database's external connection URL.

### Moving existing data from the SQLite disk

If the service ran on SQLite before Postgres was added, copy that data across once, from the service's **Shell** tab:

```bash
python scripts/sqlite_to_postgres.py /var/data/caire.db            # shows row counts, copies nothing
python scripts/sqlite_to_postgres.py /var/data/caire.db --replace  # copies everything
```

`--replace` empties Postgres first (including the admin account created on first start), so staff sign in with their
old passwords afterwards. It runs in one transaction. Once you have checked the data in the app, delete the `disk:`
block from `render.yaml`.

## Install as an app

CAIRE Cover is an installable web app (PWA). Once it is live on its `https://` address:

- **Android (Chrome):** open the link, tap **Install app** in the header, or the ⋮ menu > **Install app** / **Add to Home screen**.
- **iPhone/iPad (Safari):** open the link, tap **Share** > **Add to Home Screen**.
- **Windows/Mac (Chrome or Edge):** click the install icon at the right of the address bar.

It then opens full-screen from its own icon. Updates arrive automatically on the next launch. Screens open on a
weak connection, but enrolling, payments and claims need the internet; member data is never stored on the device.

## Install as an app

CAIRE Cover is an installable web app (PWA). Once it is live on its `https://` address:

- **Android (Chrome):** open the link, tap **Install app** in the header, or the ⋮ menu > **Install app** / **Add to Home screen**.
- **iPhone/iPad (Safari):** open the link, tap **Share** > **Add to Home Screen**.
- **Windows/Mac (Chrome or Edge):** click the install icon at the right of the address bar.

It then opens full-screen from its own icon. Updates arrive automatically on the next launch. Screens open on a
weak connection, but enrolling, payments and claims need the internet; member data is never stored on the device.

## Real mobile money and SMS

Mobile money goes through **Paystack** and SMS through **Arkesel** (`providers.py`). Each switches on when its key is
set in Render (**caire-cover > Environment**), and the service redeploys.

| Variable | Value |
|---|---|
| `PAYSTACK_SECRET_KEY` | Paystack **Settings > API Keys & Webhooks**. `sk_test_...` for trying it out, `sk_live_...` for real money |
| `ARKESEL_API_KEY` | Arkesel dashboard, SMS API key |
| `ARKESEL_SENDER` | Your approved sender ID (max 11 characters). Defaults to `CAIRE` |
| `PAYSTACK_EMAIL_DOMAIN` | Optional. Paystack needs an email per customer; members get `<member-id>@<domain>`, default `example.com` (never receives mail) |

In Paystack, set the **Webhook URL** to `https://<your-app>.onrender.com/api/paystack/webhook`
(Settings > API Keys & Webhooks, for test and live mode separately).

How it works:

- **Premium:** the desk sends the prompt; Paystack asks the member to approve on their phone (MTN, AirtelTigo) or, for
  Telecel, texts them a code the desk enters first. Cover starts only when Paystack confirms the money arrived (its
  signed webhook, or the app asking Paystack directly every few seconds), and only if the amount is right. A wrong amount
  is marked **Check in Paystack** and gives no cover. If the desk cancels but the member pays anyway, cover still starts.
  The member gets an SMS saying how long they are covered.
- **Claims:** the 4-digit code goes to the member by SMS and is never shown on the desk screen. If the SMS can't be sent
  (e.g. no Arkesel credit), the claim isn't started and the desk sees why.

Paystack charges a fee per mobile money payment, taken from the amount received.

## Simulation mode

With `CAIRE_SIMULATE=1` (the default), a channel without its key is simulated for demos and training: the mobile money
prompt is answered on the desk screen, and the claim code is shown on a mock phone. "Load sample data" / "Reset" are
also available to admins. The header says which channels are simulated.

Once both keys are set and checked, set `CAIRE_SIMULATE=0`: nothing is ever simulated, demo tools are disabled, and a
missing key shows an error instead of falling back to simulation.
