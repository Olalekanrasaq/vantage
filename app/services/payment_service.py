import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from decimal import Decimal

import requests
from flask import current_app

from app.extensions import db
from app.models import Payment

TIMEOUT = 20
_token = {"value": None, "expires_at": 0.0}


class MonnifyError(Exception):
    pass


def _base():
    return current_app.config["MONNIFY_BASE_URL"].rstrip("/")


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_token():
    if _token["value"] and _token["expires_at"] > time.time() + 30:
        return _token["value"]

    creds = f'{current_app.config["MONNIFY_API_KEY"]}:{current_app.config["MONNIFY_SECRET_KEY"]}'
    auth = base64.b64encode(creds.encode()).decode()
    r = requests.post(
        f"{_base()}/api/v1/auth/login",
        headers={"Authorization": f"Basic {auth}"},
        timeout=TIMEOUT,
    )
    data = r.json()
    if not r.ok or not data.get("requestSuccessful"):
        raise MonnifyError(data.get("responseMessage", "Monnify auth failed"))

    body = data["responseBody"]
    _token["value"] = body["accessToken"]
    _token["expires_at"] = time.time() + int(body.get("expiresIn", 3000))
    return _token["value"]


def _headers():
    return {"Authorization": f"Bearer {_get_token()}"}


# ── Initialise a checkout ─────────────────────────────────────────────────────

def initialize_payment(manager) -> str:
    """Create a Monnify transaction, persist a pending Payment, return the checkout URL."""
    cfg = current_app.config
    amount = Decimal(str(cfg["SUBSCRIPTION_AMOUNT"]))
    payment_type = "subscription" if manager.subscription_expiry else "one_time"
    payment_reference = f"VNT-{manager.id}-{uuid.uuid4().hex[:12]}"

    payload = {
        "amount": float(amount),
        "customerName": getattr(manager, "full_name", None) or manager.email,
        "customerEmail": manager.email,
        "paymentReference": payment_reference,
        "paymentDescription": "Vantage subscription",
        "currencyCode": "NGN",
        "contractCode": cfg["MONNIFY_CONTRACT_CODE"],
        "redirectUrl": f'{cfg["APP_URL"]}/payment/callback',
        "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"],
    }
    r = requests.post(
        f"{_base()}/api/v1/merchant/transactions/init-transaction",
        json=payload, headers=_headers(), timeout=TIMEOUT,
    )
    data = r.json()
    if not r.ok or not data.get("requestSuccessful"):
        raise MonnifyError(data.get("responseMessage", "Could not start payment"))

    body = data["responseBody"]
    db.session.add(Payment(
        manager_id=manager.id,
        transaction_reference=body["transactionReference"],
        payment_reference=payment_reference,
        amount=amount,
        currency="NGN",
        payment_type=payment_type,
        status="pending",
    ))
    db.session.commit()
    return body["checkoutUrl"]


# ── Verification ──────────────────────────────────────────────────────────────

def verify_signature(raw_body: bytes, signature: str) -> bool:
    """Monnify signs the raw webhook body with HMAC-SHA512 using your secret key."""
    secret = current_app.config["MONNIFY_SECRET_KEY"].encode()
    expected = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def fetch_transaction(payment_reference: str) -> dict:
    """Server-to-server status check, used by the redirect callback."""
    r = requests.get(
        f"{_base()}/api/v2/merchant/transactions/query",
        params={"paymentReference": payment_reference},
        headers=_headers(), timeout=TIMEOUT,
    )
    data = r.json()
    if not r.ok or not data.get("requestSuccessful"):
        raise MonnifyError(data.get("responseMessage", "Could not verify payment"))
    return data["responseBody"]


# ── Activation (idempotent) ───────────────────────────────────────────────────

def activate_payment(payment_id: int, gateway_data: dict, raw_event: dict | None = None) -> bool:
    """
    Mark a payment successful and extend the manager's subscription.
    Safe to call from both the webhook and the callback: the row is locked and
    already-successful payments are ignored. Returns True only on first activation.
    """
    payment = Payment.query.filter_by(id=payment_id).with_for_update().first()
    if payment is None or payment.status == "successful":
        db.session.rollback()
        return False

    paid = Decimal(str(gateway_data.get("amountPaid", 0)))
    if gateway_data.get("paymentStatus") != "PAID" or paid < payment.amount:
        db.session.rollback()
        return False

    now = _utcnow_naive()
    manager = payment.manager

    current_expiry = manager.subscription_expiry
    if current_expiry is not None and current_expiry.tzinfo is not None:
        current_expiry = current_expiry.astimezone(timezone.utc).replace(tzinfo=None)

    grace = timedelta(days=current_app.config["GRACE_DAYS"])
    if current_expiry and now <= current_expiry + grace:
        start = current_expiry   # early, on time or in grace: keep the original due date
    else:
        start = now              # first payment or lapsed past grace: fresh cycle
    
    payment.status = "successful"
    payment.paid_at = now
    if raw_event is not None:
        payment.webhook_payload = raw_event
    manager.is_active = True
    manager.subscription_expiry = start + relativedelta(months=current_app.config["SUBSCRIPTION_MONTHS"])
    db.session.commit()

    try:
        from app.services.mail_service import send_payment_confirmation
        send_payment_confirmation(manager, f"NGN {payment.amount:,.2f}", payment.payment_type)
    except Exception as e:
        current_app.logger.warning("Payment email failed: %s", e)
    return True