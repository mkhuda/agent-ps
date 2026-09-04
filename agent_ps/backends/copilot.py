"""GitHub Copilot Chat, in VS Code.

The odd one out: Copilot runs inside the editor's extension host, so there is no
process to find and no process to stop. What it does have is the richest
accounting of the six, including the credits a turn spent, which the free tier
meters and nothing else surfaces.

A chat is alive when its workspace is open in VS Code, which is answered by
asking the editor which storage directories it currently holds open.
"""

import glob
import json
import os

from ..procs import run
from ..util import directory_size
from .base import (Backend, KIND_ENDED, KIND_SESSION, STATUS_BUSY,
                   STATUS_IDLE)


class CopilotBackend(Backend):
    name = "copilot"
    root = os.path.expanduser(
        os.environ.get("VSCODE_USER_DIR",
                       "~/Library/Application Support/Code/User"))
    session_glob = "workspaceStorage/*/chatSessions/*.jsonl"
    #: Nothing in the process table belongs to Copilot, so it claims no process.
    process_patterns = ()
    #: A chat lives in the editor's UI, so there is no command that reopens one.
    resume_binary = ""

    def __init__(self):
        super().__init__()
        self._open = None

    def owns(self, proc):
        return False

    def open_workspaces(self):
        """Storage directories VS Code currently has open.

        One lsof call for every editor process, cached for the pass. A workspace
        that is not open cannot have a live chat, which is the only way to tell
        an abandoned conversation from one still on screen.
        """
        if self._open is not None:
            return self._open
        self._open = set()
        pids = run(["pgrep", "-f", "Visual Studio Code|Code Helper"]).split()
        if pids:
            out = run(["lsof", "-p", ",".join(pids[:60])], timeout=15)
            for line in out.splitlines():
                marker = line.find("workspaceStorage/")
                if marker < 0:
                    continue
                tail = line[marker + len("workspaceStorage/"):]
                self._open.add(tail.split("/")[0])
        return self._open

    def session_id(self, path):
        return os.path.basename(path)[:-len(".jsonl")]

    def workspace_of(self, path):
        return os.path.basename(os.path.dirname(os.path.dirname(path)))

    def fallback_cwd(self, path):
        """The folder a workspace points at, from the editor's own record."""
        marker = os.path.join(os.path.dirname(os.path.dirname(path)),
                              "workspace.json")
        try:
            with open(marker) as handle:
                folder = json.load(handle).get("folder", "")
        except (OSError, ValueError):
            return ""
        return folder[len("file://"):] if folder.startswith("file://") else folder

    def extract(self, reader):
        state = replay(reader.path)
        requests = [r for r in (state.get("requests") or []) if isinstance(r, dict)]
        if not requests:
            # a chat that was opened and never used, of which there are many
            return {"skip": True}
        last = requests[-1]
        info = {
            "title": state.get("customTitle") or "",
            "model": model_of(state, requests),
            "status": turn_status(last),
        }
        stamp = last.get("timestamp")
        if stamp:
            info["last_active"] = stamp / 1000.0
        return info

    def describe(self, path):
        row = super().describe(path)
        row["kind"] = (KIND_SESSION if self.workspace_of(path) in self.open_workspaces()
                       else KIND_ENDED)
        return row

    def sessions(self, limit=None):
        self._open = None
        rows = [r for r in super().sessions(limit) if r["title"] or r["model"]]
        return rows

    def live_sessions(self):
        """Chats in a window that is currently open."""
        return [r for r in self.sessions(limit=200) if r["kind"] == KIND_SESSION]

    def details(self, row):
        matches = glob.glob(os.path.join(self.root, "workspaceStorage", "*",
                                         "chatSessions", f"{row['session_id']}.jsonl"))
        if not matches:
            return []
        requests = [r for r in (replay(matches[0]).get("requests") or [])
                    if isinstance(r, dict)]
        if not requests:
            return []
        prompt = sum(r.get("promptTokens") or 0 for r in requests)
        output = sum(r.get("completionTokens") or 0 for r in requests)
        credits = sum(r.get("copilotCredits") or 0 for r in requests)
        return [
            ("host", "VS Code, no process of its own"),
            ("tokens", f"in {prompt:,}  out {output:,}"),
            ("turns", f"{len(requests)}"),
            ("credits", f"{credits:.2f} used"),
        ]

    def disk_paths(self, session_id, path):
        base = os.path.dirname(os.path.dirname(path))
        return [path, os.path.join(base, "chatEditingSessions", session_id)]

    def history_bytes(self):
        return sum(directory_size(p) for p in
                   glob.glob(os.path.join(self.root, "workspaceStorage", "*",
                                          "chatSessions")))


def replay(path):
    """Rebuild a session from its journal of changes.

    The file is not a log of messages but a list of edits: an initial object,
    then `{kind, k: [path], v}` entries that set a value somewhere inside it.
    Replaying them in order is the only way to read any field.
    """
    state = {}
    try:
        handle = open(path, errors="ignore")
    except OSError:
        return state
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            key, value = entry.get("k"), entry.get("v")
            if not key:
                if isinstance(value, dict):
                    state.update(value)
                continue
            node = state
            try:
                for step in key[:-1]:
                    node = _descend(node, step)
                _assign(node, key[-1], value)
            except (TypeError, AttributeError, IndexError):
                continue
    return state


def _descend(node, step):
    if isinstance(step, int):
        while len(node) <= step:
            node.append({})
        return node[step]
    return node.setdefault(step, {})


def _assign(node, key, value):
    if isinstance(key, int):
        while len(node) <= key:
            node.append({})
        node[key] = value
    else:
        node[key] = value


def model_of(state, requests):
    """What actually answered, not the picker that chose it.

    A request records `modelId`, but under the default setting that is the
    alias `copilot/auto`, which says nothing. The family behind the alias is
    kept with the model the user selected.
    """
    selected = ((state.get("inputState") or {}).get("selectedModel") or {})
    family = (selected.get("metadata") or {}).get("family")
    if family:
        return family
    for request in reversed(requests):
        raw = request.get("modelId")
        if raw and raw != "copilot/auto":
            return raw
    return ""


def turn_status(request):
    state = request.get("modelState")
    if isinstance(state, dict) and not state.get("completedAt"):
        return STATUS_BUSY
    return STATUS_IDLE if request.get("response") is not None else STATUS_BUSY
