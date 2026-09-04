"""Claude Code.

The only agent that records which process is running which session, so it is
also the only one whose PID column is a fact rather than an inference.
"""

import glob
import json
import os

from ..procs import environment
from ..util import decode_project_dir
from .base import (ATTACH_DIRECT, Backend, KIND_GATEWAY, KIND_SESSION,
                   blank_row)

KIND_DAEMON = "daemon"
KIND_SPARE = "bg-spare"
KIND_PTY = "bg-pty"
BACKGROUND = {KIND_DAEMON, KIND_SPARE, KIND_PTY}


class ClaudeBackend(Backend):
    name = "claude"
    root = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
    session_glob = "projects/*/*.jsonl"
    process_patterns = ("claude",)
    resume_binary = "claude"
    extra_dirs = ("file-history", "tasks", "session-env")

    def extract(self, reader):
        info = {"cwd": reader.find_head("cwd")}
        for line in reader.tail():
            if info.get("title") and info.get("model"):
                break
            if '"ai-title"' not in line and '"model"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not info.get("title") and entry.get("type") == "ai-title":
                info["title"] = entry.get("aiTitle", "")
            if not info.get("model"):
                model = (entry.get("message") or {}).get("model")
                # <synthetic> marks messages Claude Code wrote itself, such as
                # compaction notices, not a model that served a turn
                if model and not model.startswith("<"):
                    info["model"] = model
        return info

    def fallback_cwd(self, path):
        return decode_project_dir(os.path.dirname(path))

    def classify(self, proc):
        cmd = proc["cmd"]
        if "claude daemon" in cmd:
            return KIND_DAEMON
        if "bg-spare" in cmd:
            return KIND_SPARE
        if "bg-pty-host" in cmd:
            return KIND_PTY
        env = environment(proc["pid"], ("ANTHROPIC_",))
        proc["_env"] = env
        return KIND_GATEWAY if env.get("ANTHROPIC_BASE_URL") else KIND_SESSION

    def is_background(self, kind):
        return kind in BACKGROUND

    def attach(self, procs, index):
        """Read the pairing rather than guessing it.

        Claude Code writes <config dir>/sessions/<pid>.json for every live
        session, which also carries the name and whether it is mid-turn. That
        file is authoritative and stops being written when the process exits, so
        unlike the inferred backends its busy state never needs a stall timeout.
        """
        rows = []
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

            data = self._pid_file(proc["pid"])
            session_id = data.get("sessionId", "")
            if session_id:
                row.update({
                    "session_id": session_id,
                    "name": data.get("name", ""),
                    "status": data.get("status", ""),
                    "cwd": data.get("cwd", ""),
                    "attach": ATTACH_DIRECT,
                })
                for candidate in index.get(data.get("cwd", ""), []):
                    if candidate["session_id"] == session_id:
                        row.update({
                            "path": candidate["path"],
                            "title": candidate["title"],
                            "model": candidate["model"],
                            "last_active": candidate["last_active"],
                            "disk": candidate["disk"],
                        })
                        break
                else:
                    row.update(self._by_id(session_id))
            # a launcher's routing alias is what the user picked, so it wins over
            # whichever upstream model happened to answer last
            alias = (proc.get("_env") or {}).get("ANTHROPIC_MODEL")
            if alias:
                row["model"] = alias
            rows.append(row)
        return rows

    def _pid_file(self, pid):
        try:
            with open(os.path.join(self.root, "sessions", f"{pid}.json")) as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _by_id(self, session_id):
        found = glob.glob(os.path.join(self.root, "projects", "*", f"{session_id}.jsonl"))
        if not found:
            return {}
        row = self.describe(found[0])
        return {"path": row["path"], "title": row["title"],
                "model": row["model"], "last_active": row["last_active"],
                "disk": row["disk"]}

    def disk_paths(self, session_id, path):
        base = os.path.dirname(path)
        return [path,
                os.path.join(base, session_id),
                os.path.join(self.root, "file-history", session_id),
                os.path.join(self.root, "tasks", session_id),
                os.path.join(self.root, "session-env", session_id)]
