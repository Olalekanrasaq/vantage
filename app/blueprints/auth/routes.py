from flask import render_template, redirect, url_for, request, session, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.blueprints.auth import auth_bp
from app.extensions import db
from app.models import BusinessManager, Staff, SuperAdmin
from app.services.gmail_service import get_auth_url, exchange_code_for_credentials, get_google_user_info, credentials_to_json
import json


# ── Landing page ──────────────────────────────────────────────────────────────

@auth_bp.route("/")
def landing():
    return render_template("auth/landing.html")

# manager sign up if not already existing

@auth_bp.route("/signup", methods=["GET", "POST"])
def user_signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        # Check if user already exists
        if BusinessManager.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for("auth.user_login"))

        if email and password:
            new_user = BusinessManager(email=email)
            new_user.set_password(password) 
            
            db.session.add(new_user)
            db.session.commit()
            
            login_user(new_user)
            flash("Account created successfully!", "success")
            return redirect(url_for("manager.dashboard"))
        else:
            flash("Email and password are required.", "error")

    return render_template("user/signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = BusinessManager.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("manager.dashboard"))
        else:
            flash("Invalid email or password.", "error")

    return render_template("user/login.html")

# ── Staff login ───────────────────────────────────────────────────────────────

@auth_bp.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Staff usernames are unique per manager, but we need the manager context.
        # Staff logs in with username only — we find them across all managers.
        # If two managers have a staff with the same username, we match on password.
        staff = Staff.query.filter_by(username=username, is_active=True).first()

        if staff and staff.check_password(password):
            login_user(staff)
            return redirect(url_for("staff.dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("auth/staff_login.html")


# ── Super admin login ─────────────────────────────────────────────────────────

@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        admin = SuperAdmin.query.filter_by(email=email).first()
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
