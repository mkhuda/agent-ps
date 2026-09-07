"""Formatting and the rules the table reads by."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import curses

from agent_ps import ui, util
from agent_ps.ui import SORTS, Tui
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


class Advisory(unittest.TestCase):
    """The one line that says what is worth doing about what is on screen."""

    def line(self, rows=(), history=None, show_ended=False):
        tui = Tui.__new__(Tui)
        tui.rows = list(rows)
        tui.show_ended = show_ended
        tui.history = {"bytes": 0, "ended": 0, "recent": 0, "spare": 0,
                       "spare_sessions": 0}
        tui.history.update(history or {})
        return tui.advisory()

    def test_reclaimable_space_is_reported(self):
        said = self.line(history={"spare": 101_000_000, "spare_sessions": 43})
        # binary units, as everywhere else in the table
        self.assertIn(util.human_bytes(101_000_000), said)
        self.assertIn("agent-ps prune", said)

    def test_nothing_is_said_when_there_is_nothing_to_reclaim(self):
        self.assertEqual(self.line(), "")

    def test_something_that_just_happened_is_said_first(self):
        # a standing condition must not push aside an event
        said = self.line(history={"spare": 101_000_000, "recent": 4})
        self.assertIn("ended in the past week", said)


class PrunableInTheDetail(unittest.TestCase):
    """Which parts of a session the panel marks as removable."""

    class Backend:
        prunable = ("subagents", "file history")

        def disk_breakdown(self, session_id, path):
            return [("transcript", 9_000_000), ("subagents", 4_000_000),
                    ("file history", 1_000_000)]

        def details(self, row):
            return []

    def usage_lines(self, row):
        with mock.patch.object(curses, "color_pair", lambda n: 0):
            tui = Tui.__new__(Tui)
            tui.agent_colour = {}
            tui.C_BUSY, tui.C_WARNING = 1, 2
            groups = dict(tui.detail_groups(row, self.Backend()))
        return [value for _, value, _ in groups["usage"]]

    def row(self, **over):
        base = dict(blank_row("claude"), session_id="s1", disk=14_000_000,
                    path="/tmp/s1.jsonl", kind=KIND_ENDED)
        base.update(over)
        return base

    def test_an_ended_session_marks_what_can_go(self):
        lines = " ".join(self.usage_lines(self.row()))
        self.assertIn("subagents  can be pruned", lines)
        self.assertIn("file history  can be pruned", lines)

    def test_the_transcript_is_never_marked(self):
        for line in self.usage_lines(self.row()):
            if "transcript" in line:
                self.assertNotIn("can be pruned", line)

    def test_a_running_session_marks_nothing(self):
        # prune leaves a live session alone however much it is holding, so
        # saying otherwise here would be a promise the command does not keep
        live = self.row(pid=4242, kind=KIND_SESSION, uptime=60)
        for line in self.usage_lines(live):
            self.assertNotIn("can be pruned", line)


class PruneKey(unittest.TestCase):
    """What `p` does, and what it refuses to do."""

    class Backend:
        prunable = ("subagents",)

        def __init__(self, parts=None):
            self.parts = parts if parts is not None else [
                ("subagents", "/tmp/nowhere/sub", 4_000_000)]

        def prune_paths(self, session_id, path):
            return list(self.parts)

    class Snapshot:
        def __init__(self, backend):
            self.backend = backend

        def find_backend(self, name):
            return self.backend

    def tui(self, backend=None):
        made = Tui.__new__(Tui)
        made.snapshot = self.Snapshot(backend or self.Backend())
        made.pending = None
        made.message = ""
        made.message_at = 0
        return made

    def row(self, **over):
        base = dict(blank_row("claude"), session_id="s1", path="/tmp/s1.jsonl",
                    title="web-app", kind=KIND_ENDED)
        base.update(over)
        return base

    def test_an_ended_session_is_asked_about_before_anything_goes(self):
        made = self.tui()
        made.confirm_prune(self.row())
        self.assertIsNotNone(made.pending)
        verb, detail, _ = made.pending
        self.assertIn("web-app", detail)
        self.assertIn("subagents", detail)
        self.assertIn("transcript kept", detail)
        self.assertIn("remove", verb)

    def test_a_running_session_is_refused_and_told_why(self):
        made = self.tui()
        made.confirm_prune(self.row(pid=4242, kind=KIND_SESSION, uptime=60))
        self.assertIsNone(made.pending, "a live session must not be offered")
        self.assertIn("still running", made.message)
        # the reason has to hold for every agent, and /rewind is Claude only
        self.assertNotIn("/rewind", made.message)

    def test_a_session_with_nothing_spare_says_so_rather_than_asking(self):
        made = self.tui(self.Backend(parts=[]))
        made.confirm_prune(self.row())
        self.assertIsNone(made.pending)
        self.assertIn("Nothing to prune", made.message)
        self.assertIn("never removed", made.message)

    def test_a_row_with_no_session_says_so_rather_than_blaming_transcripts(self):
        made = self.tui()
        made.confirm_prune(self.row(session_id=""))
        self.assertIsNone(made.pending)
        self.assertIn("No session id", made.message)

    def test_applying_removes_the_parts_and_leaves_the_transcript(self):
        import os
        import shutil as sh
        import tempfile
        root = tempfile.mkdtemp()
        self.addCleanup(sh.rmtree, root, ignore_errors=True)
        transcript = os.path.join(root, "s1.jsonl")
        with open(transcript, "w") as handle:
            handle.write('{"kept": true}\n')
        spare = os.path.join(root, "subagents")
        os.makedirs(spare)
        with open(os.path.join(spare, "a.jsonl"), "w") as handle:
            handle.write("x" * 1000)

        made = self.tui()
        made.detail = None
        made.snapshot.history = lambda force=False: {}
        made.poll = lambda: None
        made.apply_prune(self.row(path=transcript),
                         [("subagents", spare, 1000)])

        self.assertFalse(os.path.exists(spare))
        self.assertTrue(os.path.exists(transcript))
        with open(transcript) as handle:
            self.assertIn("kept", handle.read())
        self.assertIn("Freed", made.message)

    def test_what_cannot_be_removed_is_reported_rather_than_hidden(self):
        made = self.tui()
        made.detail = None
        made.snapshot.history = lambda force=False: {}
        made.poll = lambda: None
        made.apply_prune(self.row(),
                         [("subagents", "/does/not/exist/at/all", 500)])
        self.assertIn("could not be removed", made.message)


class ReclaimableTotal(unittest.TestCase):
    """The advisory total has to agree with what prune would actually do."""

    class Backend:
        name = "claude"
        prunable = ("subagents",)

        def __init__(self, rows):
            self.rows = rows

        def sessions(self, limit=None):
            return [dict(r) for r in self.rows]

        def prune_paths(self, session_id, path):
            return [("subagents", f"/tmp/{session_id}", 1_000_000)]

    def snapshot(self, rows, alive=()):
        from agent_ps.collect import Snapshot
        made = Snapshot([self.Backend(rows)])
        made._alive = set(alive)
        return made

    def session(self, sid, days):
        return dict(blank_row("claude"), session_id=sid,
                    last_active=time.time() - days * 86400)

    def test_old_ended_sessions_are_counted(self):
        made = self.snapshot([self.session("a", 30), self.session("b", 20)])
        self.assertEqual(made.reclaimable(time.time() - 7 * 86400), (2_000_000, 2))

    def test_a_session_with_a_process_is_left_out(self):
        # it is old enough, but prune would refuse it, so counting it would
        # promise space that pressing p never frees
        made = self.snapshot([self.session("a", 30), self.session("b", 20)],
                             alive=[("claude", "a")])
        self.assertEqual(made.reclaimable(time.time() - 7 * 86400), (1_000_000, 1))

    def test_a_recent_session_is_left_out(self):
        made = self.snapshot([self.session("a", 30), self.session("b", 1)])
        self.assertEqual(made.reclaimable(time.time() - 7 * 86400), (1_000_000, 1))


class Resilience(unittest.TestCase):
    """What the drawing layer does when the screen is not what it expected."""

    class Screen:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        def addnstr(self, y, x, text, n, attr=0):
            self.calls.append((y, x, text, n))
            if self.fail:
                raise curses.error("addnwstr() returned ERR")

    def test_a_screen_that_refuses_the_write_does_not_crash(self):
        tui = Tui.__new__(Tui)
        tui.screen = self.Screen(fail=True)
        tui._put(0, 0, "anything", 8)   # must not raise

    def test_nothing_is_written_outside_the_screen(self):
        tui = Tui.__new__(Tui)
        tui.screen = self.Screen()
        for y, x, n in ((-1, 0, 5), (0, -1, 5), (0, 0, 0), (0, 0, -3)):
            tui._put(y, x, "x", n)
        self.assertEqual(tui.screen.calls, [], "a doomed write was attempted")

    def test_a_write_that_fits_goes_through(self):
        tui = Tui.__new__(Tui)
        tui.screen = self.Screen()
        tui._put(1, 2, "hello", 5, 0)
        self.assertEqual(tui.screen.calls, [(1, 2, "hello", 5)])


class CursorFollowsTheRow(unittest.TestCase):
    """Under a live sort the order changes; the cursor must not drift.

    This is a safety property, not a nicety: k acts on whatever the cursor is
    over, so a cursor that moves on its own stops the wrong session.
    """

    def tui(self, rows, cursor=0, sort=0):
        made = Tui.__new__(Tui)
        made.all_rows = rows
        made.rows = list(rows)
        made.cursor = cursor
        made.filter_text = ""
        made.sort = sort
        made.descending = True
        made.detail = None
        return made

    def row(self, session, disk=0, **over):
        return dict(blank_row("claude"), session_id=session, disk=disk, **over)

    def test_the_cursor_stays_on_its_session_when_the_order_changes(self):
        a, b, c = self.row("a", 10), self.row("b", 20), self.row("c", 30)
        made = self.tui([a, b, c], cursor=0)
        made.rows = [a, b, c]
        held = made.selected()
        self.assertEqual(held["session_id"], "a")
        # the same three rows arrive sorted by disk, so "a" is now last
        made.all_rows = [c, b, a]
        made.sort = next(i for i, s in enumerate(SORTS) if s[0] == "disk")
        made.reshape()
        self.assertEqual(made.selected()["session_id"], "a",
                         "the cursor followed the position, not the session")

    def test_the_cursor_falls_back_when_its_session_is_gone(self):
        a, b = self.row("a"), self.row("b")
        made = self.tui([a, b], cursor=1)
        made.rows = [a, b]
        made.all_rows = [a]          # b ended between polls
        made.reshape()
        self.assertEqual(made.cursor, 0)

    def test_filtering_keeps_the_cursor_on_a_row_that_survives(self):
        a, b = self.row("a", title="keep me"), self.row("b", title="other")
        made = self.tui([a, b], cursor=0)
        made.rows = [a, b]
        made.filter_text = "keep"
        made.reshape()
        self.assertEqual(made.rows, [a])
        self.assertEqual(made.cursor, 0)


class ReshapeReadsNothing(unittest.TestCase):
    """Sorting and filtering are questions about rows already in hand."""

    class Exploding:
        def rows(self, *a, **k):
            raise AssertionError("reshape asked the machine again")

        def history(self, force=False):
            raise AssertionError("reshape asked the machine again")

    def test_reshape_never_touches_the_snapshot(self):
        made = Tui.__new__(Tui)
        made.snapshot = self.Exploding()
        made.all_rows = [dict(blank_row("claude"), session_id="a")]
        made.rows, made.cursor, made.detail = [], 0, None
        made.filter_text, made.sort, made.descending = "a", 0, True
        made.reshape()      # must not raise


class SummaryArithmetic(unittest.TestCase):
    def test_history_starts_with_every_key_the_advisory_reads(self):
        made = Tui.__new__(Tui)
        Tui.__init__(made, None, None)
        for key in ("bytes", "ended", "recent", "spare", "spare_sessions"):
            self.assertIn(key, made.history)


class KeyBarPriority(unittest.TestCase):
    """The key bar drops low priority keys first, and q is never one of them."""

    def bar(self, width, show_ended=False):
        made = Tui.__new__(Tui)
        made.show_ended = show_ended
        return made.key_bar(width)

    def test_everything_fits_on_a_wide_terminal(self):
        wide = self.bar(250)
        for key in ("up/down", "enter", "k", "b", "p", "e", "s", "S", "/", "?", "q"):
            self.assertIn(key, wide.split())

    def test_q_quit_survives_at_80_columns(self):
        # this used to be the first casualty: the bar was 114 characters
        bar = self.bar(80)
        self.assertLessEqual(len(bar), 79)
        self.assertIn("q quit", bar)

    def test_it_never_exceeds_the_width_it_was_given(self):
        for width in (250, 120, 100, 80, 60, 45, 40):
            with self.subTest(width):
                self.assertLessEqual(len(self.bar(width)), width - 1)

    def test_ended_mode_changes_the_e_label(self):
        self.assertIn("show ended", self.bar(250, show_ended=False))
        self.assertIn("hide ended", self.bar(250, show_ended=True))


class ConfirmationLayout(unittest.TestCase):
    """The answer keys must survive truncation; the detail is what gives."""

    def rendered(self, verb, detail, width):
        prefix = f" [y] {verb}   [n] cancel"
        room = width - 1 - len(prefix) - 3
        return f"{prefix}   {detail[:room]}" if detail and room > 0 else prefix

    def test_both_keys_are_present_at_every_realistic_width(self):
        detail = ("in ~/projects/agent-ps/some/very/long/nested/directory, "
                 "matched by directory")
        for width in (250, 100, 80, 60, 45):
            with self.subTest(width):
                text = self.rendered("stop 7 processes", detail, width)
                self.assertIn("[y]", text[:width - 1])
                self.assertIn("[n]", text[:width - 1])

    def test_the_verb_is_never_truncated_even_with_no_room_for_detail(self):
        text = self.rendered("stop 7 processes", "somewhere far away", 30)
        self.assertIn("stop 7 processes", text)


class EmptyMessage(unittest.TestCase):
    def make(self, filter_text="", show_ended=False, ended=0):
        made = Tui.__new__(Tui)
        made.filter_text = filter_text
        made.show_ended = show_ended
        made.history = {"ended": ended}
        return made

    def test_a_filter_with_no_matches_says_so(self):
        said = self.make(filter_text="zzz").empty_message()
        self.assertIn("/zzz", said)
        self.assertIn("esc", said)

    def test_hidden_ended_sessions_are_offered(self):
        said = self.make(ended=12).empty_message()
        self.assertIn("12", said)
        self.assertIn("press e", said)

    def test_nothing_at_all_points_at_agents_command(self):
        said = self.make(show_ended=True).empty_message()
        self.assertIn("agent-ps agents", said)

    def test_a_filter_takes_priority_over_ended_sessions(self):
        said = self.make(filter_text="zzz", ended=12).empty_message()
        self.assertIn("zzz", said)
        self.assertNotIn("12", said)


class AgentStyle(unittest.TestCase):
    """A colour that repeats must not look identical to the one before it."""

    def test_the_eighth_agent_does_not_match_the_first_on_a_basic_terminal(self):
        with mock.patch.object(curses, "COLORS", 8, create=True):
            colour0, attr0 = ui._agent_style(0)
            colour7, attr7 = ui._agent_style(7)
            self.assertEqual(colour0, colour7, "the premise: colours do repeat")
            self.assertNotEqual(attr0, attr7,
                                "same colour, same attribute: indistinguishable")

    def test_a_256_colour_terminal_gives_the_eighth_agent_its_own_colour(self):
        with mock.patch.object(curses, "COLORS", 256, create=True):
            colour0, _ = ui._agent_style(0)
            colour7, _ = ui._agent_style(7)
            self.assertNotEqual(colour0, colour7)


class HelpOverlay(unittest.TestCase):
    def test_every_key_in_the_bar_is_documented_somewhere(self):
        # the bar can drop a key as the terminal narrows; the overlay must
        # still say what it does
        from agent_ps.ui import HELP
        documented = " ".join(k for k, _ in HELP)
        for key in ("k", "b", "p", "e", "s", "S", "/", "q", "space", "r"):
            self.assertIn(key, documented)
