#!/usr/bin/env python3
"""The title and notes for one release, taken from CHANGELOG.md.

The changelog is already the place where a version is described for people, so
the release page is built from it rather than from the commit list, which
describes it for machines. Keeping one source means the two cannot drift.

A section looks like this, and the first line under the heading is the title:

    ## 0.6.0

    Reclaiming what ended sessions left behind.

    - one thing that changed
    - another

    ## 0.5.0

which gives the title `v0.6.0: Reclaiming what ended sessions left behind`, and
the bullets as the notes.

Usage:
    release_notes.py 0.6.0 --title
    release_notes.py 0.6.0 --notes
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def section(version, text):
    """The lines under one version heading, without the heading itself."""
    wanted = f"## {version}"
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if line.strip() == wanted:
            break
    else:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    found = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        found.append(line)
    while found and not found[0].strip():
        found.pop(0)
    while found and not found[-1].strip():
        found.pop()
    return found


def split(lines):
    """The one line summary, and everything after it."""
    if lines and not lines[0].lstrip().startswith("-"):
        title = lines[0].strip()
        rest = lines[1:]
    else:  # a version with bullets and no summary still gets a usable title
        title, rest = "", lines
    while rest and not rest[0].strip():
        rest.pop(0)
    return title, rest


def main(argv):
    if len(argv) != 2 or argv[1] not in ("--title", "--notes"):
        raise SystemExit("usage: release_notes.py <version> --title|--notes")
    version, what = argv[0], argv[1]
    with open(os.path.join(HERE, "CHANGELOG.md")) as handle:
        title, notes = split(section(version, handle.read()))
    if what == "--title":
        summary = title.rstrip(".")
        print(f"v{version}: {summary}" if summary else f"v{version}")
    else:
        print("\n".join(notes).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
