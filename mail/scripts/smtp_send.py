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
    from_account_label: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> bool:
    """
    Send an email via SMTP. Returns True on success.
    from_account_label: match against account labels in config.
    If None, uses the first account.
    Set in_reply_to and references for threaded replies.
    """
    accounts = config.get("mail", {}).get("accounts", [])
    if accounts:
        smtp_cfg = None
        if from_account_label:
            for acc in accounts:
                if acc.get("label") == from_account_label:
                    smtp_cfg = acc["smtp"]
                    break
        if smtp_cfg is None:
            smtp_cfg = accounts[0]["smtp"]
    else:
        # Fallback: old single-account format
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
