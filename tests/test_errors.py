"""The last API or network failure a session hit, if it never recovered.

Every check here rests on one measured fact: in real transcripts, a session
that ever logs an API error almost always keeps going and succeeds afterward.
So the only trustworthy signal is whether the *newest* turn failed, not
whether one ever did, and every fixture below tests exactly that boundary.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_ps.backends import ClaudeBackend, CodexBackend, OpenCodeBackend

SESSION = "11111111-2222-3333-4444-555555555555"


def write(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


class ClaudeErrors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.backend = ClaudeBackend()
        self.backend.root = self.root

    def path(self, *turns):
        return write(os.path.join(self.root, "projects", "-tmp-work",
                                  SESSION + ".jsonl"),
                    [{"type": "attachment", "cwd": "/tmp/work"}] + list(turns))

    def turn(self, error=None, sidechain=False, model="claude-sonnet-5"):
        entry = {"type": "assistant", "isSidechain": sidechain,
                 "message": {"model": model, "content": [{"type": "text", "text": "hi"}]}}
        if error:
            entry["isApiErrorMessage"] = True
            entry["error"] = error
            entry["message"]["model"] = "<synthetic>"
            entry["message"]["content"] = [{"type": "text", "text": f"API Error: {error}"}]
        return entry

    def test_the_newest_turn_failing_is_reported(self):
        row = self.backend.describe(self.path(self.turn(), self.turn(error="rate_limit")))
        self.assertEqual(row["error_code"], "rate_limit")
        self.assertIn("rate_limit", row["error_text"])

    def test_a_turn_that_succeeded_afterward_clears_it(self):
        row = self.backend.describe(self.path(
            self.turn(error="server_error"), self.turn()))
        self.assertEqual(row["error_code"], "")

    def test_a_subagent_error_is_not_the_session_state(self):
        # a sidechain is a Task tool's own subagent, not the main conversation
        row = self.backend.describe(self.path(
            self.turn(), self.turn(error="rate_limit", sidechain=True)))
        self.assertEqual(row["error_code"], "")

    def test_no_error_at_all_is_the_common_case(self):
        row = self.backend.describe(self.path(self.turn(), self.turn()))
        self.assertEqual(row["error_code"], "")


class CodexErrors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.backend = CodexBackend()
        self.backend.root = self.root

    def path(self, *events):
        name = "rollout-2026-09-05T00-00-00-" + SESSION + ".jsonl"
        return write(os.path.join(self.root, "sessions", "2026", "09", "05", name),
                    [{"type": "session_meta", "payload": {"cwd": "/tmp/work"}}]
                    + list(events))

    def test_a_standalone_error_immediately_before_task_complete(self):
        # codex writes these two back to back with nothing in between
        row = self.backend.describe(self.path(
            {"type": "event_msg", "payload": {"type": "error",
             "message": "usage limit hit", "codex_error_info": "usage_limit_exceeded"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}}))
        self.assertEqual(row["error_code"], "usage_limit_exceeded")
        self.assertEqual(row["error_text"], "usage limit hit")
        self.assertEqual(row["status"], "idle")

    def test_an_error_nested_inside_task_complete(self):
        row = self.backend.describe(self.path(
            {"type": "event_msg", "payload": {"type": "task_complete",
             "last_agent_message": None,
             "error": {"message": "token expired", "codex_error_info": "unauthorized"}}}))
        self.assertEqual(row["error_code"], "unauthorized")
        self.assertEqual(row["error_text"], "token expired")

    def test_a_clean_task_complete_reports_nothing(self):
        row = self.backend.describe(self.path(
            {"type": "event_msg", "payload": {"type": "task_complete"}}))
        self.assertEqual(row["error_code"], "")
        self.assertEqual(row["status"], "idle")

    def test_a_turn_in_progress_is_busy_not_errored(self):
        # even if an older, already superseded error sits earlier in the file
        row = self.backend.describe(self.path(
            {"type": "event_msg", "payload": {"type": "error",
             "message": "old", "codex_error_info": "server_error"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
            {"type": "event_msg", "payload": {"type": "task_started"}}))
        self.assertEqual(row["status"], "busy")
        self.assertEqual(row["error_code"], "")


class OpenCodeErrors(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.db_path = os.path.join(self.root, "opencode.db")
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript("""
            CREATE TABLE session (id TEXT, directory TEXT, title TEXT, model TEXT,
                                  time_updated INTEGER);
            CREATE TABLE message (id TEXT, session_id TEXT, data TEXT,
                                  time_created INTEGER);
            CREATE TABLE part (id TEXT, session_id TEXT, data TEXT);
        """)
        self.db.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                        (SESSION, "/tmp/work", "a session", "{}", 1))
        self.db.commit()
        self.addCleanup(self.db.close)
        self.backend = OpenCodeBackend()
        self.backend.root = self.root

    def message(self, order, data):
        self.db.execute("INSERT INTO message VALUES (?,?,?,?)",
                        (f"m{order}", SESSION, json.dumps(data), order))
        self.db.commit()

    def row(self):
        return self.backend.sessions()[0]

    def test_the_newest_message_carrying_an_error_is_reported(self):
        self.message(1, {"role": "assistant", "finish": "stop"})
        self.message(2, {"error": {"name": "APIError",
                                   "data": {"message": "Not Found", "statusCode": 404}}})
        row = self.row()
        self.assertEqual(row["error_code"], "APIError")
        self.assertEqual(row["error_text"], "Not Found")

    def test_a_successful_reply_after_the_error_clears_it(self):
        self.message(1, {"error": {"name": "APIError", "data": {"message": "Not Found"}}})
        self.message(2, {"role": "assistant", "finish": "stop"})
        self.assertEqual(self.row()["error_code"], "")

    def test_an_aborted_message_is_still_reported_as_something(self):
        # not an API failure, but still an honest answer to "why did this stop"
        self.message(1, {"error": {"name": "MessageAbortedError",
                                   "data": {"message": "Aborted"}}})
        row = self.row()
        self.assertEqual(row["error_code"], "MessageAbortedError")

    def test_no_messages_at_all_reports_nothing(self):
        self.assertEqual(self.row()["error_code"], "")


if __name__ == "__main__":
    unittest.main()
