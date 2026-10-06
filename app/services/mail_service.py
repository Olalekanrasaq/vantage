import requests
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from flask import current_app
from datetime import date


# def _send(to: str, subject: str, html: str):
#     """Base Mailgun send function."""
#     response = requests.post(
#         f"https://api.mailgun.net/v3/{current_app.config['MAILGUN_DOMAIN']}/messages",
#         auth=("api", current_app.config["MAILGUN_API_KEY"]),
#         data={
#             "from": f"Vantage <{current_app.config['MAIL_FROM']}>",
#             "to": to,
#             "subject": subject,
#             "html": html,
#         },
#         timeout=15,
#     )
#     response.raise_for_status()


def _send(to: str, subject: str, html: str):
    """Send an HTML email through Gmail SMTP (STARTTLS)."""
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Vantage", cfg["MAIL_FROM"]))
    msg["To"] = to
    msg.set_content("This message needs an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
        server.send_message(msg)

def send_missing_report_alert(manager, report_date: date):
    _send(
        to=manager.email,
        subject=f"[Vantage] Report not uploaded for {report_date}",
        html=f"""
        <p>Hi there,</p>
        <p>Your report for <strong>{report_date}</strong> has not been uploaded yet.</p>
        <p>If you have the report file, please upload it manually on your
        <a href="{current_app.config.get('APP_URL', '')}/manager/upload">Vantage dashboard</a>.</p>
        <p>The Vantage Team</p>
        """,
    )


def send_payment_confirmation(manager, amount: str, payment_type: str):
    _send(
        to=manager.email,
        subject="[Vantage] Payment confirmed",
        html=f"""
        <p>Hi there,</p>
        <p>Your payment of <strong>{amount}</strong> for Vantage Subscription has been confirmed.
        Your Vantage account is now active.</p>
        <p>The Vantage Team</p>
        """,
    )


def send_extraction_failure_alert(manager, report_date: date, error: str):
    """Super admin only. Managers are not notified."""
    admin_email = current_app.config.get("SUPER_ADMIN_EMAIL")
    if not admin_email:
        return
    _send(
        to=admin_email,
        subject=f"[Vantage Admin] Extraction failure: {manager.email}",
        html=f"""
        <p>Manager: {manager.email}</p>
        <p>Report date: {report_date}</p>
        <p>Details: <pre>{error}</pre></p>
        """,
    )

def send_grace_period_reminder(manager, due_date, grace_ends, days_left: int):
    _send(
        to=manager.email,
        subject=f"[Vantage] Payment overdue: {days_left} day(s) left to renew",
        html=f"""
        <p>Hi there,</p>
        <p>Your Vantage subscription was due on <strong>{due_date:%d %b %Y}</strong>.
        You are in a grace period and still have access until
        <strong>{grace_ends:%d %b %Y}</strong> ({days_left} day(s) left).</p>
        <p>Renew now at <a href="{current_app.config.get('APP_URL', '')}/payment/subscribe">your payment page</a>.
        Your next due date stays the same whether you pay today or on the last day of grace.</p>
        <p>The Vantage Team</p>
        """,
    )