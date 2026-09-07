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

    # the writer lock stays: it is how codex knows who holds the session
    prunable = ("shell snapshot",)

    usage_at = (("payload", "info", "total_token_usage"),)
    usage_cumulative = True
    usage_keys = {"input": "input_tokens", "output": "output_tokens",
                  "reasoning": "reasoning_output_tokens",
                  "cache_read": "cached_input_tokens"}
    extra_dirs = ("shell_snapshots", "thread-writer-locks")

    def disk_paths(self, session_id, path):
        # the shell snapshot is routinely larger than the transcript it belongs
        # to, and it is named after the session with a timestamp appended
        return [("transcript", path),
                ("shell snapshot",
                 os.path.join(self.root, "shell_snapshots", f"{session_id}.*.sh")),
                ("writer lock",
                 os.path.join(self.root, "thread-writer-locks", f"{session_id}.lock"))]

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
        status, code, text = self._last_turn(reader)
        info["status"] = status
        if code:
            info["error_code"] = code
            info["error_text"] = text
        return info

    @staticmethod
    def _last_turn(reader):
        """Status and failure of the newest turn, from the same marker.

        A failure that stands on its own, rather than inside task_complete's
        own `error`, is always the line immediately before it: codex writes
        the two back to back with nothing in between.
        """
        lines = reader.tail()
        for index, line in enumerate(lines):
            if '"task_started"' not in line and '"task_complete"' not in line:
                continue
            entry = jsonl.parse_line(line)
            payload = (entry or {}).get("payload") or {}
            kind = payload.get("type")
            if kind == "task_started":
                return STATUS_BUSY, "", ""
            if kind == "task_complete":
                error = payload.get("error")
                if not isinstance(error, dict) and index + 1 < len(lines):
                    prior = jsonl.parse_line(lines[index + 1])
                    prior_payload = (prior or {}).get("payload") or {}
                    if prior_payload.get("type") == "error":
                        error = prior_payload
                if isinstance(error, dict):
                    return (STATUS_IDLE, error.get("codex_error_info") or "error",
                           error.get("message", ""))
                return STATUS_IDLE, "", ""
        return "", "", ""

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
