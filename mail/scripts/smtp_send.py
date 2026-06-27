"""Send emails via SMTP."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate


def send_email(
    config: dict,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> bool:
    """
    Send an email via SMTP. Returns True on success.
    Set in_reply_to and references for threaded replies.
    """
    smtp_cfg = config["mail"]["smtp"]

    msg = MIMEMultipart()
    msg["From"] = smtp_cfg["username"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if smtp_cfg["port"] == 465:
            conn = smtplib.SMTP_SSL(smtp_cfg["server"], smtp_cfg["port"])
        else:
            conn = smtplib.SMTP(smtp_cfg["server"], smtp_cfg["port"])
            conn.starttls()
        conn.login(smtp_cfg["username"], smtp_cfg["password"])
        conn.send_message(msg)
        conn.quit()
        return True
    except smtplib.SMTPException as e:
        print(f"SMTP send failed: {e}")
        return False
