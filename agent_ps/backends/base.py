"""What every agent has to provide, and what most of them get for free.

A backend answers questions about one agent and returns plain data. It never
draws anything and never decides how a row is displayed, so the table never has
to ask which agent a row came from.

Most agents keep one JSONL log per session, so that is the default behaviour
here. A backend overrides only the parts where its agent differs.
"""

import glob
import os
import time

from .. import jsonl
from ..procs import matches, working_dirs
from ..util import directory_size

KIND_SESSION = "session"
KIND_ENDED = "ended"
KIND_GATEWAY = "gateway"

STATUS_BUSY = "busy"
STATUS_IDLE = "idle"

#: How long a size is trusted before the directories are walked again.
DISK_POLL_SECONDS = 5.0

ATTACH_NONE = ""
ATTACH_DIRECT = "pid"
ATTACH_INFERRED = "cwd"


TITLE_LENGTH = 60

#: Shared, because the totals belong to the file rather than to the backend
#: that happens to be asking.
TALLY = jsonl.Tally()


def usage_lines(totals):
    """Token counts as panel lines, leaving out whatever the agent never wrote."""
    lines = []
    for label, fields in (
            ("tokens", (("in", "input"), ("out", "output"),
                        ("reasoning", "reasoning"))),
            ("cache", (("read", "cache_read"), ("write", "cache_write")))):
        parts = [f"{shown} {totals[name]:,}"
                 for shown, name in fields if totals.get(name)]
        if parts:
            lines.append((label, "  ".join(parts)))
    if totals.get("cost"):
        lines.append(("cost", f"${totals['cost']:.4f}"))
    return lines


def prompt_title(text):
    """An opening prompt, trimmed to serve as a title.

    Agents inject a context block as the first user message. That is machinery,
    not what the session was about, so it is rejected and the caller moves on to
    the next message.
    """
    text = " ".join((text or "").split())
    if not text or text.startswith("<"):
        return ""
    return text[:TITLE_LENGTH]


def blank_row(agent):
    return {
        "agent": agent,
        "pid": 0,
        "ppid": 0,
        "cmd": "",
        "kind": KIND_ENDED,
        "background": False,
        "session_id": "",
        # the file backing this session, which for a SQLite agent is the one
        # database every one of its sessions lives in
        "path": "",
        "name": "",
        "title": "",
        "cwd": "",
        "model": "",
        "uptime": 0,
        "last_active": 0,
        "cpu": 0.0,
        "rss": 0,
        "disk": 0,
        "attach": ATTACH_NONE,
        "status": "",
    }


class Backend:
    name = ""
    root = ""
    session_glob = ""
    process_patterns = ()
    resume_binary = ""
    resume_flag = "--resume"

    #: Extra directories that grow alongside the logs, counted in the totals.
    extra_dirs = ()

    #: How long a turn may appear to be running before it is called idle. A live
    #: turn keeps appending as it works, so silence for this long means the turn
    #: ended without a final entry, or the agent stopped.
    busy_timeout = 900

    #: Where a turn records what it spent, as paths into one log entry, tried in
    #: order until one is found. Agents move this between releases and some
    #: write it in two places, which is why it is a list rather than one path.
    usage_at = ()

    #: Our field names against the agent's own. Only what is listed is shown, so
    #: an agent that counts something nobody else does simply does not name it.
    usage_keys = {}

    #: Set where the agent writes a running total each turn rather than that
    #: turn's own numbers, in which case the newest entry is the answer and
    #: adding them up would count every turn as many times as it has successors.
    usage_cumulative = False

    def __init__(self):
        self.cache = jsonl.Cache()
        self._disk = {}
        self._disk_at = {}

    # Discovery -----------------------------------------------------------

    def available(self):
        return bool(self.root) and os.path.isdir(self.root)

    def session_paths(self):
        return glob.glob(os.path.join(self.root, self.session_glob))

    def owns(self, proc):
        return matches(proc["cmd"], self.process_patterns)

    # One session ---------------------------------------------------------

    def extract(self, reader):
        """Title, model, and working directory from one session log.

        Returning a partial dict is fine. Anything absent stays blank, which the
        table renders as a dash rather than treating as a special case.
        """
        return {}

    def session_id(self, path):
        return os.path.basename(path).split(".")[0]

    def describe(self, path):
        """One session as a row, whether or not a process is running it."""
        blank = {"title": "", "model": "", "cwd": "", "status": "", "last_active": 0}
        info = self.cache.info(path, self.extract, blank)
        row = blank_row(self.name)
        session_id = self.session_id(path)
        cwd = info.get("cwd") or self.fallback_cwd(path)
        row.update({
            "session_id": session_id,
            "path": path,
            "title": info.get("title", ""),
            "model": info.get("model", ""),
            "cwd": cwd,
            "name": os.path.basename(cwd) if cwd else "",
            "last_active": info.get("last_active", 0),
            "status": info.get("status", ""),
            "disk": self.disk_usage(session_id, path),
        })
        return row

    def fallback_cwd(self, path):
        """Where to look when a log never recorded its working directory."""
        return ""

    def sessions(self, limit=None):
        """Known sessions, most recently active first."""
        rows = []
        for path in self.session_paths():
            try:
                rows.append((os.path.getmtime(path), path))
            except OSError:
                continue
        rows.sort(reverse=True)
        if limit is not None:
            rows = rows[:limit]
        return [self.describe(path) for _, path in rows]

    # Processes -----------------------------------------------------------

    def classify(self, proc):
        """Which sort of process this is. Agents with helpers override this."""
        return KIND_SESSION

    def is_background(self, kind):
        return False

    def live_sessions(self):
        """Sessions that are alive without a process of their own.

        An agent embedded in an editor has no process to find in `ps`, but its
        sessions are still open and still spending. Backends that own processes
        return nothing here.
        """
        return []

    def attach(self, procs, index):
        """Pair live processes with the sessions they are running.

        Only Claude Code records the pairing, so the default is to match on
        working directory. That is a guess: two sessions of the same agent in one
        directory are indistinguishable, so the newer is chosen and the row is
        marked inferred rather than presented as fact.
        """
        rows = []
        dirs = working_dirs([p["pid"] for p in procs])
        claimed = {}
        # Sessions come newest first, so processes are matched newest first too.
        # Pairing them in process table order would hand the oldest process the
        # session that was active most recently, which is backwards whenever
        # someone has two of the same agent open in one directory.
        for proc in sorted(procs, key=lambda p: p["uptime"]):
            found = self._best_match(index, dirs.get(proc["pid"], ""), set(claimed.values()))
            if found:
                claimed[proc["pid"]] = found["session_id"]

        for proc in procs:
            row = blank_row(self.name)
            row.update({
                "pid": proc["pid"],
                "ppid": proc["ppid"],
                "cmd": proc["cmd"],
                "uptime": proc["uptime"],
                "cpu": proc["cpu"],
                "rss": proc["rss"],
            })
            row["kind"] = self.classify(proc)
            row["background"] = self.is_background(row["kind"])
            cwd = dirs.get(proc["pid"], "")
            if cwd:
                row["cwd"] = cwd
                row["name"] = os.path.basename(cwd)
            match = self._session_by_id(index, cwd, claimed.get(proc["pid"]))
            if match:
                row.update({
                    "session_id": match["session_id"],
                    "path": match["path"],
                    "title": match["title"],
                    "model": match["model"],
                    "cwd": match["cwd"] or cwd,
                    "name": match["name"] or row["name"],
                    "last_active": match["last_active"],
                    "status": self.settle(match["status"], match["last_active"]),
                    "disk": match["disk"],
                    "attach": ATTACH_INFERRED,
                })
            rows.append(row)
        return rows

    def settle(self, status, last_active):
        """Drop a busy claim the log has stopped backing up."""
        if status != STATUS_BUSY or not last_active:
            return status
        if time.time() - last_active > self.busy_timeout:
            return STATUS_IDLE
        return status

    @staticmethod
    def _best_match(index, cwd, claimed):
        """The most recently active session in a directory that is still free."""
        if not cwd:
            return None
        for candidate in index.get(cwd, []):
            if candidate["session_id"] not in claimed:
                return candidate
        return None

    @staticmethod
    def _session_by_id(index, cwd, session_id):
        if not session_id:
            return None
        return next((c for c in index.get(cwd, [])
                     if c["session_id"] == session_id), None)

    # Disk ----------------------------------------------------------------

    def disk_paths(self, session_id, path):
        """Everything on disk that belongs to one session, with a name for each.

        Named rather than bare, because the total is not the interesting part:
        a session can be a hundred megabytes and the answer to what to do about
        it depends entirely on which of these it is. Entries may contain a `*`,
        since some agents name files after the session and append a timestamp.
        """
        return [("log", path)]

    def disk_breakdown(self, session_id, path):
        """What the disk total is made of, largest first.

        Backends that keep sessions in a database override this, since their
        bytes are rows rather than files.
        """
        parts = []
        for label, entry in self.disk_paths(session_id, path):
            if "*" in entry:
                size = sum(directory_size(p) for p in glob.glob(entry))
            else:
                size = directory_size(entry)
            if size:
                parts.append((label, size))
        parts.sort(key=lambda p: -p[1])
        return parts

    def disk_usage(self, session_id, path):
        now = time.time()
        if now - self._disk_at.get(session_id, 0) < DISK_POLL_SECONDS:
            return self._disk.get(session_id, 0)
        total = sum(size for _, size in self.disk_breakdown(session_id, path))
        self._disk[session_id] = total
        self._disk_at[session_id] = now
        return total

    def history_counts(self, since):
        """How many sessions exist, and how many were active since a moment.

        Asked as a question rather than as a list of files, because not every
        agent keeps one file per session.
        """
        total = recent = 0
        for path in self.session_paths():
            total += 1
            try:
                if os.path.getmtime(path) > since:
                    recent += 1
            except OSError:
                pass
        return total, recent

    def history_bytes(self):
        roots = [os.path.join(self.root, self.session_glob.split("*")[0])]
        roots += [os.path.join(self.root, d) for d in self.extra_dirs]
        return sum(directory_size(r) for r in roots)

    def token_totals(self, path):
        """What one session has spent, as our field names.

        Only reached from the detail panel. This is the one thing here that
        reads a whole log rather than an end of it, so it is never on the path
        the table refreshes.
        """
        if not (self.usage_at and path):
            return {}
        if self.usage_cumulative:
            raw = self._last_usage(path)
        else:
            raw = TALLY.totals(path, self._usage_of)
        return {ours: raw[theirs] for ours, theirs in self.usage_keys.items()
                if raw.get(theirs)}

    def _usage_of(self, entry):
        """The usage object in one entry, from the first place it is found."""
        for where in self.usage_at:
            value = entry
            for key in where:
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, dict):
                return value
        return None

    def _last_usage(self, path):
        """The newest count, for agents that write a running total each turn.

        Nothing has to be added up in that case, so the tail is enough.
        """
        marker = self.usage_at[0][-1]
        for line in jsonl.Reader(path).tail():
            if marker not in line:
                continue
            entry = jsonl.parse_line(line)
            usage = self._usage_of(entry) if entry is not None else None
            if usage:
                return usage
        return {}

    def details(self, row):
        """Extra facts about one session, as label and value pairs.

        A backend that says where its agent writes token counts gets these for
        free; one that keeps them somewhere other than a log overrides this.
        """
        return usage_lines(self.token_totals(row.get("path", "")))

    # Resume --------------------------------------------------------------

    def resume(self, session_id):
        return f"{self.resume_binary} {self.resume_flag} {session_id}"
