"""The TUI against terminals it was not designed for.

These run the real program under a pseudo terminal, because the failures they
catch are ones no unit test sees: a window that shrinks after the layout was
computed, and a terminfo that cannot hide the cursor.
"""

import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

try:
    import fcntl
    import pty
    import termios
    HAVE_PTY = True
except ImportError:  # pragma: no cover - Windows has no pty
    HAVE_PTY = False


@unittest.skipUnless(HAVE_PTY, "needs a pseudo terminal")
class UnderAPseudoTerminal(unittest.TestCase):
    def setUp(self):
        # an empty home, not the real one: the first poll otherwise scans
        # whatever sessions actually exist on this machine, which is slow and
        # unpredictable, and is exactly what made this suite flaky
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def run_tui(self, rows, cols, shrink_to=None, term="xterm-256color"):
        """Start the TUI, optionally resize it, quit, and return its output."""
        main, sub = pty.openpty()
        fcntl.ioctl(sub, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        child = subprocess.Popen(
            [sys.executable, "-m", "agent_ps"], stdin=sub, stdout=sub, stderr=sub,
            cwd=ROOT, env=dict(os.environ, TERM=term, HOME=self.home))
        os.close(sub)

        def pump(seconds):
            """Read whatever arrives, for at most this long."""
            got, until = b"", time.time() + seconds
            while time.time() < until and child.poll() is None:
                ready, _, _ = select.select([main], [], [], 0.05)
                if not ready:
                    continue
                try:
                    chunk = os.read(main, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                got += chunk
                until = min(until, time.time() + 0.3)  # quiet means it settled
            return got

        out = pump(4.0)
        if shrink_to and child.poll() is None:
            fcntl.ioctl(main, termios.TIOCSWINSZ,
                        struct.pack("HHHH", shrink_to[0], shrink_to[1], 0, 0))
            child.send_signal(signal.SIGWINCH)
            out += pump(2.0)
        if child.poll() is None:
            try:
                os.write(main, b"q")
            except OSError:
                pass
            out += pump(1.0)
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        try:
            while True:
                chunk = os.read(main, 65536)
                if not chunk:
                    break
                out += chunk
        except OSError:
            pass
        os.close(main)
        return ESCAPES.sub("", out.decode("utf8", "ignore"))

    def assertNoCrash(self, text, what):
        self.assertNotIn("Traceback", text, f"{what} crashed:\n{text[-600:]}")
        self.assertNotIn("curses.error", text, f"{what} crashed:\n{text[-600:]}")

    def test_a_normal_terminal(self):
        self.assertNoCrash(self.run_tui(24, 120), "24x120")

    def test_a_terminal_too_small_to_lay_out(self):
        for rows, cols in ((4, 100), (1, 80), (2, 30)):
            with self.subTest(f"{cols}x{rows}"):
                seen = self.run_tui(rows, cols)
                self.assertNoCrash(seen, f"{cols}x{rows}")
                self.assertIn("too small", seen,
                              "a screen with no room must say so, not go blank")

    def test_shrinking_the_window_while_it_runs(self):
        # the layout is computed for one size and drawn against another
        for shrink in ((3, 60), (1, 20)):
            with self.subTest(f"{shrink[1]}x{shrink[0]}"):
                self.assertNoCrash(self.run_tui(24, 120, shrink_to=shrink),
                                   f"resize to {shrink}")

    def test_terminals_that_cannot_hide_the_cursor(self):
        # curs_set raises rather than returning a failure on these
        for term in ("vt100", "vt220", "ansi", "dumb"):
            with self.subTest(term):
                self.assertNoCrash(self.run_tui(24, 120, term=term), term)


if __name__ == "__main__":
    unittest.main()
