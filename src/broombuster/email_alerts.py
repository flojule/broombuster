"""SMTP email-alert helper for the CLI.

The HTTP server does not send email — alerts are surfaced in the UI. This
module exists for `cli/main.py` so a long-running CLI can email its owner
when a sweep window is imminent.

Pure I/O. The plain-text message body is built elsewhere by the
appropriate domain plugin (`src/domains/sweeping.py.compose_message`).
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from broombuster import config


def send_email(message, urgency="today"):
    """Send a street-sweeping notification email using credentials from config/env."""
    if not config.EMAIL_SENDER or not config.EMAIL_PASSWORD:
        print("Email credentials not configured — skipping notification.")
        return

    msg = MIMEMultipart()
    msg["From"]    = config.EMAIL_SENDER
    msg["To"]      = config.EMAIL_RECEIVER or config.EMAIL_SENDER
    msg["Subject"] = "⚠ Street sweeping TODAY" if urgency == "today" else "Street sweeping tomorrow"
    msg.attach(MIMEText(message, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.send_message(msg)
        print("Notification email sent.")
    except Exception as e:
        print(f"Failed to send email: {e}")
