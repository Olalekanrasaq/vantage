from flask import render_template, redirect, url_for, request, session, flash, abort, current_app
from flask_login import current_user, login_user

from app.blueprints.payment import payment_bp
from app.extensions import db
from app.models import BusinessManager, Payment
from app.services.payment_service import (
    MonnifyError, initialize_payment, fetch_transaction,
    verify_signature, activate_payment,
)


def _payment_manager():
    """Logged-in manager (grace period) or the pending one from signup/login."""
    if current_user.is_authenticated:
        return current_user if isinstance(current_user, BusinessManager) else None
    mid = session.get("pending_manager_id")
    return db.session.get(BusinessManager, mid) if mid else None


def _render_payment_page():
    manager = _payment_manager()
    if manager is None:
        return redirect(url_for("auth.user_login"))
    status = manager.subscription_status
    if status == "active":
        return redirect(url_for("manager.dashboard") if current_user.is_authenticated
                        else url_for("auth.user_login"))
    return render_template(
        "payment/checkout.html",
        status=status,
        grace_days_left=manager.grace_days_left,
        amount=current_app.config["SUBSCRIPTION_AMOUNT"],
    )


@payment_bp.route("/checkout")
def checkout():
    return _render_payment_page()


@payment_bp.route("/subscribe")
def subscribe():
    return _render_payment_page()


@payment_bp.route("/initiate", methods=["POST"])
def initiate():
    manager = _payment_manager()
    if manager is None:
        return redirect(url_for("auth.user_login"))
    if manager.subscription_status == "active":
        return redirect(url_for("manager.dashboard"))
    try:
        return redirect(initialize_payment(manager))
    except MonnifyError as e:
        flash(f"Payment could not be started: {e}", "error")
    except Exception:
        current_app.logger.exception("Monnify init failed")
        flash("Payment service is unreachable. Please try again.", "error")
    return redirect(url_for("payment.checkout"))


@payment_bp.route("/callback")
def callback():
    ref = request.args.get("paymentReference", "")
    payment = Payment.query.filter_by(payment_reference=ref).first()
    manager = _payment_manager()

    # The payment must belong to the manager in this browser session,
    # otherwise anyone could log in as someone else with a reference.
    if not payment or not manager or payment.manager_id != manager.id:
        flash("Please log in to continue.", "info")
        return redirect(url_for("auth.user_login"))

    try:
        activate_payment(payment.id, fetch_transaction(ref))
    except Exception:
        current_app.logger.exception("Monnify verify failed")
        flash("We could not confirm your payment yet. If you were charged, your access will activate shortly. Try logging in again in a few minutes.", "warning")
        return redirect(url_for("payment.checkout"))

    db.session.refresh(payment)
    db.session.refresh(manager)
    if payment.status == "successful":
        session.pop("pending_manager_id", None)
        if not current_user.is_authenticated:
            login_user(manager)
        flash("Payment confirmed. Your account is active.", "success")
        return redirect(url_for("manager.dashboard"))

    flash("Payment was not completed.", "error")
    return redirect(url_for("payment.checkout"))


@payment_bp.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data()
    if not verify_signature(raw, request.headers.get("monnify-signature", "")):
        return {"status": "invalid signature"}, 401

    event = request.get_json(silent=True) or {}
    if event.get("eventType") == "SUCCESSFUL_TRANSACTION":
        data = event.get("eventData", {})
        payment = Payment.query.filter_by(payment_reference=data.get("paymentReference")).first()
        if payment:
            activate_payment(payment.id, data, raw_event=event)
    return {"status": "ok"}, 200