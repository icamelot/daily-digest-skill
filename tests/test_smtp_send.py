"""Unit tests for outbound SMTP message construction and delivery."""
import email
import os
import sys
import tempfile
import unittest
from pathlib import Path

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
