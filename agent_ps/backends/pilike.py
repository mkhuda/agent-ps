"""Pi and CommandCode.

These write the same log: a `session` entry carrying the working directory, then
`message` entries, with `model_change` whenever the model is switched. Only the
field naming the model differs, so one parser serves both.

Neither records a title, so the opening prompt stands in for one. That is what
the session was about, which is the question a title answers.
"""

import os

from .. import jsonl
from .base import Backend, STATUS_BUSY, STATUS_IDLE, prompt_title


class PiLikeBackend(Backend):
    #: Fields on a model_change entry, joined with a slash when there are two.
    model_fields = ("model",)

    def extract(self, reader):
        info = {"cwd": reader.find_head("cwd")}
        for line in reader.tail():
            if '"model_change"' not in line:
                continue
            entry = jsonl.parse_line(line)
            if entry and entry.get("type") == "model_change":
                parts = [entry.get(f) for f in self.model_fields if entry.get(f)]
                if parts:
                    info["model"] = "/".join(parts)
                    break
        info["title"] = self._opening_prompt(reader)
        info["status"] = self._turn(reader)
        return info

    @staticmethod
    def _turn(reader):
        """Whose turn it is, from the last message in the log.

        A user message with no reply after it means the agent is answering. An
        assistant message means it is waiting for the next prompt.
        """
        for line in reader.tail():
            if '"message"' not in line:
                continue
            entry = jsonl.parse_line(line)
            if not entry or entry.get("type") != "message":
                continue
            role = (entry.get("message") or {}).get("role")
            if role == "user":
                return STATUS_BUSY
            if role == "assistant":
                return STATUS_IDLE
        return ""

    @staticmethod
    def _opening_prompt(reader):
        for entry in reader.head():
            message = entry.get("message") or {}
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = content
            else:
                text = next((c.get("text", "") for c in content or []
                             if isinstance(c, dict) and c.get("type") == "text"), "")
            title = prompt_title(text)
            if title:
                return title
        return ""


    def disk_paths(self, session_id, path):
        return [("transcript", path)]


class PiBackend(PiLikeBackend):
    name = "pi"
    root = os.path.expanduser("~/.pi/agent")
    session_glob = "sessions/*/*.jsonl"
    process_patterns = ("pi",)
    resume_binary = "pi"
    # --resume opens a picker and takes no argument; --session is the one that
    # accepts an id
    resume_flag = "--session"
    model_fields = ("provider", "modelId")

    def session_id(self, path):
        # named <timestamp>_<uuid>.jsonl, and only the second half identifies it
        stem = os.path.basename(path)[:-len(".jsonl")]
        return stem.split("_", 1)[-1]


class CommandCodeBackend(PiLikeBackend):
    name = "commandcode"
    root = os.path.expanduser("~/.commandcode")
    session_glob = "projects/*/*.jsonl"
    process_patterns = ("cmd", "commandcode")
    resume_binary = "cmd"
    extra_dirs = ("file-history",)

    def session_paths(self):
        # checkpoint files sit beside the logs and share the session id
        return [p for p in super().session_paths() if ".checkpoints." not in p]

    def disk_paths(self, session_id, path):
        base = os.path.dirname(path)
        return [("transcript", path),
                ("checkpoints", os.path.join(base, f"{session_id}.checkpoints.jsonl")),
                ("metadata", os.path.join(base, f"{session_id}.meta.json")),
                ("file history", os.path.join(self.root, "file-history", session_id))]
