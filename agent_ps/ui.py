"""The table and the live view.

Nothing here knows which agent a row came from. Every backend fills the same
shape, so a column is one lookup and never a branch.
"""

import curses
import time

from . import backends
from .collect import ATTACH_WINDOW, idle_seconds, is_live
from .backends.base import (ATTACH_INFERRED, KIND_ENDED, KIND_SESSION,
                            STATUS_BUSY)
from .procs import children_of, collect_tree, terminate
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
    ("SESSION", 16, lambda r: r["name"] or "-"),
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
    C_SORT = 6
    C_AGENT_BASE = 7

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
        self.pending_kill = None
        self.detail = None
        self.sort = 0
        self.descending = True
        self.last_poll = 0.0
        self.history = {"bytes": 0, "ended": 0, "recent": 0}

    def setup(self):
        curses.curs_set(0)
        self.agent_colour = {}
        if not curses.has_colors():
            self.screen.timeout(200)
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.C_HEADER, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(self.C_BUSY, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_BACKGROUND, curses.COLOR_BLUE, -1)
        curses.init_pair(self.C_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.C_WARNING, curses.COLOR_YELLOW, -1)
        # the heading row is black on white, so the sorted column needs its own
        # background. Cyan rather than yellow, because a light terminal theme
        # renders a yellow background close enough to white to disappear.
        curses.init_pair(self.C_SORT, curses.COLOR_BLACK, curses.COLOR_CYAN)
        for index, name in enumerate(backends.names()):
            pair = self.C_AGENT_BASE + index
            curses.init_pair(pair, AGENT_COLOURS[index % len(AGENT_COLOURS)], -1)
            self.agent_colour[name] = curses.color_pair(pair)
        self.screen.timeout(200)

    def poll(self):
        # the same window already loaded to pair processes with sessions, so
        # showing all of it costs nothing and makes sorting by size honest
        rows = self.snapshot.rows(show_ended=self.show_ended, limit=ATTACH_WINDOW)
        if self.filter_text:
            needle = self.filter_text.lower()
            rows = [r for r in rows if matches_filter(r, needle)]
        key = SORTS[self.sort][2]
        if key:
            rows = sorted(rows, key=key, reverse=self.descending)
        self.rows = rows
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self.last_poll = time.time()
        self.history = self.snapshot.history()

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

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.draw_summary(width)
        if self.detail and not self.pending_kill:
            self.draw_detail(height, width)
            self.screen.noutrefresh()
            curses.doupdate()
            return

        self.screen.attron(curses.color_pair(self.C_HEADER))
        self.screen.addnstr(2, 0, format_header(width).ljust(width - 1), width - 1)
        self.screen.attroff(curses.color_pair(self.C_HEADER))
        self.mark_sorted_column(width)

        body = max(1, height - 5)
        first = max(0, self.cursor - body + 1) if self.cursor >= body else 0
        for index, row in enumerate(self.rows[first: first + body]):
            line = format_row(row, width - 1)
            if first + index == self.cursor:
                attr = curses.color_pair(self.C_SELECTED)
                line = line.ljust(width - 1)
            elif row["kind"] == KIND_ENDED:
                attr = curses.A_DIM
            elif row["background"]:
                attr = curses.color_pair(self.C_BACKGROUND)
            elif row["status"] == STATUS_BUSY:
                attr = curses.color_pair(self.C_BUSY)
            else:
                attr = curses.A_NORMAL
            screen_row = 3 + index
            self.screen.addnstr(screen_row, 0, line, width - 1, attr)
            # the cursor has to stay unmistakable, so a selected row keeps its
            # own colour rather than being broken up by the agent's
            if first + index != self.cursor:
                self.paint_agent(screen_row, row, width, attr)

        if not self.rows:
            self.screen.addnstr(4, 2, "No coding agent sessions running.", width - 3)

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
        self.screen.addnstr(screen_row, start, text, min(size, width - 1 - start),
                            colour | (base & curses.A_DIM) | curses.A_BOLD)

    def mark_sorted_column(self, width):
        """Show which column is sorting, on the column itself.

        A word in the summary line says the same thing, but the eye reading a
        table looks at the table, so the marker belongs in the heading it
        describes.
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
        self.screen.addnstr(2, start, text, min(cell, width - 1 - start),
                            curses.color_pair(self.C_SORT) | curses.A_BOLD)

    def detail_groups(self, row, backend):
        """Everything known about one session, in the order it gets asked.

        What it is, then what it is doing to the machine, then what it cost,
        then the command line, which is long and rarely the question.
        """
        session = [
            ("agent", agent_label(row), self.agent_colour.get(row["agent"], 0)),
            ("session", row["session_id"] or "not matched to a session", 0),
            ("status", status_label(row),
             curses.color_pair(self.C_BUSY) if row["status"] == STATUS_BUSY else 0),
            ("model", row["model"] or "-", 0),
            ("title", row["title"] or "-", 0),
            ("directory", row["cwd"] or "-", 0),
        ]

        process = []
        if row["pid"]:
            process.append(("pid", f"{row['pid']}  (parent {row['ppid']})", 0))
            process.append(("uptime", human_duration(row["uptime"]), 0))
        process.append(("last turn", idle_label(row), 0))
        if row["attach"] == ATTACH_INFERRED:
            process.append(("paired", "matched by working directory, not reported",
                            curses.color_pair(self.C_WARNING)))

        usage = []
        if row["pid"]:
            usage.append(("cpu", f"{row['cpu']:.1f}%", 0))
            usage.append(("memory", human_bytes(row["rss"], unit_kb=True), 0))
        if row.get("disk"):
            usage.append(("disk", human_bytes(row["disk"]), 0))
            # the total is rarely the useful part: a hundred megabytes of
            # transcript and a hundred of snapshots call for different answers
            if backend and row["session_id"]:
                for label, size in backend.disk_breakdown(row["session_id"],
                                                          row.get("path", "")):
                    usage.append(("", f"{human_bytes(size):>7}  {label}", 0))
        if backend and row["session_id"]:
            usage.extend((label, value, 0) for label, value in backend.details(row))

        groups = [("session", session), ("process", process), ("usage", usage)]
        if row["cmd"]:
            groups.append(("command", [("", row["cmd"], 0)]))
        return groups

    def draw_detail(self, height, width):
        row = self.detail
        backend = self.snapshot.find_backend(row["agent"])

        self.screen.attron(curses.color_pair(self.C_HEADER))
        self.screen.addnstr(2, 0, " session details".ljust(width - 1), width - 1)
        self.screen.attroff(curses.color_pair(self.C_HEADER))

        screen_row = 4
        for heading, entries in self.detail_groups(row, backend):
            if not entries or screen_row >= height - 3:
                continue
            self.screen.addnstr(screen_row, 2, heading.upper(), 12,
                                curses.A_BOLD | curses.A_DIM)
            screen_row += 1
            for label, value, attr in entries:
                if screen_row >= height - 2:
                    break
                if label:
                    self.screen.addnstr(screen_row, 4, f"{label:>11}", 11,
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
                    self.screen.addnstr(screen_row, 17, chunk, width - 18, attr)
                    screen_row += 1
            screen_row += 1

        keys = " enter or esc close"
        if row["pid"]:
            keys += "   k stop this process"
        self.screen.addnstr(height - 1, 0, keys.ljust(width - 1), width - 1,
                            curses.color_pair(self.C_HEADER))

    def draw_summary(self, width):
        """Two lines: what is running, then what it is costing."""
        live = [r for r in self.rows if is_live(r)]
        busy = sum(1 for r in live if r["status"] == STATUS_BUSY)
        background = sum(1 for r in live if r["background"])
        sessions = len(live) - background

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

        self.screen.attron(curses.A_BOLD)
        self.screen.addnstr(0, 1, "agent-ps", width - 2)
        self.screen.attroff(curses.A_BOLD)
        self.screen.addnstr(0, 10, f"  {'  '.join(parts)}   {state}", width - 11)

        disk = sum(r.get("disk", 0) for r in self.rows)
        stale = max(0, self.history["bytes"] - disk)
        line = (f" cpu {sum(r['cpu'] for r in live):.1f}%"
                f"   mem {human_bytes(sum(r['rss'] for r in live), unit_kb=True)}"
                f"   disk {human_bytes(disk)} active"
                f"   history {self.history['ended']} sessions, {human_bytes(stale)}")
        self.screen.addnstr(1, 0, line, width - 1, curses.A_DIM)

        note = self.advisory()
        if note:
            column = min(len(line) + 3, width - 2)
            self.screen.addnstr(1, column, note, max(0, width - column - 1),
                                curses.color_pair(self.C_WARNING))

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

        orphans = [r for r in self.rows if r["background"] and r["uptime"] > 3600]
        if orphans:
            return f"{len(orphans)} background helper(s) over an hour old, press b"

        stale = [r for r in self.rows if r["pid"] and idle_seconds(r) > 86400]
        if stale:
            return f"{len(stale)} session(s) untouched for over a day, still holding memory"

        recent = self.history["recent"]
        if recent and not self.show_ended:
            return f"{recent} session(s) ended in the past week, press e to show them"
        return ""

    def draw_footer(self, height, width):
        if self.editing_filter:
            prompt = f" filter: {self.filter_text}"
            self.screen.addnstr(height - 1, 0, prompt.ljust(width - 1), width - 1,
                                curses.color_pair(self.C_SELECTED))
            return
        if self.pending_kill:
            _, order, note = self.pending_kill
            text = f" Stop {len(order)} process(es){note}?  [y] confirm  [n] cancel"
            self.screen.addnstr(height - 1, 0, text.ljust(width - 1), width - 1,
                                curses.color_pair(self.C_WARNING))
            return
        # the note and the legend share a line: a note is worth interrupting the
        # legend for, and it is gone again in a few seconds
        if self.message and time.time() - self.message_at < MESSAGE_SECONDS:
            self.screen.addnstr(height - 2, 0, f" {self.message}", width - 1,
                                curses.color_pair(self.C_WARNING))
        else:
            self.draw_legend(height - 2, width)
        mode = "hide ended" if self.show_ended else "show ended"
        keys = (" up/down select   enter details   k stop   b background"
                f"   e {mode}   s sort   S reverse   / filter   q quit")
        self.screen.addnstr(height - 1, 0, keys.ljust(width - 1), width - 1,
                            curses.color_pair(self.C_HEADER))

    def draw_legend(self, screen_row, width):
        """Which agents are on screen, each in its own colour.

        It sits above the keys rather than in the header, next to the column it
        explains being less useful than being where the eye already goes.
        """
        agents = sorted({r["agent"] for r in self.rows})
        if not agents:
            return
        text = " agents  " + "  ".join(agents)
        self.screen.addnstr(screen_row, 0, text, width - 1, curses.A_DIM)

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
                self.screen.addnstr(screen_row, at, note, len(note),
                                    curses.color_pair(self.C_SORT) | curses.A_BOLD)
                break

        for agent in agents:
            at = text.find(agent)
            if at < 0 or at >= width - 1:
                continue
            self.screen.addnstr(screen_row, at, agent, min(len(agent), width - 1 - at),
                                self.agent_colour.get(agent, 0) | curses.A_BOLD)

    def confirm_kill(self, row):
        """Ask before stopping, and say where the process is.

        A PID matched by working directory could be the wrong session, so the
        directory is part of the question rather than something to check
        afterwards.
        """
        order = collect_tree(row["pid"], children_of(self.rows))
        note = f" in {short_path(row['cwd'], 40)}" if row["cwd"] else ""
        if row["attach"] == ATTACH_INFERRED:
            note += ", session matched by directory"
        self.pending_kill = (row["pid"], order, note)

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
        self.resume_selected(row)

    def resume_selected(self, row):
        if not row["session_id"]:
            self.notify("No session id for that row.")
            return
        backend = self.snapshot.find_backend(row["agent"])
        if not backend or not backend.resume_binary:
            self.notify(f"{row['agent']} sessions cannot be reopened from a terminal.")
            return
        command = resume_command(backend, row["session_id"], row.get("cwd", ""))
        ok, note = open_in_terminal(command)
        self.notify(note if ok else f"{note} Run: {command}")

    def apply_kill(self):
        _, order, _ = self.pending_kill
        self.pending_kill = None
        self.detail = None
        stopped = [p for p in order if terminate(p)]
        self.notify(f"Stopped {len(stopped)} of {len(order)} processes.")
        self.poll()

    def handle(self, key):
        if self.editing_filter:
            return self.handle_filter(key)

        # moving around means the last result has been read, so drop the note
        # early rather than leaving it to time out under a different row
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END,
                   ord("j"), ord("K")):
            self.message = ""

        if self.detail and not self.pending_kill:
            if key == ord("k") and self.detail["pid"]:
                self.confirm_kill(self.detail)
            elif key in (10, 13, curses.KEY_ENTER, 27, ord("q")):
                self.detail = None
            return True

        if self.pending_kill:
            if key in (ord("y"), ord("Y")):
                self.apply_kill()
            elif key in (ord("n"), ord("N"), 27):
                self.pending_kill = None
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
            self.cursor = 0
            self.poll()
        elif key == ord("S"):
            self.descending = not self.descending
            self.cursor = 0
            self.poll()
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
        elif key == ord("k"):
            row = self.selected()
            if row and row["pid"]:
                self.confirm_kill(row)
            elif row and is_live(row):
                self.notify(f"{row['agent']} has no process of its own to stop.")
            elif row:
                self.notify("That session has already ended.")
        elif key in (ord("b"), ord("B")):
            background = [r["pid"] for r in self.rows if r["background"]]
            if background:
                self.pending_kill = (background[0], background, "")
            else:
                self.notify("No background processes.")
        return True

    def handle_filter(self, key):
        if key in (10, 13, curses.KEY_ENTER):
            self.editing_filter = False
            self.poll()
        elif key == 27:
            self.editing_filter = False
            self.filter_text = ""
            self.poll()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            # backspacing past the last character leaves filter mode, so the key
            # hints come back without needing escape
            if not self.filter_text:
                self.editing_filter = False
            else:
                self.filter_text = self.filter_text[:-1]
                if not self.filter_text:
                    self.editing_filter = False
                self.poll()
        elif 32 <= key < 127:
            self.filter_text += chr(key)
            self.poll()
        return True

    def loop(self):
        self.setup()
        self.poll()
        while True:
            self.draw()
            key = self.screen.getch()
            if key != -1 and not self.handle(key):
                break
            if not self.paused and not self.pending_kill:
                if time.time() - self.last_poll >= REFRESH_SECONDS:
                    self.poll()
