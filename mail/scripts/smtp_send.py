"""Send emails via SMTP."""
import mimetypes
import os
import smtplib
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from email import encoders
from email.message import Message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path


DEFAULT_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
TELEGRAM_FILES_ROOT = Path("/ductor/workspace/telegram_files")

AttachmentPath = str | os.PathLike[str]
AttachmentInput = AttachmentPath | Sequence[AttachmentPath] | None


class AttachmentValidationError(ValueError):
    """The complete attachment set is unsafe or cannot be prepared."""


@dataclass(frozen=True)
class PreparedAttachment:
    path: Path
    filename: str
    size: int
    maintype: str
    subtype: str
    content: bytes
    device: int
    inode: int
    cleanup_eligible: bool


@dataclass(frozen=True)
class _AttachmentMetadata:
    path: Path
    filename: str
    declared_size: int
    device: int
    inode: int
    cleanup_eligible: bool


def _normalize_attachment_paths(attachments: AttachmentInput) -> tuple[Path, ...]:
    if attachments is None:
        return ()
    if isinstance(attachments, (str, os.PathLike)):
        values = (attachments,)
    elif isinstance(attachments, Sequence):
        values = tuple(attachments)
    else:
        raise AttachmentValidationError("attachments must be a path or a sequence of paths")
    if any(not isinstance(value, (str, os.PathLike)) for value in values):
        raise AttachmentValidationError("each attachment must be a path")
    return tuple(Path(value) for value in values)


def _mime_parts(filename: str) -> tuple[str, str]:
    guessed, _ = mimetypes.guess_type(filename)
    if not guessed or "/" not in guessed:
        return "application", "octet-stream"
    maintype, subtype = guessed.split("/", 1)
    return maintype, subtype


def _is_strictly_below(path: Path, root: Path) -> bool:
    return path != root and path.is_relative_to(root)


def prepare_attachments(
    attachments: AttachmentInput,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> tuple[PreparedAttachment, ...]:
    if (
        isinstance(max_attachment_bytes, bool)
        or not isinstance(max_attachment_bytes, int)
        or max_attachment_bytes < 0
    ):
        raise AttachmentValidationError("max_attachment_bytes must be a non-negative integer")

    cleanup_root = TELEGRAM_FILES_ROOT.resolve(strict=False)
    metadata = []
    declared_total = 0
    for source in _normalize_attachment_paths(attachments):
        try:
            file_stat = source.lstat()
        except OSError as exc:
            raise AttachmentValidationError(
                f"attachment does not exist: {source.name}"
            ) from exc
        if stat.S_ISLNK(file_stat.st_mode):
            raise AttachmentValidationError(
                f"attachment symbolic links are not allowed: {source.name}"
            )
        if not stat.S_ISREG(file_stat.st_mode):
            raise AttachmentValidationError(
                f"attachment is not a regular file: {source.name}"
            )
        try:
            canonical = source.resolve(strict=True)
        except OSError as exc:
            raise AttachmentValidationError(
                f"attachment does not exist: {source.name}"
            ) from exc
        declared_total += file_stat.st_size
        if declared_total > max_attachment_bytes:
            raise AttachmentValidationError(
                f"attachments exceed raw size limit of {max_attachment_bytes} bytes"
            )
        metadata.append(_AttachmentMetadata(
            path=canonical,
            filename=source.name,
            declared_size=file_stat.st_size,
            device=file_stat.st_dev,
            inode=file_stat.st_ino,
            cleanup_eligible=_is_strictly_below(canonical, cleanup_root),
        ))

    prepared = []
    actual_total = 0
    for item in metadata:
        descriptor = None
        try:
            descriptor = os.open(item.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source_file:
                descriptor = None
                before = os.fstat(source_file.fileno())
                content = source_file.read()
                after = os.fstat(source_file.fileno())
        except OSError as exc:
            raise AttachmentValidationError(
                f"attachment is not readable: {item.filename}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        expected_identity = (item.device, item.inode)
        if (
            (before.st_dev, before.st_ino) != expected_identity
            or (after.st_dev, after.st_ino) != expected_identity
        ):
            raise AttachmentValidationError(
                f"attachment changed while being read: {item.filename}"
            )
        actual_total += len(content)
        if actual_total > max_attachment_bytes:
            raise AttachmentValidationError(
                f"attachments exceed raw size limit of {max_attachment_bytes} bytes"
            )
        maintype, subtype = _mime_parts(item.filename)
        prepared.append(
            PreparedAttachment(
                path=item.path,
                filename=item.filename,
                size=len(content),
                maintype=maintype,
                subtype=subtype,
                content=content,
                device=item.device,
                inode=item.inode,
                cleanup_eligible=item.cleanup_eligible,
            )
        )
    return tuple(prepared)


def build_email_message(
    smtp_cfg: dict,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: AttachmentInput = None,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> tuple[Message, tuple[PreparedAttachment, ...]]:
    prepared = prepare_attachments(attachments, max_attachment_bytes)
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
    for item in prepared:
        part = MIMEBase(item.maintype, item.subtype)
        part.set_payload(item.content)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=item.filename)
        msg.attach(part)
    return msg, prepared


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
