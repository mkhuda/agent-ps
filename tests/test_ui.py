"""Formatting and the rules the table reads by."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_ps import ui, util
from agent_ps.backends.base import KIND_ENDED, KIND_SESSION, blank_row
from agent_ps.collect import is_live, idle_seconds


def row(**fields):
    """A live session by default; blank_row starts every row as ended."""
    base = blank_row(fields.pop("agent", "claude"))
    base["kind"] = KIND_SESSION
    base.update(fields)
    return base


class Formatting(unittest.TestCase):
    def test_sizes_read_at_a_glance(self):
        self.assertEqual(util.human_bytes(512), "0K", "half a kilobyte rounds away")
        self.assertEqual(util.human_bytes(512 * 1024), "512K")
        self.assertEqual(util.human_bytes(5 * 1024 ** 2), "5M")
        self.assertEqual(util.human_bytes(2 * 1024 ** 3), "2.0G")

    def test_elapsed_time_from_the_process_table(self):
        self.assertEqual(util.parse_elapsed("05:00"), 300)
        self.assertEqual(util.parse_elapsed("01:00:00"), 3600)
        self.assertEqual(util.parse_elapsed("2-03:00:00"), 2 * 86400 + 3 * 3600)

    def test_paths_lose_whole_segments_rather_than_half_a_word(self):
        short = util.short_path("/very/long/path/to/some/project", 20)
        self.assertTrue(short.startswith(".../"))
        self.assertTrue(short.endswith("project"))
        self.assertLessEqual(len(short), 20)

    def test_a_path_that_fits_is_left_alone(self):
        self.assertEqual(util.short_path("/tmp/work", 25), "/tmp/work")


class Columns(unittest.TestCase):
    def test_an_inferred_pairing_is_marked(self):
        self.assertEqual(ui.pid_label(row(pid=42, attach="cwd")), "42?")
        self.assertEqual(ui.pid_label(row(pid=42, attach="pid")), "42")
        self.assertEqual(ui.pid_label(row(pid=0)), "-")

    def test_a_plain_session_shows_only_its_agent(self):
        self.assertEqual(ui.agent_label(row(kind=KIND_SESSION)), "claude")
        self.assertEqual(ui.agent_label(row(kind="daemon")), "claude:daemon")

    def test_long_gaps_are_coarse_so_the_column_stays_narrow(self):
        now = time.time()
        self.assertEqual(ui.idle_label(row(last_active=now - 5)), "now")
        self.assertEqual(ui.idle_label(row(last_active=now - 3600)), "1h00m ago")
        self.assertEqual(ui.idle_label(row(last_active=now - 120 * 86400)), "4mo ago")
        self.assertEqual(ui.idle_label(row(last_active=0)), "-")

    def test_nothing_known_is_a_dash_rather_than_an_invented_word(self):
        self.assertEqual(ui.status_label(row(status="")), "-")
        self.assertEqual(ui.status_label(row(kind=KIND_ENDED)), "ended")


class Liveness(unittest.TestCase):
    """A PID is evidence of life, not the definition of it."""

    def test_a_session_without_a_process_can_still_be_open(self):
        self.assertTrue(is_live(row(agent="copilot", pid=0, kind=KIND_SESSION)))
        self.assertFalse(is_live(row(agent="copilot", pid=0, kind=KIND_ENDED)))

    def test_idle_seconds_is_zero_when_nothing_was_recorded(self):
        self.assertEqual(idle_seconds(row(last_active=0)), 0)


class Sorting(unittest.TestCase):
    def test_numbers_start_at_the_largest_and_names_at_a(self):
        for name, column, key, descending in ui.SORTS:
            if key is None:
                continue
            with self.subTest(sort=name):
                self.assertIsNotNone(column)
                self.assertEqual(descending, name not in ("session", "title"))

    def test_rows_with_nothing_in_that_column_go_last_either_way(self):
        rows = [row(name="beta"), row(name=""), row(name="alpha")]
        key = dict((s[0], s[2]) for s in ui.SORTS)["session"]
        self.assertEqual([r["name"] for r in sorted(rows, key=key)],
                         ["alpha", "beta", ""])

    def test_every_sort_names_a_column_that_exists(self):
        headings = {label for label, _, _ in ui.COLUMNS}
        for name, column, key, _ in ui.SORTS:
            if column:
                self.assertIn(column, headings, name)


class Layout(unittest.TestCase):
    def test_column_spans_come_from_the_table_definition(self):
        start, size = ui.column_span("AGENT")
        self.assertEqual(start, ui.COLUMNS[0][1])
        self.assertEqual(size, ui.COLUMNS[1][1])

    def test_the_last_column_has_no_fixed_width(self):
        self.assertEqual(ui.COLUMNS[-1][1], 0)

    def test_a_row_never_runs_past_the_screen(self):
        line = ui.format_row(row(title="x" * 400), 100)
        self.assertLessEqual(len(line), 100)


class Filtering(unittest.TestCase):
    def test_matching_looks_at_everything_a_person_might_type(self):
        r = row(name="web-app", title="fix the parser", model="opus-5",
                cwd="/tmp/web-app", pid=4321)
        for needle in ("web", "parser", "opus", "claude", "4321", "/tmp"):
            self.assertTrue(ui.matches_filter(r, needle), needle)
        self.assertFalse(ui.matches_filter(r, "nothing-like-this"))


if __name__ == "__main__":
    unittest.main()
