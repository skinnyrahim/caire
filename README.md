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

## Run

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
python -m pytest tests   # rule tests
```

Data is stored in SQLite (`caire.db`, or the path in `CAIRE_DB`). For a server, use a WSGI host, e.g.
`gunicorn "app:create_app()"`.

## Simulation mode

`CAIRE_SIMULATE=1` (the default) is for demos and training: the mobile money prompt is approved or declined on the desk
screen, the claim code is shown on a mock phone, and "Load sample data" / "Reset" are available. Before a live pilot:

- set `CAIRE_SIMULATE=0`, which hides the code from the desk and disables seed/reset
- connect `send_sms` in `app.py` to an SMS gateway
- replace `POST /api/payments/<id>/approve|decline` with the mobile money provider's verified webhook
- add staff login; the API has no authentication yet
