"""Antigravity, the `agy` CLI from Google.

Each conversation is its own SQLite file, but the interesting fields are inside
protobuf blobs rather than columns, so they are pulled out by hand. Three of
them are reachable and one is not: the step status is the same value whether a
session is mid-turn or finished, so busy and idle cannot be told apart.

The workspace, the opening request and the model each live in a different blob,
and the choice of which blob matters more than it looks. Reading the model from
the large prompt blob finds whatever the agent was reading at the time, which in
a repository that mentions another agent is that agent's name.
"""

import glob
import os
import re
import sqlite3

from .base import Backend, prompt_title

#: Model names carry a version number. A bare vendor word is something the
#: session was reading, not the model that answered.
MODEL = re.compile(rb"\b((?:gemini|gpt|claude|grok)[a-z0-9.\-]*\d[a-z0-9.\-]*)\b", re.I)
REQUEST = re.compile(rb"<USER_REQUEST>\s*(.{1,400}?)\s*</USER_REQUEST>", re.S)


class AntigravityBackend(Backend):
    name = "antigravity"
    root = os.path.expanduser(
        os.environ.get("ANTIGRAVITY_HOME", "~/.gemini/antigravity-cli"))
    session_glob = "conversations/*.db"
    process_patterns = ("agy",)
    resume_binary = "agy"
    resume_flag = "--conversation"

    def session_id(self, path):
        return os.path.basename(path)[:-len(".db")]

    def extract(self, reader):
        try:
            db = sqlite3.connect(f"file:{reader.path}?mode=ro", uri=True, timeout=1.0)
        except sqlite3.Error:
            return {}
        try:
            return {
                "cwd": self._workspace(db),
                "title": self._request(db),
                "model": self._model(db),
            }
        except sqlite3.Error:
            return {}
        finally:
            db.close()

    @staticmethod
    def _blobs(db, table, column="data"):
        try:
            for (blob,) in db.execute(f"SELECT {column} FROM {table}"):
                if blob:
                    yield bytes(blob)
        except sqlite3.Error:
            return

    def _workspace(self, db):
        for raw in self._blobs(db, "trajectory_metadata_blob"):
            for uri in _strings(raw, b"file://"):
                if uri.startswith("file://"):
                    return uri[len("file://"):]
        return ""

    def _request(self, db):
        for raw in self._blobs(db, "gen_metadata"):
            found = REQUEST.search(raw)
            if found:
                title = prompt_title(found.group(1).decode("utf8", "ignore"))
                if title:
                    return title
        return ""

    def _model(self, db):
        for raw in self._blobs(db, "executor_metadata"):
            for name in MODEL.findall(raw):
                return name.decode("utf8", "ignore")
        return ""

    def disk_paths(self, session_id, path):
        return [("conversation", path)]


def _strings(raw, prefix):
    """Length prefixed strings starting with `prefix`, read out of a protobuf.

    Protobuf writes a field tag, then the length, then the bytes, with nothing
    to mark the end. Reading the length is what stops the next field's tag being
    swept up as a trailing character.
    """
    found = []
    start = 0
    while True:
        at = raw.find(prefix, start)
        if at < 0:
            return found
        start = at + 1
        if at >= 2 and raw[at - 2] == 0x0A:  # field 1, length delimited
            size = raw[at - 1]
            if size < 0x80 and at + size <= len(raw):
                found.append(raw[at:at + size].decode("utf8", "ignore"))
