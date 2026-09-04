"""OpenCode.

Newer versions keep everything in one SQLite database, where a session row
already carries its directory, title, and model. Nothing has to be parsed to
build a row, which makes this the cheapest backend of the set.

Older versions wrote a tree of JSON files under `storage/`. Installs that never
migrated still have only that, so it is read when the database is absent.
"""

import glob
import json
import os

from .. import jsonl
from ..util import directory_size
from .base import Backend, KIND_SESSION, STATUS_BUSY, STATUS_IDLE
from .sqlite import SqliteBackend

KIND_SERVE = "serve"


class OpenCodeBackend(SqliteBackend):
    name = "opencode"
    root = os.path.expanduser(
        os.environ.get("OPENCODE_DATA", "~/.local/share/opencode"))
    session_glob = "storage/session/*/*.json"
    process_patterns = ("opencode",)
    resume_binary = "opencode"
    resume_flag = "--session"

    db_filename = "opencode.db"

    def classify(self, proc):
        # `opencode serve` hosts sessions for editors and keeps running after
        # every one of them is closed
        return KIND_SERVE if " serve" in proc["cmd"] else KIND_SESSION

    def is_background(self, kind):
        return kind == KIND_SERVE

    # SQLite ---------------------------------------------------------------

    def sessions(self, limit=None):
        if not self.database:
            return super(SqliteBackend, self).sessions(limit)
        return self.rows_from(
            "SELECT id, directory, title, model, time_updated "
            "FROM session ORDER BY time_updated DESC",
            limit,
            lambda r: {
                "session_id": r["id"],
                "title": r["title"] or "",
                "model": _model_name(r["model"]),
                "cwd": r["directory"] or "",
                "last_active": (r["time_updated"] or 0) / 1000.0,
                "status": self._turn(r["id"]),
                "disk": self._session_bytes(r["id"]),
            })

    def _turn(self, session_id):
        """Whose turn it is, from the newest message.

        An assistant message carries a `finish` reason once its turn is over, so
        one without it is still being written.
        """
        found = self.query("SELECT data FROM message WHERE session_id = ? "
                           "ORDER BY time_created DESC LIMIT 1", (session_id,))
        if not found:
            return ""
        try:
            data = json.loads(found[0]["data"])
        except ValueError:
            return ""
        if data.get("role") == "user":
            return STATUS_BUSY
        if data.get("role") == "assistant":
            return STATUS_IDLE if data.get("finish") else STATUS_BUSY
        return ""

    def _session_bytes(self, session_id):
        """What one session holds in the database, rather than on its own."""
        return sum(size for _, size in self.disk_breakdown(session_id, ""))

    def disk_breakdown(self, session_id, path):
        if not self.database:
            return super().disk_breakdown(session_id, path)
        parts = []
        for label, table in (("messages", "message"), ("parts", "part")):
            found = self.query(
                f"SELECT total(length(data)) FROM {table} WHERE session_id = ?",
                (session_id,))
            size = int(found[0][0] or 0) if found else 0
            if size:
                parts.append((label, size))
        parts.sort(key=lambda p: -p[1])
        return parts

    def details(self, row):
        if not self.database:
            return []
        found = self.query(
            "SELECT cost, tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, agent "
            "FROM session WHERE id = ?", (row["session_id"],))
        if not found:
            return []
        r = found[0]
        return [
            ("agent mode", r["agent"] or "-"),
            ("tokens", f"in {r['tokens_input'] or 0:,}  out {r['tokens_output'] or 0:,}"
                       f"  reasoning {r['tokens_reasoning'] or 0:,}"),
            ("cache", f"read {r['tokens_cache_read'] or 0:,}"
                      f"  write {r['tokens_cache_write'] or 0:,}"),
            ("cost", f"${r['cost'] or 0:.4f}"),
        ]

    def history_counts(self, since):
        if not self.database:
            return super().history_counts(since)
        found = self.query("SELECT count(*), "
                           "sum(CASE WHEN time_updated > ? THEN 1 ELSE 0 END) "
                           "FROM session", (since * 1000,))
        if not found:
            return 0, 0
        return int(found[0][0] or 0), int(found[0][1] or 0)

    def history_bytes(self):
        # a migrated install keeps the old tree around, and those are still
        # bytes sitting on the disk
        return (directory_size(self.database) if self.database else 0) \
            + directory_size(os.path.join(self.root, "storage"))

    # The JSON store older versions wrote ----------------------------------

    def extract(self, reader):
        data = jsonl.load(reader.path)
        model, status = self._latest(data.get("id", ""))
        info = {
            "cwd": data.get("directory", ""),
            "title": data.get("title", ""),
            "model": model,
            "status": status,
        }
        updated = (data.get("time") or {}).get("updated")
        if updated:
            info["last_active"] = updated / 1000.0
        return info

    def _latest(self, session_id):
        """Model and turn state from the newest message files, newest first."""
        if not session_id:
            return "", ""
        paths = glob.glob(os.path.join(self.root, "storage", "message",
                                       session_id, "*.json"))
        model = status = ""
        for path in sorted(paths, key=_mtime, reverse=True):
            data = jsonl.load(path)
            if not status:
                role = data.get("role")
                if role == "user":
                    status = STATUS_BUSY
                elif role == "assistant":
                    status = STATUS_IDLE
            if not model and data.get("modelID"):
                provider = data.get("providerID", "")
                model = f"{provider}/{data['modelID']}" if provider else data["modelID"]
            if model and status:
                break
        return model, status

    def session_id(self, path):
        return os.path.basename(path)[:-len(".json")]

    def disk_paths(self, session_id, path):
        return [path,
                os.path.join(self.root, "storage", "message", session_id),
                os.path.join(self.root, "storage", "session_diff",
                             f"{session_id}.json")]


def _model_name(value):
    """`{"id": "mimo-claude", "providerID": "9router"}` as `9router/mimo-claude`."""
    if not value:
        return ""
    try:
        data = json.loads(value)
    except ValueError:
        return str(value)
    if not isinstance(data, dict):
        return str(value)
    model = data.get("id", "")
    provider = data.get("providerID", "")
    return f"{provider}/{model}" if provider and model else model


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0
