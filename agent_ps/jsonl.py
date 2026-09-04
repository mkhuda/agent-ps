"""Cached reading of session logs.

Session logs reach several megabytes, and the fields worth showing sit at one
end or the other: the working directory is written when the session starts, the
title and current model near the last turn. So the whole file is never parsed.

Backends say what to pull out; the caching and the file handling live here, so
no backend reimplements either.
"""

import json
import os

HEAD_LINES = 40
TAIL_BYTES = 262144


class Reader:
    """The two ends of one log file, parsed lazily and at most once."""

    def __init__(self, path):
        self.path = path
        self._head = None
        self._tail = None

    def head(self):
        """Entries from the start of the file, in order."""
        if self._head is None:
            self._head = []
            try:
                with open(self.path, "r", errors="ignore") as handle:
                    for _ in range(HEAD_LINES):
                        line = handle.readline()
                        if not line:
                            break
                        entry = parse_line(line)
                        if entry is not None:
                            self._head.append(entry)
            except OSError:
                pass
        return self._head

    def tail(self):
        """Raw lines from the end of the file, newest first.

        Raw rather than parsed, so a caller can skip most lines with a substring
        test before paying for json.loads.
        """
        if self._tail is None:
            self._tail = []
            try:
                with open(self.path, "rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - TAIL_BYTES))
                    chunk = handle.read().decode("utf-8", "ignore")
                self._tail = list(reversed(chunk.splitlines()))
            except OSError:
                pass
        return self._tail

    def find_head(self, *fields):
        """First value found for any of these fields near the start."""
        for entry in self.head():
            for field in fields:
                if entry.get(field):
                    return entry[field]
        return ""


class Cache:
    """Extraction results, keyed by path and invalidated by modification time."""

    def __init__(self):
        self._values = {}
        self._mtimes = {}

    def info(self, path, extract, blank):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return dict(blank)
        if self._mtimes.get(path) == mtime:
            return self._values[path]
        value = dict(blank, last_active=mtime)
        try:
            value.update(extract(Reader(path)) or {})
        except (OSError, ValueError):
            pass
        self._values[path] = value
        self._mtimes[path] = mtime
        return value


def parse_line(line):
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def load(path):
    """A whole JSON file, or an empty dict. For sidecar metadata, not logs."""
    try:
        with open(path, "r", errors="ignore") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
