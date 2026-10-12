import hashlib
import hmac
from itsdangerous import URLSafeTimedSerializer, BadSignature

from flask import render_template, redirect, url_for, request, session, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func

from app.blueprints.auth import auth_bp
from app.extensions import db, limiter
from app.models import BusinessManager, Staff, SuperAdmin
from app.services.mail_service import send_password_reset


def _failed_login(response):
    """Count an attempt against the limit only when the login failed.
    Success redirects (302); failure re-renders the form (200)."""
    return response.status_code == 200


# ── Landing page ──────────────────────────────────────────────────────────────

@auth_bp.route("/")
def landing():
    return render_template("auth/landing.html")


# ── Manager sign up ───────────────────────────────────────────────────────────

@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])          # stops scripted fake sign-ups
def user_signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("user/signup.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("user/signup.html")

        if BusinessManager.query.filter(func.lower(BusinessManager.email) == email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for("auth.user_login"))

        new_user = BusinessManager(email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        session["pending_manager_id"] = new_user.id
        flash("Account created. Complete payment to activate it.", "success")
        return redirect(url_for("payment.checkout"))

    return render_template("user/signup.html")


# ── Manager login ─────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;50 per hour", methods=["POST"], deduct_when=_failed_login)
def user_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = BusinessManager.query.filter(func.lower(BusinessManager.email) == email).first()

        if user and user.check_password(password):
            if not user.has_access:
                session["pending_manager_id"] = user.id
                if user.subscription_status == "unpaid":
                    flash("Complete your payment to activate your account.", "info")
                else:
                    flash("Your subscription has expired. Renew to continue.", "error")
                return redirect(url_for("payment.checkout"))

            session.pop("pending_manager_id", None)
            login_user(user)
            if user.subscription_status == "grace":
                flash(f"Your subscription is overdue. Renew within {user.grace_days_left} day(s) to keep access.", "warning")
            return redirect(url_for("manager.dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("user/login.html")

# ── Manager password reset ────────────────────────────────────────────────────

RESET_MAX_AGE = 3600  # seconds


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="manager-password-reset")


def _pw_fingerprint(user):
    # Changes whenever the password changes, so a used or old link stops working
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def _make_reset_token(user):
    return _reset_serializer().dumps({"id": user.id, "fp": _pw_fingerprint(user)})


def _user_from_reset_token(token):
    try:
        data = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except BadSignature:          # also covers expired tokens
        return None
    user = db.session.get(BusinessManager, data.get("id"))
    if not user or not hmac.compare_digest(str(data.get("fp", "")), _pw_fingerprint(user)):
        return None
    return user


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])                         # per IP
@limiter.limit("3 per hour", methods=["POST"],                          # per email: no inbox flooding
               key_func=lambda: request.form.get("email", "").strip().lower() or "blank")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = BusinessManager.query.filter(func.lower(BusinessManager.email) == email).first()

        if user:
            reset_url = current_app.config["APP_URL"] + url_for(
                "auth.reset_password", token=_make_reset_token(user))
            try:
                send_password_reset(user, reset_url)
            except Exception:
                current_app.logger.exception("Password reset email failed for %s", email)

        # Same message whether or not the account exists
        flash("If an account exists for that email, we've sent a reset link. It expires in 1 hour.", "info")
        return redirect(url_for("auth.user_login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    user = _user_from_reset_token(token)
    if not user:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            user.set_password(password)
            db.session.commit()
            flash("Your password has been updated. You can now log in.", "success")
            return redirect(url_for("auth.user_login"))

    return render_template("auth/reset_password.html", token=token)


# ── Staff login ───────────────────────────────────────────────────────────────

@auth_bp.route("/staff/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;50 per hour", methods=["POST"], deduct_when=_failed_login)
def staff_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Usernames are unique per manager, not globally, so several active staff
        # can share a username. Try each and log in the one whose password matches.
        candidates = Staff.query.filter_by(username=username, is_active=True).all()
        staff = next((s for s in candidates if s.check_password(password)), None)

        if staff:
            if not staff.manager.has_access:
                flash("Your manager has not subscribed. Please contact your manager.", "error")
                return render_template("auth/staff_login.html")
            login_user(staff)
            return redirect(url_for("staff.dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("auth/staff_login.html")


# ── Super admin login ─────────────────────────────────────────────────────────

@auth_bp.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("5 per minute;20 per hour", methods=["POST"], deduct_when=_failed_login)
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        admin = SuperAdmin.query.filter(func.lower(SuperAdmin.email) == email).first()
        if admin and admin.check_password(password):
            login_user(admin)
            return redirect(url_for("admin.dashboard"))

        flash("Invalid credentials.", "error")

    return render_template("admin/login.html")


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.landing"))