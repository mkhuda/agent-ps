"""Removing what a session left behind, without removing the session.

Every test here builds its own store in a temporary directory. Nothing reads or
writes a real agent's files, because the one mistake this feature can make is
not recoverable.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures
from agent_ps.__main__ import parse_age
from agent_ps.backends import (ClaudeBackend, PiBackend, OpenCodeBackend,
                               HermesBackend, AntigravityBackend)

SESSION = "11111111-2222-3333-4444-555555555555"


def claude_store(root, session=SESSION, extras=True):
    """A Claude session with everything it writes alongside the transcript."""
    path = fixtures.claude(root, session)
    if not extras:
        return path
    for where, name, size in (
            (os.path.join(os.path.dirname(path), session), "sub.jsonl", 400),
            (os.path.join(root, "file-history", session), "a.txt", 300),
            (os.path.join(root, "tasks", session), "t.json", 100),
            (os.path.join(root, "session-env", session), "env", 50)):
        os.makedirs(where, exist_ok=True)
        with open(os.path.join(where, name), "w") as handle:
            handle.write("x" * size)
    return path


class Choosing(unittest.TestCase):
    """What each backend is willing to give up."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_claude_offers_everything_but_the_transcript(self):
        backend = ClaudeBackend()
        backend.root = self.root
        path = claude_store(self.root)
        labels = [label for label, _, _ in backend.prune_paths(SESSION, path)]
        self.assertEqual(sorted(labels),
                         ["file history", "session env", "subagents", "tasks"])
        self.assertNotIn("transcript", labels)

    def test_the_transcript_is_never_among_the_paths(self):
        backend = ClaudeBackend()
        backend.root = self.root
        path = claude_store(self.root)
        self.assertNotIn(path, [entry for _, entry, _ in
                                backend.prune_paths(SESSION, path)])

    def test_an_agent_that_writes_only_a_transcript_offers_nothing(self):
        backend = PiBackend()
        backend.root = self.root
        path = fixtures.pi(self.root, SESSION)
        self.assertEqual(backend.prune_paths(SESSION, path), [])

    def test_database_backends_offer_nothing_and_are_never_walked(self):
        # deleting a row means writing to a file the agent may have open, and
        # reading everything read only is worth more than a cleaner
        for cls in (OpenCodeBackend, HermesBackend, AntigravityBackend):
            with self.subTest(cls.name):
                backend = cls()
                backend.root = self.root
                if cls is HermesBackend:  # its dumps are files beside the db
                    self.assertEqual(backend.prune_paths(SESSION, ""), [])
                else:
                    self.assertEqual(backend.prunable, ())
                    self.assertEqual(backend.prune_paths(SESSION, ""), [])

    def test_hermes_finds_one_dump_per_turn(self):
        backend = HermesBackend()
        backend.root = self.root
        where = os.path.join(self.root, "sessions")
        os.makedirs(where)
        for turn in range(3):
            with open(os.path.join(where,
                                   f"request_dump_{SESSION}_{turn}.json"), "w") as h:
                h.write("x" * 500)
        found = backend.prune_paths(SESSION, "")
        self.assertEqual(len(found), 3)
        self.assertEqual(sum(size for _, _, size in found), 1500)

    def test_a_missing_directory_is_not_offered(self):
        backend = ClaudeBackend()
        backend.root = self.root
        path = claude_store(self.root, extras=False)
        self.assertEqual(backend.prune_paths(SESSION, path), [])


class Age(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_age("7d"), 7 * 86400)
        self.assertEqual(parse_age("36h"), 36 * 3600)
        self.assertEqual(parse_age("2w"), 14 * 86400)
        self.assertEqual(parse_age("30"), 30 * 86400)

    def test_nonsense_is_refused_rather_than_guessed(self):
        for bad in ("", "soon", "-1d", "d"):
            with self.subTest(bad):
                with self.assertRaises(Exception):
                    parse_age(bad)


class ThroughTheCommand(unittest.TestCase):
    """The whole path, including --apply, against a store built for it."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.root = os.path.join(self.home, ".claude")
        self.path = claude_store(self.root)
        self.subagents = os.path.join(os.path.dirname(self.path), SESSION)
        self.history = os.path.join(self.root, "file-history", SESSION)
        old = time.time() - 30 * 86400
        for entry in (self.path, self.subagents, self.history):
            os.utime(entry, (old, old))

    def run_prune(self, *extra):
        env = dict(os.environ, HOME=self.home, CLAUDE_CONFIG_DIR=self.root)
        env.pop("AGENT_PS_HOME", None)
        return subprocess.run(
            [sys.executable, "-m", "agent_ps", "--agent", "claude",
             "prune", "--older-than", "7d"] + list(extra),
            capture_output=True, text=True, env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_reporting_removes_nothing(self):
        done = self.run_prune()
        self.assertIn("Transcripts are untouched", done.stdout)
        self.assertTrue(os.path.exists(self.subagents), done.stdout)
        self.assertTrue(os.path.exists(self.history))
        self.assertTrue(os.path.exists(self.path))

    def test_apply_removes_the_parts_and_keeps_the_transcript(self):
        done = self.run_prune("--apply")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(self.subagents), done.stdout)
        self.assertFalse(os.path.exists(self.history))
        self.assertTrue(os.path.exists(self.path),
                        "the conversation must survive")
        with open(self.path) as handle:
            self.assertIn("a claude session", handle.read())

    def test_a_session_younger_than_the_age_is_left_alone(self):
        now = time.time()
        for entry in (self.path, self.subagents, self.history):
            os.utime(entry, (now, now))
        done = self.run_prune("--apply")
        self.assertIn("Nothing to remove", done.stdout)
        self.assertTrue(os.path.exists(self.subagents))

    def test_running_twice_is_harmless(self):
        self.run_prune("--apply")
        done = self.run_prune("--apply")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Nothing to remove", done.stdout)



class TheLiveGuard(unittest.TestCase):
    """A session with a process is never a candidate, whatever its age.

    This is the guarantee that matters most. File history backs /rewind, so
    taking it from a session someone is still working in destroys something
    they can still reach for, and no age threshold would have saved them.
    """

    class Fake:
        def __init__(self, rows, backend):
            self._rows, self._backend = rows, backend

        def rows(self, show_ended=False, limit=40):
            return self._rows

        def find_backend(self, name):
            return self._backend

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.backend = ClaudeBackend()
        self.backend.root = self.root
        self.path = claude_store(self.root)

    def row(self, **over):
        base = {"agent": "claude", "session_id": SESSION, "path": self.path,
                "pid": 0, "kind": "ended", "last_active": time.time() - 30 * 86400,
                "title": "old work", "name": "work"}
        base.update(over)
        return base

    def found(self, row):
        from agent_ps.__main__ import prunable_rows
        return prunable_rows(self.Fake([row], self.backend), 7 * 86400)

    def test_an_ended_session_is_a_candidate(self):
        self.assertEqual(len(self.found(self.row())), 1)

    def test_a_session_with_a_process_is_not(self):
        self.assertEqual(self.found(self.row(pid=4242, kind="session")), [])

    def test_not_even_when_it_is_ancient(self):
        old = self.row(pid=4242, kind="session", last_active=0)
        self.assertEqual(self.found(old), [])

    def test_a_row_with_no_session_is_not(self):
        self.assertEqual(self.found(self.row(session_id="")), [])

if __name__ == "__main__":
    unittest.main()
