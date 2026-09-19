"""No-network tests for Instagram comment-to-DM automation."""

import sqlite3
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from wingman.db.automation_repository import AutomationRepository
from wingman.db.video_catalog import Video, store_discovered_videos
from wingman.instagram.automation import deliver_comment, handle_opt_in
from wingman.instagram.client import PostedReply
from wingman.db.comment_store import connect_database
from wingman.web import create_app


class FakeClient:
    def __init__(self) -> None:
        self.messages = []
        self.public_replies = []
        self.fail_private = False

    def send_message(self, account_id, recipient, message):
        self.messages.append((account_id, recipient, message))
        if self.fail_private:
            raise RuntimeError("Meta rejected DM")
        return {"recipient_id": "ig-scoped-viewer", "message_id": f"msg-{len(self.messages)}"}

    def reply(self, comment_id, text):
        self.public_replies.append((comment_id, text))
        return PostedReply("public-1", text, "2026-09-19T00:00:00Z")

    def authenticated_account(self):
        return {"id": "creator", "user_id": "creator", "username": "creator"}

    def get(self, comment_id, **parameters):
        return {"id": comment_id, "text": "pcb please", "timestamp": datetime.now(timezone.utc).isoformat()}


class InstagramAutomationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        store_discovered_videos(self.db, [Video("reel", "Test Reel", "2026-09-19T00:00:00Z", "", platform="instagram")])
        self.repo = AutomationRepository(self.db)
        self.rule_id = self.repo.create(
            "reel", "PCB, pcb", "Check your DMs for my question.",
            mode="instagram_dm", initial_dm="Would you like the PCB guide?",
            followup_dm="Here is the guide: https://example.test/pcb",
        )
        self.rule = self.repo.get(self.rule_id)
        self.client = FakeClient()
        self.comment = {
            "comment_id": "comment-1", "text": "pcb please", "author_channel_id": "viewer",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }

    def tearDown(self):
        self.db.close()

    def test_private_first_then_public_and_deduplicated(self):
        self.assertEqual(deliver_comment(self.repo, self.client, "creator", self.rule, self.comment), "sent")
        self.assertEqual(self.client.messages[0][1], {"comment_id": "comment-1"})
        self.assertEqual(self.client.messages[0][2]["quick_replies"][0]["title"], "Yes please")
        self.assertEqual(len(self.client.public_replies), 1)
        self.assertEqual(deliver_comment(self.repo, self.client, "creator", self.rule, self.comment), "skipped")
        self.assertEqual(len(self.client.messages), 1)

    def test_no_public_reply_when_private_fails_and_no_retry(self):
        self.client.fail_private = True
        self.assertEqual(deliver_comment(self.repo, self.client, "creator", self.rule, self.comment), "failed")
        self.assertEqual(self.client.public_replies, [])
        self.assertEqual(deliver_comment(self.repo, self.client, "creator", self.rule, self.comment), "skipped")

    def test_button_sends_one_followup_only_to_original_recipient(self):
        deliver_comment(self.repo, self.client, "creator", self.rule, self.comment)
        self.assertFalse(handle_opt_in(self.repo, self.client, "creator", "other", "wingman_yes:comment-1"))
        self.assertTrue(handle_opt_in(self.repo, self.client, "creator", "ig-scoped-viewer", "wingman_yes:comment-1"))
        self.assertEqual(self.client.messages[1][2]["text"], "Here is the guide: https://example.test/pcb")
        self.assertFalse(handle_opt_in(self.repo, self.client, "creator", "ig-scoped-viewer", "wingman_yes:comment-1"))
        self.assertEqual(len(self.client.messages), 2)

    def test_old_prefill_rules_remain_prefill(self):
        self.repo.create("reel", "PCB", "Manual reply")
        self.assertEqual(self.repo.match("reel", "PCB please").default_reply, "Manual reply")

    def test_exact_match_is_case_sensitive_and_whole_comment(self):
        exact_id = self.repo.create(
            "reel", "wingman", "Check your DMs", mode="instagram_dm",
            initial_dm="Want it?", followup_dm="Test link", match_type="exact",
        )
        exact = self.repo.get(exact_id)
        self.assertEqual(self.repo.match_delivery(exact, " wingman "), "wingman")
        self.assertIsNone(self.repo.match_delivery(exact, "Wingman"))
        self.assertIsNone(self.repo.match_delivery(exact, "wingman please"))


class InstagramWebhookTests(unittest.TestCase):
    def test_verification_requires_configured_matching_token(self):
        app = create_app({"TESTING": True})
        with patch.dict(os.environ, {"INSTAGRAM_WEBHOOK_VERIFY_TOKEN": "test-verify"}):
            client = app.test_client()
            path = "/webhooks/instagram?hub.mode=subscribe&hub.challenge=challenge-123"
            self.assertEqual(client.get(path + "&hub.verify_token=wrong").status_code, 403)
            response = client.get(path + "&hub.verify_token=test-verify")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_data(as_text=True), "challenge-123")

    def test_signed_comment_event_starts_flow_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.db")
            db = connect_database(path)
            store_discovered_videos(db, [Video("reel", "Test", "2026-09-19T00:00:00Z", "", platform="instagram")])
            repo = AutomationRepository(db)
            repo.create("reel", "pcb", "Check your DMs", mode="instagram_dm", initial_dm="Want it?", followup_dm="https://example.test")
            db.close()
            fake = FakeClient()
            app = create_app({"TESTING": True, "DATABASE": path, "INSTAGRAM_CLIENT": fake})
            body = json.dumps({"entry": [{"changes": [{"field": "comments", "value": {"id": "new-comment", "text": "PCB please", "media": {"id": "reel"}, "from": {"id": "viewer"}}}]}]}).encode()
            signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
            with patch.dict(os.environ, {"META_APP_SECRET": "test-secret", "INSTAGRAM_ACCOUNT_ID": "creator"}):
                client = app.test_client()
                for _ in range(2):
                    self.assertEqual(client.post("/webhooks/instagram", data=body, content_type="application/json", headers={"X-Hub-Signature-256": signature}).status_code, 200)
            self.assertEqual(len(fake.messages), 1)
            self.assertEqual(len(fake.public_replies), 1)

    def test_signed_button_event_sends_followup_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.db")
            db = connect_database(path)
            store_discovered_videos(db, [Video("reel", "Test", "2026-09-19T00:00:00Z", "", platform="instagram")])
            repo = AutomationRepository(db)
            rule_id = repo.create("reel", "pcb", "Check your DMs", mode="instagram_dm", initial_dm="Want it?", followup_dm="https://example.test")
            fake = FakeClient()
            comment = {"comment_id": "c-1", "text": "pcb", "author_channel_id": "viewer", "published_at": datetime.now(timezone.utc).isoformat()}
            self.assertEqual(deliver_comment(repo, fake, "creator", repo.get(rule_id), comment), "sent")
            db.close()
            app = create_app({"TESTING": True, "DATABASE": path, "INSTAGRAM_CLIENT": fake})
            body = json.dumps({"entry": [{"messaging": [{"sender": {"id": "ig-scoped-viewer"}, "message": {"quick_reply": {"payload": "wingman_yes:c-1"}}}]}]}).encode()
            signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
            with patch.dict(os.environ, {"META_APP_SECRET": "test-secret", "INSTAGRAM_ACCOUNT_ID": "creator"}):
                client = app.test_client()
                self.assertEqual(client.post("/webhooks/instagram", data=body, content_type="application/json").status_code, 403)
                self.assertEqual(client.post("/webhooks/instagram", data=body, content_type="application/json", headers={"X-Hub-Signature-256": signature}).status_code, 200)
                self.assertEqual(client.post("/webhooks/instagram", data=body, content_type="application/json", headers={"X-Hub-Signature-256": signature}).status_code, 200)
            self.assertEqual(len(fake.messages), 2)


if __name__ == "__main__":
    unittest.main()
