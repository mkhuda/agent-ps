"""The table and the live view.

Nothing here knows which agent a row came from. Every backend fills the same
shape, so a column is one lookup and never a branch.
"""

import curses
import os
import shutil
import time

from . import VERSION, backends
from .collect import ATTACH_WINDOW, idle_seconds, is_live
from .backends.base import (ATTACH_INFERRED, KIND_ENDED, KIND_SESSION,
                            STATUS_BUSY)
from .procs import children_of, collect_tree, table as proc_table, terminate
from .resume import open_in_terminal, resume_command
from .util import FAILURES, human_bytes, human_duration, short_path

REFRESH_SECONDS = 2.0
MESSAGE_SECONDS = 6.0

#: Assigned to agents in registry order, so a new backend gets a colour without
#: anything here having to learn its name.
AGENT_COLOURS = (
    curses.COLOR_YELLOW,
    curses.COLOR_CYAN,
    curses.COLOR_MAGENTA,
    curses.COLOR_GREEN,
    curses.COLOR_BLUE,
    curses.COLOR_RED,
    curses.COLOR_WHITE,
)

#: A terminal offering 256 colours has room for more without disturbing the
#: first seven, which people have already learned. The first of these is the
#: blue Antigravity is branded in, since it lands on that slot.
EXTRA_COLOURS = (69, 208, 141, 84, 173, 205)

#: Attributes that set an agent apart from an earlier one wearing the same
#: colour, tried in order as the colours run out a second time.
EXTRA_WEIGHT = (curses.A_UNDERLINE, curses.A_REVERSE)


def agent_label(row):
    """Agent name, with the sort of process appended when it is not a session."""
    kind = row["kind"]
    if kind in (KIND_ENDED, KIND_SESSION):
        return row["agent"]
    return f"{row['agent']}:{kind}"


def status_label(row):
    """What the session is doing, or a dash when nothing says.

    A process with no session paired to it has no turn to report.
    """
    if row["kind"] == KIND_ENDED:
        return "ended"
    return row["status"] or "-"


def pid_label(row):
    """PID, marked when the pairing with a session was inferred.

    Only Claude Code records which process runs which session. Elsewhere the two
    are matched on working directory, which cannot tell apart two sessions of the
    same agent in one folder, so the guess is shown as a guess.
    """
    if not row["pid"]:
        return "-"
    return f"{row['pid']}?" if row["attach"] == ATTACH_INFERRED else str(row["pid"])


def model_label(row):
    model = row.get("model", "")
    if model.startswith("claude-"):
        model = model[len("claude-"):]
    return model or "-"


def idle_label(row):
    """Time since the last turn, coarser the further back it goes.

    A session last touched four months ago is old, and knowing it was 120 days
    and 18 hours does not make it any older. Keeping long spans short is what
    lets this column stay narrow enough to sit beside uptime.
    """
    seconds = idle_seconds(row)
    if not seconds:
        return "-"
    if seconds < 60:
        return "now"
    if seconds < 30 * 86400:
        return human_duration(seconds) + " ago"
    months = seconds // (30 * 86400)
    return f"{months}mo ago" if months < 12 else f"{months // 12}y ago"


COLUMNS = [
    ("PID", 8, pid_label),
    ("AGENT", 18, agent_label),
    ("SESSION", 24, lambda r: r["name"] or "-"),
    ("STATUS", 7, status_label),
    ("MODEL", 20, model_label),
    ("UPTIME", 8, lambda r: human_duration(r["uptime"]) if r["pid"] else "-"),
    ("ACTIVE", 10, idle_label),
    ("CPU", 6, lambda r: f"{r['cpu']:.1f}%" if r["pid"] else "-"),
    ("MEM", 7, lambda r: human_bytes(r["rss"], unit_kb=True) if r["pid"] else "-"),
    ("DISK", 8, lambda r: human_bytes(r["disk"]) if r.get("disk") else "-"),
    ("DIR", 24, lambda r: short_path(r.get("cwd", ""), 23)),
    ("TITLE", 0, lambda r: r["title"] or "-"),
]


def _agent_style(index):
    """A colour, and an attribute that sets it apart from an earlier repeat.

    A basic terminal has seven usable colours, and an eighth agent wearing the
    first one's is not a hypothetical past this point in the registry. On a
    256-colour terminal it gets a colour of its own instead; on anything
    smaller, the same colour with a distinguishing attribute.
    """
    if index < len(AGENT_COLOURS):
        return AGENT_COLOURS[index], curses.A_BOLD
    if curses.COLORS >= 256:
        colour = EXTRA_COLOURS[(index - len(AGENT_COLOURS)) % len(EXTRA_COLOURS)]
        return colour, curses.A_BOLD
    generation = index // len(AGENT_COLOURS)
    weight = EXTRA_WEIGHT[(generation - 1) % len(EXTRA_WEIGHT)]
    return AGENT_COLOURS[index % len(AGENT_COLOURS)], curses.A_BOLD | weight


def column_span(label):
    """Where a column starts and how wide it is, derived from COLUMNS itself.

    Computed rather than written down, so changing a width in one place does not
    quietly misalign anything that paints over a cell.
    """
    start = 0
    for name, size, _ in COLUMNS:
        if name == label:
            return start, size
        start += size
    return 0, 0


#: Cycled with `s`. The first keeps the natural order: agents in registry order,
#: running sessions before ended ones. The rest sort everything together, since
#: the question they answer is which session is the largest or the busiest, and
#: that does not care whether its process is still alive.
def _text(field):
    """Sort text A to Z, with the blanks pushed to the end either way."""
    return lambda row: (not row[field], row[field].lower())


#: Each is a name, the column it marks, the value to sort on, and whether the
#: useful end comes first. Numbers read largest first, which is what makes the
#: question worth asking; names read A to Z.
SORTS = (
    ("agent", None, None, True),
    ("active", "ACTIVE", lambda r: r["last_active"] or 0, True),
    ("disk", "DISK", lambda r: r.get("disk", 0), True),
    ("cpu", "CPU", lambda r: r["cpu"], True),
    ("mem", "MEM", lambda r: r["rss"], True),
    ("uptime", "UPTIME", lambda r: r["uptime"], True),
    ("session", "SESSION", _text("name"), False),
    ("title", "TITLE", _text("title"), False),
)

#: Everything the key bar has no room for, and every key not in it at all.
#: Doubles as the documentation for keys that never appear on screen otherwise.
HELP = (
    ("up, down, j, K", "move the selection"),
    ("home, end", "jump to the first or last row"),
    ("enter", "details for a live session, or reopen an ended one"),
    ("s", "cycle the sort column"),
    ("S", "reverse the sort direction"),
    ("k", "stop the selected process and its children"),
    ("b", "stop every background helper"),
    ("p", "remove what an ended session left behind"),
    ("e", "show or hide ended sessions"),
    ("/", "filter by session, title, agent, model, directory, or pid"),
    ("esc", "leave filter mode, or close a panel"),
    ("space", "pause refreshing"),
    ("r", "refresh now"),
    ("y, n", "answer a confirmation"),
    ("q", "quit"),
)


def format_row(row, width):
    cells = []
    used = 0
    for _, size, getter in COLUMNS:
        value = getter(row)
        if size == 0:
            cells.append(value[:max(0, width - used - 1)])
        else:
            cells.append(f"{value[: size - 1]:<{size}}")
            used += size
    return "".join(cells)[:width]


def format_header(width):
    parts = [label if size == 0 else f"{label[: size - 1]:<{size}}"
             for label, size, _ in COLUMNS]
    return "".join(parts)[:width]


def print_table(rows):
    width = 220
    print(format_header(width).rstrip())
    for row in rows:
        print(format_row(row, width).rstrip())


def _wrap(text, width):
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    lines.append(current)
    return lines or [""]


def matches_filter(row, needle):
    return (needle in row["name"].lower()
            or needle in row["title"].lower()
            or needle in row["agent"].lower()
            or needle in model_label(row).lower()
            or needle in row.get("cwd", "").lower()
            or needle in str(row["pid"]))


class Tui:
    C_HEADER = 1
    C_BUSY = 2
    C_BACKGROUND = 3
    C_SELECTED = 4
    C_WARNING = 5
    C_AGENT_BASE = 6

    def __init__(self, screen, snapshot):
        self.screen = screen
        self.snapshot = snapshot
        self.rows = []
        self.cursor = 0
        self.message = ""
        self.message_at = 0.0
        self.paused = False
        self.show_ended = False
        self.filter_text = ""
        self.editing_filter = False
        # (verb, detail, action): the verb and [y]/[n] must survive truncation,
        # so the detail that may not fit comes last
        self.pending = None
        self.show_help = False
        self.detail = None
        self.sort = 0
        self.descending = True
        self.last_poll = 0.0
        # opening a terminal can take a few seconds, so a second Enter on the
        # same row before it returns must not open a second one
        self.resuming = None
        self.all_rows = []
        self.history = {"bytes": 0, "ended": 0, "recent": 0, "spare": 0,
                        "spare_sessions": 0}

    def _put(self, y, x, text, n, attr=0):
        """addnstr that tolerates a screen too small for what it was told.

        The bottom right cell always raises, even on correct code, and a window
        that shrank between the layout and the draw raises everywhere.
        """
        if n <= 0 or y < 0 or x < 0:
            return
        try:
            self.screen.addnstr(y, x, text, n, attr)
        except curses.error:
            pass

    def setup(self):
        # a terminal whose terminfo has no `civis` raises rather than ignoring
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.agent_colour = {}
        if not curses.has_colors():
            # attributes work on every terminal, so they are the mechanism and
            # colour, below, is only ever an enhancement on top of them
            self.style = {
                "header": curses.A_REVERSE,
                "busy": curses.A_BOLD,
                "background": curses.A_DIM,
                "selected": curses.A_REVERSE | curses.A_BOLD,
                "warning": curses.A_BOLD | curses.A_UNDERLINE,
            }
            self.screen.timeout(200)
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.C_HEADER, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(self.C_BUSY, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_BACKGROUND, curses.COLOR_BLUE, -1)
        curses.init_pair(self.C_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.C_WARNING, curses.COLOR_YELLOW, -1)
        self.style = {
            "header": curses.color_pair(self.C_HEADER),
            "busy": curses.color_pair(self.C_BUSY),
            "background": curses.color_pair(self.C_BACKGROUND),
            "selected": curses.color_pair(self.C_SELECTED),
            "warning": curses.color_pair(self.C_WARNING),
        }
        for index, name in enumerate(backends.names()):
            colour, attr = _agent_style(index)
            pair = self.C_AGENT_BASE + index
            curses.init_pair(pair, colour, -1)
            self.agent_colour[name] = curses.color_pair(pair) | attr
        self.screen.timeout(200)

    def poll(self):
        """Read the machine. Everything here touches disk or shells out."""
        # the same window already loaded to pair processes with sessions, so
        # showing all of it costs nothing and makes sorting by size honest
        self.all_rows = self.snapshot.rows(show_ended=self.show_ended,
                                           limit=ATTACH_WINDOW)
        self.last_poll = time.time()
        self.history = self.snapshot.history()
        self.reshape()

    def reshape(self):
        """Filter and sort what the last poll returned, reading nothing.

        Separate from `poll` because sorting and filtering are questions about
        rows already in hand. Asking the machine again for every keystroke cost
        a fifth of a second each time.
        """
        held = self.selected()
        rows = self.all_rows
        if self.filter_text:
            needle = self.filter_text.lower()
            rows = [r for r in rows if matches_filter(r, needle)]
        key = SORTS[self.sort][2]
        if key:
            rows = sorted(rows, key=key, reverse=self.descending)
        self.rows = rows
        # follow the row rather than the position. Under a live sort the order
        # changes beneath the cursor, and k acts on whatever it is now over.
        current = self.same_row(held) if held else None
        self.cursor = (self.rows.index(current) if current
                       else max(0, min(self.cursor, len(self.rows) - 1)))
        # every pass builds new row objects, so a panel holding one from the
        # last pass would sit there showing the cpu and uptime it had when it
        # was opened
        if self.detail:
            self.detail = self.same_row(self.detail) or self.detail

    def same_row(self, row):
        """The current version of a row, if it is still on screen.

        Matched on the session where there is one, since a session outlives the
        process that ran it, and on the PID otherwise, which is what background
        helpers have instead.
        """
        for other in self.rows:
            if other["agent"] != row["agent"]:
                continue
            if row["session_id"]:
                if other["session_id"] == row["session_id"]:
                    return other
            elif row["pid"] and other["pid"] == row["pid"]:
                return other
        return None

    def selected(self):
        return self.rows[self.cursor] if self.rows else None

    def notify(self, text):
        """Show a note under the key bar for a few seconds.

        These report what an action just did, so they stop being useful the
        moment attention moves on, and one left behind reads as the outcome of
        whatever was done next.
        """
        self.message = text
        self.message_at = time.time()

    #: Below this there is no layout worth attempting, only a message saying so.
    MIN_HEIGHT = 6
    MIN_WIDTH = 40

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < self.MIN_HEIGHT or width < self.MIN_WIDTH:
            self.draw_too_small(height, width)
            return
        self.draw_summary(width)
        if self.show_help:
            self.draw_help(height, width)
            self.screen.noutrefresh()
            curses.doupdate()
            return
        if self.detail and not self.pending:
            self.draw_detail(height, width)
            self.screen.noutrefresh()
            curses.doupdate()
            return

        self.screen.attron(self.style["header"])
        self._put(2, 0, format_header(width).ljust(width - 1), width - 1)
        self.screen.attroff(self.style["header"])
        self.mark_sorted_column(width)

        body = max(1, height - 5)
        first = max(0, self.cursor - body + 1) if self.cursor >= body else 0
        for index, row in enumerate(self.rows[first: first + body]):
            line = format_row(row, width - 1)
            if first + index == self.cursor:
                attr = self.style["selected"]
                line = line.ljust(width - 1)
            elif row["kind"] == KIND_ENDED:
                attr = curses.A_DIM
            elif row["background"]:
                attr = self.style["background"]
            elif row["status"] == STATUS_BUSY:
                attr = self.style["busy"]
            else:
                attr = curses.A_NORMAL
            screen_row = 3 + index
            self._put(screen_row, 0, line, width - 1, attr)
            # the cursor has to stay unmistakable, so a selected row keeps its
            # own colour rather than being broken up by the agent's
            if first + index != self.cursor:
                self.paint_agent(screen_row, row, width, attr)

        if not self.rows:
            self._put(4, 2, self.empty_message(), width - 3)

        self.draw_footer(height, width)
        self.screen.noutrefresh()
        curses.doupdate()

    def paint_agent(self, screen_row, row, width, base):
        """Repaint the agent cell in that agent's colour.

        With five agents on screen the name is read constantly, and a colour is
        quicker to recognise than a word. Only this cell is coloured; the rest of
        the row still carries what the row is doing.
        """
        colour = self.agent_colour.get(row["agent"])
        if not colour:
            return
        start, size = column_span("AGENT")
        if start >= width - 1:
            return
        text = agent_label(row)[: size - 1].ljust(size)
        self._put(screen_row, start, text, min(size, width - 1 - start),
                            colour | (base & curses.A_DIM))

    def mark_sorted_column(self, width):
        """Show which column is sorting, on the column itself.

        A word in the summary line says the same thing, but the eye reading a
        table looks at the table, so the marker belongs in the heading it
        describes. Bold and underlined on top of the header's own colour,
        rather than a colour of its own: a pair distinct from the header would
        have needed a background too, and the closest free one is the cursor's.
        """
        label = SORTS[self.sort][1]
        if not label:
            return
        start, size = column_span(label)
        if start >= width - 1:
            return
        arrow = "v" if self.descending else "^"
        # the last column has no fixed width, so it is marked to its own length
        cell = size or len(label) + 1
        text = f"{label}{arrow}"[:cell].ljust(cell)
        self._put(2, start, text, min(cell, width - 1 - start),
                            self.style["header"] | curses.A_BOLD | curses.A_UNDERLINE)

    def detail_groups(self, row, backend):
        """Everything known about one session, in the order it gets asked.

        What it is, then what it is doing to the machine, then what it cost,
        then the command line, which is long and rarely the question.
        """
        session = [
            ("agent", agent_label(row), self.agent_colour.get(row["agent"], 0)),
            ("session", row["session_id"] or "not matched to a session", 0),
            ("name", row["name"] or "-", 0),
            ("status", status_label(row),
             self.style["busy"] if row["status"] == STATUS_BUSY else 0),
            ("model", row["model"] or "-", 0),
            ("title", row["title"] or "-", 0),
            ("directory", row["cwd"] or "-", 0),
        ]

        process = []
        if row["pid"]:
            process.append(("pid", f"{row['pid']}  (parent {row['ppid']})", 0))
            process.append(("uptime", human_duration(row["uptime"]), 0))
        process.append(("last turn", idle_label(row), 0))
        if row["error_code"]:
            # only ever set when nothing has succeeded since, so this is the
            # session's current state rather than something it already recovered from
            text = f"{row['error_code']}: {row['error_text']}" if row["error_text"] \
                else row["error_code"]
            process.append(("last error", text, self.style["warning"]))
        if row["attach"] == ATTACH_INFERRED:
            process.append(("paired", "matched by working directory, not reported",
                            self.style["warning"]))

        usage = []
        if row["pid"]:
            usage.append(("cpu", f"{row['cpu']:.1f}%", 0))
            usage.append(("memory", human_bytes(row["rss"], unit_kb=True), 0))
        if row.get("disk"):
            usage.append(("disk", human_bytes(row["disk"]), 0))
            # the total is rarely the useful part: a hundred megabytes of
            # transcript and a hundred of snapshots call for different answers
            if backend and row["session_id"]:
                spare = () if is_live(row) else backend.prunable
                for label, size in backend.disk_breakdown(row["session_id"],
                                                          row.get("path", "")):
                    # ended only, since prune leaves a running session alone
                    mark = "  can be pruned" if label in spare else ""
                    usage.append(("", f"{human_bytes(size):>7}  {label}{mark}",
                                  curses.A_DIM if mark else 0))
        if backend and row["session_id"]:
            usage.extend((label, value, 0) for label, value in backend.details(row))

        groups = [("session", session), ("process", process), ("usage", usage)]
        if row["cmd"]:
            groups.append(("command", [("", row["cmd"], 0)]))
        return groups

    def draw_detail(self, height, width):
        row = self.detail
        backend = self.snapshot.find_backend(row["agent"])

        self.screen.attron(self.style["header"])
        self._put(2, 0, " session details".ljust(width - 1), width - 1)
        self.screen.attroff(self.style["header"])

        screen_row = 4
        for heading, entries in self.detail_groups(row, backend):
            if not entries or screen_row >= height - 3:
                continue
            self._put(screen_row, 2, heading.upper(), 12,
                                curses.A_BOLD | curses.A_DIM)
            screen_row += 1
            for label, value, attr in entries:
                if screen_row >= height - 2:
                    break
                if label:
                    self._put(screen_row, 4, f"{label:>11}", 11,
                                        curses.A_DIM)
                # the command line is the one field that runs long, so it wraps
                # instead of being cut off where it stops being useful
                room = max(10, width - 20)
                text = str(value)
                # wrapping rebuilds the string from its words, so a value that
                # already fits is printed untouched and keeps its alignment
                chunks = [text] if len(text) <= room else _wrap(text, room)
                for chunk in chunks:
                    if screen_row >= height - 2:
                        break
                    self._put(screen_row, 17, chunk, width - 18, attr)
                    screen_row += 1
            screen_row += 1

        keys = " enter or esc close"
        if row["pid"]:
            keys += "   k stop this process"
        self._put(height - 1, 0, keys.ljust(width - 1), width - 1,
                            self.style["header"])

    def draw_too_small(self, height, width):
        """Say why the table is gone, rather than drawing a broken one."""
        for offset, text in enumerate((
                "terminal too small",
                f"{width}x{height}, need {self.MIN_WIDTH}x{self.MIN_HEIGHT}")):
            if offset < height:
                self._put(offset, 0, text, max(0, width - 1))
        self.screen.noutrefresh()
        curses.doupdate()

    def draw_help(self, height, width):
        """Every key, for the ones the footer had no room to name."""
        self.screen.attron(self.style["header"])
        self._put(2, 0, " keys".ljust(width - 1), width - 1)
        self.screen.attroff(self.style["header"])
        screen_row = 4
        for key, meaning in HELP:
            if screen_row >= height - 2:
                break
            self._put(screen_row, 4, f"{key:<16}", min(16, max(0, width - 5)),
                                curses.A_BOLD)
            self._put(screen_row, 21, meaning, max(0, width - 22))
            screen_row += 1
        self._put(height - 1, 0, " press any key to close".ljust(width - 1),
                            width - 1, self.style["header"])

    def draw_summary(self, width):
        """Two lines: what is running, then what it is costing."""
        live = [r for r in self.rows if is_live(r)]
        busy = sum(1 for r in live if r["status"] == STATUS_BUSY)
        background = sum(1 for r in live if r["background"])
        sessions = len(live) - background

        if self.filter_text and len(self.rows) != len(self.all_rows):
            parts = [f"{len(self.rows)} of {len(self.all_rows)} rows"]
        else:
            parts = [f"{sessions} session" + ("s" if sessions != 1 else "")]
        if busy:
            parts.append(f"{busy} busy")
        if background:
            parts.append(f"{background} background")

        state = "PAUSED" if self.paused else "LIVE"
        if self.show_ended:
            state = "ALL   " + state
        if self.filter_text:
            state = f"/{self.filter_text}   " + state

        title = f"agent-ps {VERSION}"
        self.screen.attron(curses.A_BOLD)
        self._put(0, 1, title, width - 2)
        self.screen.attroff(curses.A_BOLD)
        start = 1 + len(title)
        self._put(0, start, f"  {'  '.join(parts)}   {state}",
                            max(0, width - start - 1))

        # from live rows, not from self.rows: those are filtered, and a
        # machine wide total that moves when you type a search term is wrong
        disk = sum(r.get("disk", 0) for r in live)
        stale = max(0, self.history["bytes"] - disk)
        line = (f" cpu {sum(r['cpu'] for r in live):.1f}%"
                f"   mem {human_bytes(sum(r['rss'] for r in live), unit_kb=True)}"
                f"   disk {human_bytes(disk)} active"
                f"   history {self.history['ended']} sessions, {human_bytes(stale)}")
        self._put(1, 0, line, width - 1, curses.A_DIM)

        note = self.advisory()
        if note:
            column = min(len(line) + 3, width - 2)
            self._put(1, column, note, max(0, width - column - 1),
                                self.style["warning"])

    def empty_message(self):
        """Why the table has nothing in it, which is not always the same why.

        A filter that matched nothing and no agent being found look identical
        as an empty table; only the message tells them apart.
        """
        if self.filter_text:
            return f"No rows match /{self.filter_text}. Press esc to clear the filter."
        ended = self.history["ended"]
        if not self.show_ended and ended:
            return (f"No sessions running. {ended} ended session"
                    f"{'s' if ended != 1 else ''} on record, press e to show them.")
        return ("No coding agent sessions found. "
                "Run `agent-ps agents` to see what was detected.")

    def advisory(self):
        """One short note when something on screen deserves attention.

        Only the most useful is shown. A wall of warnings trains people to ignore
        the line entirely.
        """
        # an empty table because a command would not run is a different thing
        # from an empty table, and saying so first matters more than anything
        # else this line could report
        if FAILURES:
            return f"could not run {', '.join(sorted(FAILURES))}; rows may be missing"

        # a live session whose last turn failed explains a table that looks
        # idle when it is actually stuck, which is worth knowing before
        # anything about background helpers or old memory
        errored = [r for r in self.rows if r["pid"] and r["error_code"]]
        if errored:
            if len(errored) == 1:
                return f"{errored[0]['agent']} session hit {errored[0]['error_code']}"
            return f"{len(errored)} session(s) hit an API error"

        orphans = [r for r in self.rows if r["background"] and r["uptime"] > 3600]
        if orphans:
            return f"{len(orphans)} background helper(s) over an hour old, press b"

        stale = [r for r in self.rows if r["pid"] and idle_seconds(r) > 86400]
        if stale:
            return f"{len(stale)} session(s) untouched for over a day, still holding memory"

        recent = self.history["recent"]
        if recent and not self.show_ended:
            return f"{recent} session(s) ended in the past week, press e to show them"

        # last: a standing condition, not something that just happened
        spare = self.history["spare"]
        if spare:
            return (f"{human_bytes(spare)} reclaimable in ended sessions, "
                    f"run agent-ps prune")
        return ""

    def draw_footer(self, height, width):
        if self.editing_filter:
            prompt = f" filter: {self.filter_text}"
            self._put(height - 1, 0, prompt.ljust(width - 1), width - 1,
                                self.style["selected"])
            return
        if self.pending:
            verb, detail, _ = self.pending
            # the answer keys must survive truncation, so they come first and
            # the detail, which can run long, is what gets cut on a narrow screen
            prefix = f" [y] {verb}   [n] cancel"
            room = width - 1 - len(prefix) - 3
            text = f"{prefix}   {detail[:room]}" if detail and room > 0 else prefix
            self._put(height - 1, 0, text.ljust(width - 1), width - 1,
                                self.style["warning"])
            return
        # the note and the legend share a line: a note is worth interrupting the
        # legend for, and it is gone again in a few seconds
        if self.message and time.time() - self.message_at < MESSAGE_SECONDS:
            self._put(height - 2, 0, f" {self.message}", width - 1,
                                self.style["warning"])
        else:
            self.draw_legend(height - 2, width)
        keys = self.key_bar(width)
        self._put(height - 1, 0, keys.ljust(width - 1), width - 1,
                            self.style["header"])

    def key_bar(self, width):
        """The bottom row, longest first so the least useful key drops first.

        `q quit` is appended after fitting the rest, so a screen too narrow
        for anything else still has an answer for how to leave.
        """
        mode = "hide ended" if self.show_ended else "show ended"
        # ordered by priority: everything up to and including "e" is kept as
        # long as there is any room at all, the rest goes as the screen narrows
        entries = [("up/down", "move"), ("enter", "details"), ("k", "stop"),
                  ("/", "filter"), ("s", "sort"), ("e", mode), ("?", "keys"),
                  ("b", "background"), ("p", "prune"), ("S", "reverse")]
        tail = "q quit"
        while entries:
            text = (" " + "   ".join(f"{k} {v}" for k, v in entries)
                    + "   " + tail)
            if len(text) <= width - 1:
                return text
            entries.pop()
        return " " + tail

    def draw_legend(self, screen_row, width):
        """Which agents are on screen, each in its own colour.

        It sits above the keys rather than in the header, next to the column it
        explains being less useful than being where the eye already goes.
        """
        agents = sorted({r["agent"] for r in self.rows})
        if not agents:
            return
        # Positions are recorded while the line is built rather than searched
        # for afterwards. One agent's name can sit inside another's, and looking
        # for "pi" in a line that also says "copilot" finds the wrong two
        # letters and leaves the real one uncoloured.
        text = " agents  "
        placed = []
        for agent in agents:
            placed.append((len(text), agent))
            text += agent + "  "
        text = text.rstrip()
        self._put(screen_row, 0, text, width - 1, curses.A_DIM)

        # a long agent list leaves no room for a sentence on an 80 column
        # terminal, and dropping the note entirely is worse than abbreviating it
        name, column, _, _ = SORTS[self.sort]
        arrow = "v" if self.descending else "^"
        wordy = ("sorted by agent, running first" if not column
                 else f"sorted by {name}, "
                      f"{'high to low' if self.descending else 'low to high'}")
        for note in (wordy, f"sort: {name} {arrow}" if column else "sort: agent"):
            at = width - len(note) - 2
            if at > len(text) + 3:
                self._put(screen_row, at, note, len(note),
                                    curses.A_BOLD | curses.A_UNDERLINE)
                break

        for at, agent in placed:
            if at >= width - 1:
                continue
            self._put(screen_row, at, agent, min(len(agent), width - 1 - at),
                                self.agent_colour.get(agent, curses.A_BOLD))

    def confirm_kill(self, row):
        """Ask before stopping, and say where the process is.

        A PID matched by working directory could be the wrong session, so the
        directory is part of the question rather than something to check
        afterwards.
        """
        # the real process table, not the agent rows: a session's children are
        # MCP servers and helpers, which are not rows and would be orphaned
        order = collect_tree(row["pid"], children_of(proc_table()))
        detail = short_path(row["cwd"], 40) if row["cwd"] else ""
        if row["attach"] == ATTACH_INFERRED:
            detail += (", " if detail else "") + "matched by directory"
        verb = f"stop {len(order)} process" + ("es" if len(order) != 1 else "")
        self.pending = (verb, detail, lambda: self.apply_kill(order))

    def confirm_prune(self, row):
        """Ask before removing what an ended session left behind.

        Refuses a session that is still running, and says why. What it left
        is still in use while the process is alive.
        """
        name = row["title"] or row["name"] or row["session_id"] or "that session"
        if is_live(row):
            self.notify("That session is still running. Prune only "
                        "touches ended sessions.")
            return
        if not row["session_id"]:
            self.notify("No session id for that row.")
            return
        backend = self.snapshot.find_backend(row["agent"])
        parts = backend.prune_paths(row["session_id"], row.get("path", "")) \
            if backend else []
        if not parts:
            self.notify("Nothing to prune. Transcripts are never removed.")
            return
        total = sum(size for _, _, size in parts)
        labels = sorted({label for label, _, _ in parts})
        verb = f"remove {human_bytes(total)}"
        detail = (f"{', '.join(labels)} from {short_path(name, 24)}, "
                  f"transcript kept")
        self.pending = (verb, detail, lambda: self.apply_prune(row, parts))

    def apply_prune(self, row, parts):
        self.detail = None
        freed = failed = 0
        for _, entry, size in parts:
            try:
                if os.path.isdir(entry):
                    shutil.rmtree(entry)
                else:
                    os.remove(entry)
                freed += size
            except OSError:
                failed += 1
        note = f"Freed {human_bytes(freed)}."
        self.notify(note if not failed else f"{note} {failed} could not be removed.")
        self.snapshot.history(force=True)  # the sizes on screen are now wrong
        self.poll()

    def open_selected(self):
        """Enter does the obvious thing for the row it is on.

        A session that has ended is one you would want back, so it reopens. One
        that is running is already open, so the useful answer is what it is doing
        and what it has spent.
        """
        row = self.selected()
        if not row:
            return
        if is_live(row):
            self.detail = row
            return
        self.confirm_resume(row)

    #: How long a row stays debounced after a resume, matching the outer bound
    #: of open_in_terminal's own timeout: the whole slow call fits inside it.
    RESUME_DEBOUNCE = 10.0

    def confirm_resume(self, row):
        """Ask before reopening: the new session can start spending at once.

        Everything that would make the resume pointless or wrong is checked
        up front, so the question is only ever asked when it can actually be
        answered yes: a resume that cannot proceed is reported directly rather
        than offered and then failing.
        """
        if not row["session_id"]:
            self.notify("No session id for that row.")
            return
        identity = (row["agent"], row["session_id"])
        if self.resuming and self.resuming[0] == identity \
                and time.time() - self.resuming[1] < self.RESUME_DEBOUNCE:
            self.notify("Already reopening that session.")
            return
        backend = self.snapshot.find_backend(row["agent"])
        if not backend or not backend.resume_binary:
            self.notify(f"{row['agent']} sessions cannot be reopened from a terminal.")
            return
        name = row["title"] or row["name"] or row["session_id"]
        self.pending = (f"reopen {short_path(name, 40)}", "",
                        lambda: self.apply_resume(row, backend, identity))

    def apply_resume(self, row, backend, identity):
        self.resuming = (identity, time.time())
        # opening a terminal can take a few seconds, and the wait would
        # otherwise look identical to the screen simply not having reacted
        self.notify("Opening in a new tab...")
        self.draw()
        command = resume_command(backend, row["session_id"], row.get("cwd", ""))
        ok, note = open_in_terminal(command)
        self.notify(note if ok else f"{note} Run: {command}")

    def apply_kill(self, order):
        self.detail = None
        stopped = [p for p in order if terminate(p)]
        left = [p for p in order if p not in stopped]
        note = f"Stopped {len(stopped)} of {len(order)} processes."
        self.notify(note if not left else
                    f"{note} Still running: {', '.join(str(p) for p in left)}.")
        self.poll()

    def handle(self, key):
        if self.editing_filter:
            return self.handle_filter(key)

        if key == curses.KEY_RESIZE:
            curses.update_lines_cols()
            self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
            return True

        if self.show_help:
            self.show_help = False
            return True

        # moving around means the last result has been read, so drop the note
        # early rather than leaving it to time out under a different row
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END,
                   ord("j"), ord("K")):
            self.message = ""

        if self.detail and not self.pending:
            if key == ord("k") and self.detail["pid"]:
                self.confirm_kill(self.detail)
            elif key == ord("p"):
                self.confirm_prune(self.detail)
            elif key in (10, 13, curses.KEY_ENTER, 27, ord("q")):
                self.detail = None
            return True

        if self.pending:
            if key in (ord("y"), ord("Y")):
                action = self.pending[2]
                self.pending = None
                action()
            elif key in (ord("n"), ord("N"), 27):
                self.pending = None
                self.notify("Cancelled.")
            return True

        if key in (ord("q"), ord("Q")):
            return False
        if key in (10, 13, curses.KEY_ENTER):
            self.open_selected()
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor = min(self.cursor + 1, max(0, len(self.rows) - 1))
        elif key in (curses.KEY_UP, ord("K")):
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = max(0, len(self.rows) - 1)
        elif key == ord(" "):
            self.paused = not self.paused
        elif key == ord("s"):
            self.sort = (self.sort + 1) % len(SORTS)
            self.descending = SORTS[self.sort][3]
            self.reshape()
        elif key == ord("S"):
            self.descending = not self.descending
            self.reshape()
        elif key in (ord("r"), ord("R")):
            self.poll()
            self.message = ""
        elif key in (ord("e"), ord("E")):
            self.show_ended = not self.show_ended
            self.cursor = 0
            self.snapshot.history(force=True)
            self.poll()
        elif key == ord("/"):
            self.editing_filter = True
        elif key == ord("?"):
            self.show_help = True
        elif key == ord("k"):
            row = self.selected()
            if row and row["pid"]:
                self.confirm_kill(row)
            elif row and is_live(row):
                self.notify(f"{row['agent']} has no process of its own to stop.")
            elif row:
                self.notify("That session has already ended.")
        elif key == ord("p"):
            row = self.selected()
            if row:
                self.confirm_prune(row)
        elif key in (ord("b"), ord("B")):
            background = [r["pid"] for r in self.rows if r["background"]]
            if background:
                verb = ("stop " + str(len(background)) + " background process"
                        + ("es" if len(background) != 1 else ""))
                self.pending = (verb, "", lambda: self.apply_kill(background))
            else:
                self.notify("No background processes.")
        return True

    def handle_filter(self, key):
        if key in (10, 13, curses.KEY_ENTER):
            self.editing_filter = False
        elif key == 27:
            self.editing_filter = False
            self.filter_text = ""
            self.reshape()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            # backspacing past the last character leaves filter mode, so the key
            # hints come back without needing escape
            if not self.filter_text:
                self.editing_filter = False
            else:
                self.filter_text = self.filter_text[:-1]
                if not self.filter_text:
                    self.editing_filter = False
                self.reshape()
        elif 32 <= key < 127:
            self.filter_text += chr(key)
            self.reshape()
        return True

    def loop(self):
        self.setup()
        self.poll()
        while True:
            self.draw()
            key = self.screen.getch()
            if key != -1 and not self.handle(key):
                break
            if not self.paused and not self.pending:
                if time.time() - self.last_poll >= REFRESH_SECONDS:
                    self.poll()
