import unittest

from routes.emails import _clean_ai_email_draft


class CleanAiEmailDraftTests(unittest.TestCase):
    def test_removes_common_model_framing(self):
        self.assertEqual(
            _clean_ai_email_draft(
                "Sure, here's a friendly response:\n\nHi Jane,\n\nThanks for your email."
            ),
            "Hi Jane,\n\nThanks for your email.",
        )

    def test_removes_markdown_fence_and_label(self):
        self.assertEqual(
            _clean_ai_email_draft(
                "```text\nDraft:\nHi Jane,\n\nI will check this today.\n```"
            ),
            "Hi Jane,\n\nI will check this today.",
        )

    def test_preserves_ready_to_send_body(self):
        body = "Hi Jane,\n\nThank you for sending this over."
        self.assertEqual(_clean_ai_email_draft(body), body)


if __name__ == "__main__":
    unittest.main()
