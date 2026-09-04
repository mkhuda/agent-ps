"""Formatting and shell helpers shared by every backend."""

import os
import subprocess


#: Commands that failed to run at all, as opposed to running and saying nothing.
#: A tool whose job is not to miss processes must not report an empty table when
#: it could not read the process table in the first place.
FAILURES = set()


def run(argv, timeout=5):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        FAILURES.discard(argv[0])
        return result.stdout
    except (subprocess.SubprocessError, OSError):
        FAILURES.add(argv[0])
        return ""


def parse_elapsed(text):
    """ps elapsed time, [[dd-]hh:]mm:ss, into seconds."""
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        days = int(head)
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts[-3:]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def human_duration(seconds):
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def human_bytes(value, unit_kb=False):
    """Compact size string. ps reports RSS in kilobytes, os.path in bytes."""
    mb = value / 1024 if unit_kb else value / 1024 / 1024
    if mb < 1:
        return f"{mb * 1024:.0f}K"
    if mb < 1024:
        return f"{mb:.0f}M"
    return f"{mb / 1024:.1f}G"


def directory_size(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def short_path(path, width=25):
    """Home-relative path, shortened by dropping leading segments.

    The last segments carry most of the meaning, so those are kept and whatever
    sits in front is collapsed.
    """
    if not path:
        return "-"
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    if len(path) <= width:
        return path
    # drop whole leading segments rather than cutting through one, so what is
    # left still reads as a path
    parts = [p for p in path.split(os.sep) if p]
    while len(parts) > 1 and len(os.sep.join(parts)) + 4 > width:
        parts.pop(0)
    short = ".../" + os.sep.join(parts)
    return short if len(short) <= width else short[:width - 1] + "\u2026"


def decode_project_dir(directory):
    """Best effort path for a session directory named after the project path.

    Several agents name these directories after the working directory with
    separators replaced by hyphens, which cannot be reversed when a folder name
    contains one, so candidate segments are checked against the filesystem.
    """
    segments = os.path.basename(directory).strip("-").split("-")
    current = "/"
    index = 0
    while index < len(segments):
        for span in range(len(segments) - index, 0, -1):
            candidate = os.path.join(current, "-".join(segments[index:index + span]))
            if os.path.isdir(candidate):
                current, index = candidate, index + span
                break
        else:
            break
    return current if current != "/" else ""
