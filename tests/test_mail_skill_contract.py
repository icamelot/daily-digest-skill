"""Safety contract tests for the agent-facing outbound mail instructions."""
import unittest
from pathlib import Path


MAIL_SKILL = Path(__file__).resolve().parents[1] / "mail" / "SKILL.md"


class TestAttachmentConfirmationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MAIL_SKILL.read_text(encoding="utf-8")

    def test_draft_lists_attachment_names_sizes_and_total(self):
        self.assertIn("prepare_attachments", self.text)
        self.assertIn("附件名", self.text)
        self.assertIn("单个原始大小", self.text)
        self.assertIn("附件总原始大小", self.text)
        self.assertIn("KiB/MiB（二进制单位）", self.text)

    def test_explicit_confirmation_and_invalidation_are_mandatory(self):
        self.assertIn("明确肯定确认", self.text)
        self.assertIn("沉默、歧义回复或旧草稿确认均不算授权", self.text)
        self.assertIn("路径、附件名或大小", self.text)
        self.assertIn("重新展示草稿并再次确认", self.text)

    def test_cancel_failure_and_cleanup_failure_never_delete_or_resend(self):
        self.assertIn("取消时不得调用 send_email", self.text)
        self.assertIn("取消或发送失败不得删除任何附件", self.text)
        self.assertIn("邮件已发送、附件清理失败", self.text)
        self.assertIn("绝不重发", self.text)
