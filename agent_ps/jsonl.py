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


class Tally:
    """Numbers summed over a whole log, scanned once and afterwards extended.

    Token counts are written per turn, so the only way to a session total is the
    whole file, which for a long session is a hundred megabytes. Logs are append
    only, though, so the total for a file that grew is the previous total plus
    whatever arrived after it. Remembering how far the last scan reached is what
    keeps a busy session from re-reading all of it every refresh.
    """

    def __init__(self):
        self._at = {}
        self._sums = {}
        self._mtimes = {}

    def totals(self, path, usage_of):
        try:
            stat = os.stat(path)
        except OSError:
            return {}
        size, mtime = stat.st_size, stat.st_mtime
        start = self._at.get(path, 0)
        # shrinking means truncated, and changing without growing means
        # rewritten. Either way nothing already counted still applies.
        if size < start or (size == start and self._mtimes.get(path) != mtime):
            start = 0
        elif size == start and path in self._sums:
            return self._sums[path]
        sums = dict(self._sums[path]) if start and path in self._sums else {}
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                data = handle.read()
        except OSError:
            # recorded nothing, so the next call tries this file again
            return self._sums.get(path, {})
        self._mtimes[path] = mtime
        # a turn may be half written, so stop at the last complete line and
        # leave the rest for the next call
        end = data.rfind(b"\n") + 1
        for line in data[:end].split(b"\n"):
            if b"usage" not in line:  # cheap, and skips most of a log
                continue
            entry = parse_line(line.decode("utf-8", "ignore"))
            usage = usage_of(entry) if entry is not None else None
            if not usage:
                continue
            for field, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    sums[field] = sums.get(field, 0) + value
        self._at[path] = start + end
        self._sums[path] = sums
        return sums


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
