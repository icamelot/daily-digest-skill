"""Unit tests for outbound SMTP message construction and delivery."""
import email
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "mail" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import smtp_send  # noqa: E402


SMTP_CFG = {
    "server": "smtp.example.com",
    "port": 587,
    "username": "sender@example.com",
    "password": "secret",
}


def attachment_parts(message):
    return [
        part for part in message.walk()
        if part.get_content_disposition() == "attachment"
    ]


class TestBuildEmailMessage(unittest.TestCase):
    def test_no_attachment_preserves_body_and_headers(self):
        message, prepared = smtp_send.build_email_message(
            SMTP_CFG,
            "to@example.com",
            "主题",
            "正文",
            in_reply_to="<parent@example.com>",
            references="<root@example.com> <parent@example.com>",
        )

        self.assertEqual(prepared, ())
        self.assertEqual(message["From"], "sender@example.com")
        self.assertEqual(message["To"], "to@example.com")
        self.assertEqual(str(message["Subject"]), "主题")
        self.assertEqual(message["In-Reply-To"], "<parent@example.com>")
        self.assertEqual(
            message["References"],
            "<root@example.com> <parent@example.com>",
        )
        text_parts = [part for part in message.walk() if part.get_content_type() == "text/plain"]
        self.assertEqual(text_parts[0].get_payload(decode=True).decode("utf-8"), "正文")
        self.assertEqual(attachment_parts(message), [])

    def test_single_string_path_builds_pdf_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(b"%PDF-test")

            message, prepared = smtp_send.build_email_message(
                SMTP_CFG, "to@example.com", "subject", "body", attachments=str(path)
            )

        parts = attachment_parts(message)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_content_type(), "application/pdf")
        self.assertEqual(parts[0].get_filename(), "paper.pdf")
        self.assertEqual(parts[0].get_payload(decode=True), b"%PDF-test")
        self.assertEqual(prepared[0].size, len(b"%PDF-test"))

    def test_pathlike_sequence_preserves_order_unknown_type_and_unicode_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "说明.txt"
            second = root / "数据.unknown-extension"
            first.write_text("你好", encoding="utf-8")
            second.write_bytes(b"raw")

            message, prepared = smtp_send.build_email_message(
                SMTP_CFG,
                "to@example.com",
                "subject",
                "body",
                attachments=[first, second],
            )
            reparsed = email.message_from_bytes(message.as_bytes())

        parts = attachment_parts(reparsed)
        self.assertEqual([part.get_filename() for part in parts], ["说明.txt", "数据.unknown-extension"])
        self.assertEqual([part.get_content_type() for part in parts], ["text/plain", "application/octet-stream"])
        self.assertEqual([part.get_payload(decode=True) for part in parts], ["你好".encode(), b"raw"])
        self.assertEqual([item.filename for item in prepared], ["说明.txt", "数据.unknown-extension"])

    def test_repeated_path_is_attached_and_counted_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "twice.bin"
            path.write_bytes(b"123")

            message, prepared = smtp_send.build_email_message(
                SMTP_CFG,
                "to@example.com",
                "subject",
                "body",
                attachments=[path, path],
                max_attachment_bytes=6,
            )

        self.assertEqual(len(attachment_parts(message)), 2)
        self.assertEqual([item.size for item in prepared], [3, 3])


class TestPrepareAttachments(unittest.TestCase):
    def assert_invalid(self, attachments, limit=smtp_send.DEFAULT_MAX_ATTACHMENT_BYTES):
        with self.assertRaises(smtp_send.AttachmentValidationError):
            smtp_send.prepare_attachments(attachments, limit)

    def test_none_empty_and_zero_byte_at_zero_limit(self):
        self.assertEqual(smtp_send.prepare_attachments(None), ())
        self.assertEqual(smtp_send.prepare_attachments([]), ())
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.bin"
            empty.write_bytes(b"")
            result = smtp_send.prepare_attachments(empty, 0)
        self.assertEqual(result[0].size, 0)

    def test_invalid_container_element_and_limits(self):
        self.assert_invalid(iter(["file.txt"]))
        self.assert_invalid([123])
        for invalid_limit in (-1, 1.5, True, "20"):
            with self.subTest(limit=invalid_limit):
                self.assert_invalid([], invalid_limit)

    def test_missing_directory_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("safe")
            link = root / "link.txt"
            link.symlink_to(target)
            self.assert_invalid(root / "missing.txt")
            self.assert_invalid(root)
            self.assert_invalid(link)

    def test_declared_aggregate_limit_counts_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "three.bin"
            path.write_bytes(b"123")
            self.assertEqual(len(smtp_send.prepare_attachments([path, path], 6)), 2)
            self.assert_invalid([path, path], 5)

    def test_unreadable_open_is_translated_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.txt"
            path.write_text("do-not-print")
            original_open = os.open

            def fail_target(candidate, *args, **kwargs):
                if Path(candidate).resolve() == path.resolve():
                    raise PermissionError("do-not-print")
                return original_open(candidate, *args, **kwargs)

            with patch.object(smtp_send.os, "open", side_effect=fail_target):
                with self.assertRaisesRegex(
                    smtp_send.AttachmentValidationError,
                    r"attachment is not readable: private\.txt",
                ) as caught:
                    smtp_send.prepare_attachments(path)
        self.assertNotIn("do-not-print", str(caught.exception))
        self.assertNotIn(str(path.parent), str(caught.exception))

    def test_identity_change_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "moving.bin"
            path.write_bytes(b"abc")
            real_fstat = os.fstat

            def changed_identity(fd):
                current = real_fstat(fd)
                values = list(current)
                values[1] = current.st_ino + 1
                return os.stat_result(values)

            with patch.object(smtp_send.os, "fstat", side_effect=changed_identity):
                self.assert_invalid(path)

    def test_growth_after_metadata_pass_is_checked_against_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "growing.bin"
            path.write_bytes(b"1234")
            real_lstat = Path.lstat

            def smaller_metadata(candidate):
                current = real_lstat(candidate)
                if candidate == path:
                    values = list(current)
                    values[6] = 2
                    return os.stat_result(values)
                return current

            with patch.object(Path, "lstat", autospec=True, side_effect=smaller_metadata):
                self.assert_invalid(path, 3)
