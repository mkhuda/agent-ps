"""The process table, and the matching rules that decide what is a session."""

import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_ps import procs


class Matching(unittest.TestCase):
    """A session that merely mentions a tool is not that tool.

    This rule has been got wrong three times in this project, in the skip list,
    in agent ownership, and in the legend, so it is worth pinning down.
    """

    def test_prompt_mentioning_a_tool_is_still_a_session(self):
        self.assertFalse(procs.is_tool("claude -p grep the logs", procs.SKIP))
        self.assertFalse(procs.is_tool("claude --file /tmp/grep.txt", procs.SKIP))
        self.assertFalse(procs.is_tool("codex resume /var/log/lsof.log", procs.SKIP))

    def test_the_tool_itself_is_skipped(self):
        for cmd in ("grep -r foo .", "/usr/bin/grep foo", "lsof -p 1",
                    "/home/x/.local/bin/agent-ps"):
            self.assertTrue(procs.is_tool(cmd, procs.SKIP), cmd)

    def test_a_helper_run_through_an_interpreter_is_skipped(self):
        self.assertTrue(procs.is_tool("node /home/x/.claude/statusline.js",
                                      procs.SKIP, procs.SKIP_SCRIPTS))

    def test_ownership_matches_the_executable_not_the_path(self):
        self.assertTrue(procs.matches("pi", ("pi",)))
        self.assertTrue(procs.matches("/usr/bin/pi --session x", ("pi",)))
        self.assertFalse(procs.matches("claude /home/pilot/project", ("pi",)))
        self.assertFalse(procs.matches("vim pizza.txt", ("pi",)))


class Shims(unittest.TestCase):
    """A version manager's shim and its child are one session, not two."""

    def test_identical_child_means_the_parent_is_a_shim(self):
        kept = procs.drop_shims([{"pid": 1, "ppid": 0, "cmd": "pi"},
                                 {"pid": 2, "ppid": 1, "cmd": "pi"}])
        self.assertEqual([p["pid"] for p in kept], [2])

    def test_a_supervisor_and_its_workers_are_both_kept(self):
        kept = procs.drop_shims([{"pid": 1, "ppid": 0, "cmd": "claude daemon run"},
                                 {"pid": 2, "ppid": 1, "cmd": "claude bg-spare"}])
        self.assertEqual(sorted(p["pid"] for p in kept), [1, 2])


class Tree(unittest.TestCase):
    def test_children_are_stopped_before_their_parents(self):
        table = [{"pid": 1, "ppid": 0, "cmd": ""}, {"pid": 2, "ppid": 1, "cmd": ""},
                 {"pid": 3, "ppid": 2, "cmd": ""}]
        order = procs.collect_tree(1, procs.children_of(table))
        self.assertEqual(order, [3, 2, 1])


@unittest.skipUnless(sys.platform.startswith("linux"),
                     "reads a working directory the way Linux exposes it")
class WorkingDirsOnLinux(unittest.TestCase):
    """The pairing every agent but Claude Code depends on."""

    def test_reads_the_directory_a_process_is_in(self):
        here = os.path.dirname(os.path.abspath(__file__))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 cwd=here)
        try:
            time.sleep(0.3)
            found = procs.working_dirs([child.pid])
            self.assertEqual(found.get(child.pid), os.path.realpath(here))
        finally:
            child.kill()
            child.wait()

    def test_a_pid_that_is_gone_is_left_out_rather_than_guessed(self):
        self.assertEqual(procs.working_dirs([999999]), {})


if __name__ == "__main__":
    unittest.main()
