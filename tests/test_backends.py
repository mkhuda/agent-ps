"""Every backend, against the smallest store its agent would write.

These are the tests that notice when an agent changes its format, which is the
failure this project is most exposed to: the formats are nobody's public
interface and they move without warning.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures
from agent_ps.backends import (ClaudeBackend, PiBackend, CommandCodeBackend,
                               CodexBackend, OpenCodeBackend, HermesBackend,
                               CopilotBackend, AntigravityBackend)

SESSION = "11111111-2222-3333-4444-555555555555"

CASES = [
    (ClaudeBackend, fixtures.claude, "a claude session", "claude-sonnet-5"),
    (PiBackend, fixtures.pi, "a pi prompt", "9router/mimo"),
    (CommandCodeBackend, fixtures.commandcode, "a cmd prompt", "poolside/laguna"),
    (CodexBackend, fixtures.codex, "a codex prompt", "gpt-5.6-terra"),
    (OpenCodeBackend, fixtures.opencode, "an opencode session", "9router/mimo"),
    (HermesBackend, fixtures.hermes, "a hermes session", "nemotron-3.5"),
    (CopilotBackend, fixtures.copilot, "a copilot chat", "claude-fable-5.1"),
    (AntigravityBackend, fixtures.antigravity, "an agy prompt", "gemini-3.6-flash-medium"),
]


class Reading(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def rows_for(self, cls, build):
        # each backend gets its own root, as they do in life. Claude Code and
        # CommandCode share a layout, so a shared root would let one glob find
        # the other's sessions.
        root = os.path.join(self.root, cls.name)
        backend = cls()
        backend.root = root
        build(root, SESSION)
        return backend, backend.sessions()

    def test_every_backend_finds_its_session(self):
        for cls, build, title, model in CASES:
            with self.subTest(agent=cls.name):
                backend, rows = self.rows_for(cls, build)
                self.assertEqual(len(rows), 1, "one session was written")
                row = rows[0]
                self.assertEqual(row["agent"], cls.name)
                self.assertEqual(row["cwd"], "/tmp/work")
                self.assertEqual(row["name"], "work")
                self.assertEqual(row["title"], title)
                self.assertEqual(row["model"], model)
                self.assertGreater(row["last_active"], 0)

    def test_a_missing_store_is_no_sessions_rather_than_an_error(self):
        for cls, _, _, _ in CASES:
            with self.subTest(agent=cls.name):
                backend = cls()
                backend.root = os.path.join(self.root, "nothing-here")
                self.assertFalse(backend.available())
                self.assertEqual(backend.sessions(), [])

    def test_disk_breakdown_adds_up_to_the_column(self):
        for cls, build, _, _ in CASES:
            with self.subTest(agent=cls.name):
                backend, rows = self.rows_for(cls, build)
                parts = backend.disk_breakdown(rows[0]["session_id"],
                                               rows[0].get("path", ""))
                self.assertEqual(sum(size for _, size in parts), rows[0]["disk"])
                for label, _ in parts:
                    self.assertTrue(label, "every part is named")


class ClaudeDetail(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_synthetic_marks_are_not_a_model(self):
        """Claude Code writes its own notices with a <synthetic> model."""
        backend = ClaudeBackend()
        backend.root = self.root
        fixtures.claude(self.root, SESSION)
        self.assertEqual(backend.sessions()[0]["model"], "claude-sonnet-5")


class AntigravityDetail(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_the_model_is_not_taken_from_what_the_session_was_reading(self):
        """The large blob holds the repository's own text, which mentions models."""
        backend = AntigravityBackend()
        backend.root = self.root
        fixtures.antigravity(self.root, SESSION)
        self.assertEqual(backend.sessions()[0]["model"], "gemini-3.6-flash-medium")

    def test_the_workspace_stops_where_protobuf_says_it_does(self):
        """A regex would carry the next field's tag into the path."""
        backend = AntigravityBackend()
        backend.root = self.root
        fixtures.antigravity(self.root, SESSION)
        self.assertEqual(backend.sessions()[0]["cwd"], "/tmp/work")


if __name__ == "__main__":
    unittest.main()


#: What each fixture spends, as the panel should report it. Written out rather
#: than computed, so a change in the summing is a failure here rather than a
#: matching change in two places.
SPEND = {
    "claude": [("tokens", "in 17  out 5"), ("cache", "read 100  write 5")],
    "pi": [("tokens", "in 13  out 4"), ("cache", "read 50"), ("cost", "$0.7500")],
    "commandcode": [("tokens", "in 12  out 4"), ("cache", "read 60"),
                    ("cost", "$1.5000")],
    # a running total, so the newest entry is the answer and the earlier one
    # must not be added to it
    "codex": [("tokens", "in 250  out 25  reasoning 4"), ("cache", "read 200")],
}


class Spend(unittest.TestCase):
    """Token counts, for the agents that write them into their logs."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def backend_for(self, cls, build):
        root = os.path.join(self.root, cls.name)
        backend = cls()
        backend.root = root
        build(root, SESSION)
        return backend, backend.sessions()[0]

    def test_each_agent_reports_what_it_spent(self):
        for cls, build, _, _ in CASES:
            if cls.name not in SPEND:
                continue
            with self.subTest(cls.name):
                backend, row = self.backend_for(cls, build)
                self.assertEqual(backend.details(row), SPEND[cls.name])

    def test_an_agent_that_records_nothing_says_nothing(self):
        backend = ClaudeBackend()
        self.assertEqual(backend.details({"path": "/nowhere/at/all.jsonl"}), [])

    def test_a_growing_log_adds_rather_than_rescans(self):
        backend, row = self.backend_for(ClaudeBackend, fixtures.claude)
        self.assertEqual(backend.token_totals(row["path"])["input"], 17)
        with open(row["path"], "a") as handle:
            handle.write('{"message": {"usage": {"input_tokens": 6}}}\n')
        self.assertEqual(backend.token_totals(row["path"])["input"], 23)

    def test_half_a_turn_is_not_counted_until_it_is_whole(self):
        backend, row = self.backend_for(ClaudeBackend, fixtures.claude)
        backend.token_totals(row["path"])
        with open(row["path"], "a") as handle:
            handle.write('{"message": {"usage": {"input_')
        self.assertEqual(backend.token_totals(row["path"])["input"], 17)
        with open(row["path"], "a") as handle:
            handle.write('tokens": 6}}}\n')
        self.assertEqual(backend.token_totals(row["path"])["input"], 23)

    def test_a_rewritten_log_starts_over(self):
        backend, row = self.backend_for(ClaudeBackend, fixtures.claude)
        self.assertEqual(backend.token_totals(row["path"])["input"], 17)
        with open(row["path"], "w") as handle:
            handle.write('{"message": {"usage": {"input_tokens": 2}}}\n')
        self.assertEqual(backend.token_totals(row["path"])["input"], 2)
