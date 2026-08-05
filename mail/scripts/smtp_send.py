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


class MailConfigurationError(ValueError):
    """The SMTP account configuration is missing or malformed."""


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
    ctime_ns: int
    cleanup_eligible: bool


@dataclass(frozen=True)
class _AttachmentMetadata:
    path: Path
    filename: str
    declared_size: int
    device: int
    inode: int
    ctime_ns: int
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
            ctime_ns=file_stat.st_ctime_ns,
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
        expected_identity = (item.device, item.inode, item.ctime_ns)
        if (
            (before.st_dev, before.st_ino, before.st_ctime_ns) != expected_identity
            or (after.st_dev, after.st_ino, after.st_ctime_ns) != expected_identity
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
                ctime_ns=item.ctime_ns,
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


def _select_smtp_config(config: dict, from_account_label: str | None) -> dict:
    try:
        accounts = config.get("mail", {}).get("accounts", [])
        if accounts:
            if from_account_label:
                for account in accounts:
                    if account.get("label") == from_account_label:
                        smtp_cfg = account["smtp"]
                        break
                else:
                    smtp_cfg = accounts[0]["smtp"]
            else:
                smtp_cfg = accounts[0]["smtp"]
        else:
            smtp_cfg = config["mail"]["smtp"]
        if any(key not in smtp_cfg for key in ("server", "port", "username", "password")):
            raise KeyError("incomplete SMTP config")
        return smtp_cfg
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise MailConfigurationError("SMTP account configuration is missing or malformed") from exc


def _cleanup_sent_attachments(
    prepared: tuple[PreparedAttachment, ...],
) -> tuple[Path, ...]:
    cleanup_root = TELEGRAM_FILES_ROOT.resolve(strict=False)
    failures = []
    seen = set()
    for item in prepared:
        if item.path in seen:
            continue
        seen.add(item.path)
        if not item.cleanup_eligible:
            continue
        try:
            current = item.path.lstat()
            canonical = item.path.resolve(strict=True)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or not _is_strictly_below(canonical, cleanup_root)
                or (current.st_dev, current.st_ino, current.st_ctime_ns)
                != (item.device, item.inode, item.ctime_ns)
            ):
                failures.append(item.path)
                continue
            canonical.unlink()
        except OSError:
            failures.append(item.path)
    return tuple(failures)


def send_email(
    config: dict,
    to: str,
    subject: str,
    body: str,
    from_account_label: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: AttachmentInput = None,
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> bool:
    """Send one message; True means send_message() returned normally."""
    try:
        smtp_cfg = _select_smtp_config(config, from_account_label)
        message, prepared = build_email_message(
            smtp_cfg,
            to,
            subject,
            body,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
            max_attachment_bytes=max_attachment_bytes,
        )
    except (AttachmentValidationError, MailConfigurationError, KeyError) as exc:
        print(f"Email preparation failed: {exc}")
        return False

    connection = None
    accepted = False
    send_error = None
    try:
        if smtp_cfg["port"] == 465:
            connection = smtplib.SMTP_SSL(smtp_cfg["server"], smtp_cfg["port"])
        else:
            connection = smtplib.SMTP(smtp_cfg["server"], smtp_cfg["port"])
            connection.starttls()
        connection.login(smtp_cfg["username"], smtp_cfg["password"])
        connection.send_message(message)
        accepted = True
    except (OSError, smtplib.SMTPException) as exc:
        send_error = exc
    finally:
        if connection is not None:
            try:
                connection.quit()
            except (OSError, smtplib.SMTPException) as exc:
                if accepted:
                    print(f"Email accepted; SMTP teardown failed: {exc}")

    if not accepted:
        print(f"SMTP send failed: {send_error}")
        return False
    cleanup_failures = _cleanup_sent_attachments(prepared)
    if cleanup_failures:
        paths = ", ".join(str(path) for path in cleanup_failures)
        print(f"Email sent; attachment cleanup failed: {paths}; do not resend")
    return True
