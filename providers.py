"""Mobile money (Paystack) and SMS (Arkesel) for CAIRE Cover.

Each function makes one HTTPS call and raises ProviderError with a message
that can be shown to clinic staff. Tests replace these functions with fakes.
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request

PAYSTACK_API = "https://api.paystack.co"
ARKESEL_API = "https://sms.arkesel.com/api/v2/sms/send"

# App network name -> Paystack mobile money provider code (Ghana).
PAYSTACK_PROVIDERS = {"MTN MoMo": "mtn", "Telecel Cash": "vod", "AirtelTigo Money": "atl"}


class ProviderError(Exception):
    pass


def _call(method, url, headers, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "caire-cover", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")  # providers explain errors in the body
        except ValueError:
            raise ProviderError(f"The provider returned an error ({e.code}). Try again.")
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ProviderError("Could not reach the provider. Check the connection and try again.")


# ---------- Paystack ----------
def paystack_request(secret_key, method, path, body=None):
    """Calls the Paystack API; returns `data` or raises ProviderError with Paystack's message."""
    r = _call(method, PAYSTACK_API + path, {"Authorization": f"Bearer {secret_key}"}, body)
    if not r.get("status"):
        raise ProviderError(r.get("message") or "Mobile money request failed.")
    return r.get("data") or {}


def paystack_signature_ok(secret_key, raw_body, signature):
    """Paystack signs each webhook body with HMAC-SHA512 of the secret key."""
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


# ---------- Arkesel ----------
def arkesel_send(api_key, sender, phone, message):
    """Sends one SMS. `phone` is a 10-digit Ghana number (0241234567)."""
    r = _call("POST", ARKESEL_API, {"api-key": api_key},
              {"sender": sender, "message": message, "recipients": ["233" + phone[1:]]})
    if r.get("status") != "success":
        raise ProviderError(r.get("message") or "The SMS could not be sent.")
    return r
