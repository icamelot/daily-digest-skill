"""Tests for filter_rules."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mail.scripts.filter_rules import classify_emails, _matches_keywords


class TestFilterRules(unittest.TestCase):

    def test_matches_keywords(self):
        self.assertEqual(_matches_keywords("Hello World", ["hello"]), ["hello"])
        self.assertEqual(_matches_keywords("Hello World", ["xyz"]), [])
        self.assertEqual(_matches_keywords("", ["test"]), [])
        self.assertEqual(_matches_keywords(None, ["test"]), [])

    def test_classify_junk_by_sender(self):
        config = {
            "mail": {
                "filters": {
                    "blacklist_senders": ["spam@spam.com"],
                    "blacklist_keywords": [],
                    "important_senders": [],
                    "important_keywords": [],
                }
            }
        }
        emails = [{"sender": "spam@spam.com", "subject": "Buy now", "body": "Cheap!"}]
        result = classify_emails(emails, config)
        self.assertEqual(len(result["junk"]), 1)
        self.assertEqual(len(result["important"]), 0)
        self.assertEqual(len(result["normal"]), 0)

    def test_classify_important_by_sender(self):
        config = {
            "mail": {
                "filters": {
                    "blacklist_senders": [],
                    "blacklist_keywords": [],
                    "important_senders": ["boss@company.com"],
                    "important_keywords": [],
                }
            }
        }
        emails = [{"sender": "boss@company.com", "subject": "Q3 review", "body": "Please review"}]
        result = classify_emails(emails, config)
        self.assertEqual(len(result["important"]), 1)

    def test_blacklist_overrides_important(self):
        config = {
            "mail": {
                "filters": {
                    "blacklist_senders": ["spam@spam.com"],
                    "blacklist_keywords": [],
                    "important_senders": ["spam@spam.com"],
                    "important_keywords": [],
                }
            }
        }
        emails = [{"sender": "spam@spam.com", "subject": "Urgent", "body": "..."}]
        result = classify_emails(emails, config)
        self.assertEqual(len(result["junk"]), 1)
        self.assertEqual(len(result["important"]), 0)

    def test_frequency_anomaly(self):
        config = {
            "mail": {
                "filters": {
                    "blacklist_senders": [],
                    "blacklist_keywords": [],
                    "important_senders": [],
                    "important_keywords": ["promo"],
                }
            }
        }
        emails = [
            {"sender": f"user{i}@example.com", "subject": f"promo deal {i}", "body": "promo"}
            for i in range(6)
        ]
        result = classify_emails(emails, config)
        self.assertGreater(len(result["anomalies"]), 0)
        self.assertIn("promo", result["anomalies"][0])

    def test_normal_email(self):
        config = {
            "mail": {
                "filters": {
                    "blacklist_senders": [],
                    "blacklist_keywords": [],
                    "important_senders": [],
                    "important_keywords": [],
                }
            }
        }
        emails = [{"sender": "friend@example.com", "subject": "Hello", "body": "How are you?"}]
        result = classify_emails(emails, config)
        self.assertEqual(len(result["normal"]), 1)


if __name__ == '__main__':
    unittest.main()
