"""Hermes.

Keeps its state in one SQLite database, and keeps more of it than the others:
a session row already holds the working directory, the title, the model, and
when it was last active, so a row costs a single query.

Hermes also runs a gateway process that outlives the sessions it serves, which
is the sort of thing worth noticing before it sits there for a week.
"""

import glob
import os

from ..util import directory_size
from .base import KIND_GATEWAY, KIND_SESSION, STATUS_BUSY, STATUS_IDLE
from .sqlite import SqliteBackend

KIND_SUPERVISOR = "supervisor"
BACKGROUND = {KIND_GATEWAY, KIND_SUPERVISOR}


class HermesBackend(SqliteBackend):
    name = "hermes"
    root = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
    process_patterns = ("hermes", "hermes_cli.main", "hermes_cli.stderr_timestamp")
    resume_binary = "hermes"
    # the --resume flag reopens a session; `hermes resume` is a different
    # command that lifts an emergency stop, so do not "simplify" this
    resume_flag = "--resume"

    db_filename = "state.db"

    def available(self):
        return bool(self.database)

    def classify(self, proc):
        """Tell the gateway apart from the thing that watches it.

        Hermes starts a messaging gateway on its own and detaches it, so it
        outlives the session that caused it. The gateway runs under a wrapper
        that timestamps its stderr, and since the wrapper carries the gateway
        command after a `--`, both would otherwise read as the gateway.
        """
        cmd = proc["cmd"]
        if "stderr_timestamp" in cmd:
            return KIND_SUPERVISOR
        if "gateway run" in cmd:
            return KIND_GATEWAY
        return KIND_SESSION

    def is_background(self, kind):
        return kind in BACKGROUND

    def sessions(self, limit=None):
        return self.rows_from(
            "SELECT id, cwd, title, model, last_activity_at, ended_at "
            "FROM sessions ORDER BY last_activity_at DESC",
            limit,
            lambda r: {
                "session_id": r["id"],
                "title": r["title"] or "",
                "model": r["model"] or "",
                "cwd": r["cwd"] or "",
                "last_active": r["last_activity_at"] or 0,
                "status": "" if r["ended_at"] else self._turn(r["id"]),
                "disk": self._session_bytes(r["id"]),
            })

    def _turn(self, session_id):
        """Whose turn it is, from the newest message.

        Hermes records a finish reason when a reply completes, so an assistant
        message without one is still being written.
        """
        found = self.query("SELECT role, finish_reason FROM messages "
                           "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1",
                           (session_id,))
        if not found:
            return ""
        role = found[0]["role"]
        if role == "user":
            return STATUS_BUSY
        if role == "assistant":
            return STATUS_IDLE if found[0]["finish_reason"] else STATUS_BUSY
        return ""

    def _session_bytes(self, session_id):
        """The conversation in the database, plus what it dumped beside it.

        Hermes writes a full request dump per turn, and those are two orders of
        magnitude larger than the messages they record.
        """
        return sum(size for _, size in self.disk_breakdown(session_id, ""))

    def disk_breakdown(self, session_id, path):
        found = self.query("SELECT total(length(content)) + total(length(tool_calls)) "
                           "FROM messages WHERE session_id = ?", (session_id,))
        dumps = os.path.join(self.root, "sessions", f"request_dump_{session_id}_*.json")
        parts = [
            ("conversation", int(found[0][0] or 0) if found else 0),
            ("request dumps", sum(directory_size(p) for p in glob.glob(dumps))),
        ]
        return sorted([p for p in parts if p[1]], key=lambda p: -p[1])

    def details(self, row):
        found = self.query(
            "SELECT input_tokens, output_tokens, reasoning_tokens, api_call_count, "
            "estimated_cost_usd, billing_provider, message_count, tool_call_count "
            "FROM sessions WHERE id = ?", (row["session_id"],))
        if not found:
            return []
        r = found[0]
        return [
            ("provider", r["billing_provider"] or "-"),
            ("tokens", f"in {r['input_tokens'] or 0:,}  out {r['output_tokens'] or 0:,}"
                       f"  reasoning {r['reasoning_tokens'] or 0:,}"),
            ("calls", f"{r['api_call_count'] or 0} api, {r['message_count'] or 0} messages,"
                      f" {r['tool_call_count'] or 0} tool calls"),
            ("cost", f"${r['estimated_cost_usd'] or 0:.4f} estimated"),
        ]

    def history_counts(self, since):
        found = self.query("SELECT count(*), "
                           "sum(CASE WHEN last_activity_at > ? THEN 1 ELSE 0 END) "
                           "FROM sessions", (since,))
        if not found:
            return 0, 0
        return int(found[0][0] or 0), int(found[0][1] or 0)

    def history_bytes(self):
        # the request dumps under sessions/ are debugging artefacts, and they are
        # larger than the database that holds the actual conversations
        return (directory_size(self.database)
                + directory_size(os.path.join(self.root, "sessions"))
                + directory_size(os.path.join(self.root, "logs")))
