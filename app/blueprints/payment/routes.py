from flask import render_template, redirect, url_for, request, session, flash
from flask_login import login_user
from app.blueprints.payment import payment_bp
from app.models import BusinessManager, Payment
from app.extensions import db

# Monnify integration will be implemented in the next step.


@payment_bp.route("/checkout")
def checkout():
    """Initial payment page for new managers."""
    manager_id = session.get("pending_manager_id")
    if not manager_id:
        return redirect(url_for("auth.landing"))

    manager = BusinessManager.query.get(manager_id)
    if not manager:
        return redirect(url_for("auth.landing"))

    return render_template("payment/checkout.html", manager=manager)


@payment_bp.route("/subscribe")
def subscribe():
    """Monthly subscription renewal page."""
    manager_id = session.get("pending_manager_id")
    if not manager_id:
        return redirect(url_for("auth.landing"))

    manager = BusinessManager.query.get(manager_id)
    return render_template("payment/subscribe.html", manager=manager)


@payment_bp.route("/webhook", methods=["POST"])
def webhook():
    """Monnify payment webhook — called by Monnify on payment events."""
    # Full implementation in next step
    return {"status": "ok"}, 200
