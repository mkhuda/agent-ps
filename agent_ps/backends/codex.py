"""Codex CLI.

Every entry is an envelope: a type, a timestamp, and a `payload` holding the
actual record. The working directory arrives in `session_meta` at the top of the
file, the model in the most recent `turn_context`.
"""

import os

from .. import jsonl
from .base import Backend, STATUS_BUSY, STATUS_IDLE, prompt_title


class CodexBackend(Backend):
    name = "codex"
    root = os.path.expanduser("~/.codex")
    session_glob = "sessions/*/*/*/*.jsonl"
    process_patterns = ("codex",)
    resume_binary = "codex"
    resume_flag = "resume"
    extra_dirs = ("shell_snapshots", "thread-writer-locks")

    def disk_paths(self, session_id, path):
        # the shell snapshot is routinely larger than the transcript it belongs
        # to, and it is named after the session with a timestamp appended
        return [path,
                os.path.join(self.root, "shell_snapshots", f"{session_id}.*.sh"),
                os.path.join(self.root, "thread-writer-locks", f"{session_id}.lock")]

    def extract(self, reader):
        info = {}
        for entry in reader.head():
            payload = entry.get("payload") or {}
            if entry.get("type") == "session_meta":
                info["cwd"] = payload.get("cwd", "")
                break
        for line in reader.tail():
            if '"turn_context"' not in line:
                continue
            entry = jsonl.parse_line(line)
            payload = (entry or {}).get("payload") or {}
            if payload.get("model"):
                info["model"] = payload["model"]
                break
        info["title"] = self._opening_prompt(reader)
        info["status"] = self._turn(reader)
        return info

    @staticmethod
    def _turn(reader):
        """Codex marks the start and end of a turn itself, so read the marker."""
        for line in reader.tail():
            if '"task_started"' not in line and '"task_complete"' not in line:
                continue
            entry = jsonl.parse_line(line)
            kind = ((entry or {}).get("payload") or {}).get("type")
            if kind == "task_started":
                return STATUS_BUSY
            if kind == "task_complete":
                return STATUS_IDLE
        return ""

    @staticmethod
    def _opening_prompt(reader):
        for entry in reader.head():
            payload = entry.get("payload") or {}
            if entry.get("type") != "response_item" or payload.get("role") != "user":
                continue
            content = payload.get("content")
            if isinstance(content, str):
                text = content
            else:
                text = " ".join(c.get("text", "") for c in content or []
                                if isinstance(c, dict))
            title = prompt_title(text)
            if title:
                return title
        return ""

    def session_id(self, path):
        # named rollout-<timestamp>-<uuid>.jsonl
        stem = os.path.basename(path)[:-len(".jsonl")]
        parts = stem.split("-")
        return "-".join(parts[-5:]) if len(parts) >= 5 else stem
