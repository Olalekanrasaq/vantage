import requests
from flask import current_app
from datetime import date


def _send(to: str, subject: str, html: str):
    """Base Mailgun send function."""
    response = requests.post(
        f"https://api.mailgun.net/v3/{current_app.config['MAILGUN_DOMAIN']}/messages",
        auth=("api", current_app.config["MAILGUN_API_KEY"]),
        data={
            "from": f"Vantage <{current_app.config['MAIL_FROM']}>",
            "to": to,
            "subject": subject,
            "html": html,
        },
    )
    response.raise_for_status()


def send_missing_report_alert(manager, report_date: date):
    _send(
        to=manager.email,
        subject=f"[Vantage] Report not received for {report_date}",
        html=f"""
        <p>Hi {manager.full_name or 'there'},</p>
        <p>Your report for <strong>{report_date}</strong> has not arrived in your Gmail yet.</p>
        <p>If you have the report file, please upload it manually on your
        <a href="{current_app.config.get('APP_URL', '')}/manager/upload">Vantage dashboard</a>.</p>
        <p>— The Vantage Team</p>
        """,
    )


def send_payment_confirmation(manager, amount: str, payment_type: str):
    _send(
        to=manager.email,
        subject="[Vantage] Payment confirmed",
        html=f"""
        <p>Hi {manager.full_name or 'there'},</p>
        <p>Your payment of <strong>{amount}</strong> ({payment_type}) has been confirmed.
        Your Vantage account is now active.</p>
        <p>— The Vantage Team</p>
        """,
    )


def send_subscription_expiry_warning(manager, expiry_date):
    _send(
        to=manager.email,
        subject="[Vantage] Your subscription expires soon",
        html=f"""
        <p>Hi {manager.full_name or 'there'},</p>
        <p>Your Vantage subscription expires on <strong>{expiry_date}</strong>.</p>
        <p>Please renew to avoid losing access.</p>
        <p>— The Vantage Team</p>
        """,
    )


def send_extraction_failure_alert(manager, report_date: date, error: str):
    """Notify both manager and super-admin of extraction failures."""
    _send(
        to=manager.email,
        subject=f"[Vantage] Report processing failed for {report_date}",
        html=f"""
        <p>Hi {manager.full_name or 'there'},</p>
        <p>We encountered an error processing your report for <strong>{report_date}</strong>.</p>
        <p>Our team has been notified. You may also try uploading the report manually.</p>
        <p>— The Vantage Team</p>
        """,
    )

    # Also alert super-admin
    admin_email = current_app.config.get("SUPER_ADMIN_EMAIL")
    if admin_email:
        _send(
            to=admin_email,
            subject=f"[Vantage Admin] Extraction failure — {manager.email}",
            html=f"""
            <p>Manager: {manager.email}</p>
            <p>Report date: {report_date}</p>
            <p>Error: <pre>{error}</pre></p>
            """,
        )
