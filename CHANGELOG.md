# Changelog

## 0.1.0

First public release.

### Agents

Seven, in one table: Claude Code, Pi, CommandCode, Codex CLI, OpenCode, Hermes
and GitHub Copilot. Each keeps its own colour, and the legend above the keys is
the key to the palette.

Only Claude Code records which process runs which session. Everywhere else the
two are matched on working directory and the PID is marked with a `?`, since two
sessions of one agent in one folder cannot be told apart.

Copilot has no process at all. It runs inside the VS Code extension host, so its
rows carry no PID, uptime, CPU or memory, and it reports the credits each turn
spent instead.

### The table

Columns for agent, session, status, model, uptime, time since the last turn, cpu,
memory, disk and working directory.

`s` cycles the sort through eight columns and marks the heading it is sorting by;
`S` reverses it. `e` includes ended sessions. `/` filters. Enter opens a detail
panel for a live session, or reopens an ended one in a new terminal tab.

`k` stops a process tree after a confirmation that names the working directory,
and says when the pairing was inferred. `b` stops every background helper.

### Disk

The DISK column counts everything a session left behind, which is often more
than its log: subagent transcripts, file history, shell snapshots, request dumps.
A Codex shell snapshot is routinely larger than the transcript it belongs to.

### Build

A single zipapp with no dependencies, built by the standard library. The build is
reproducible, so the committed executable can be checked against the source, and
its SHA-256 is published in the README.

Tested on Python 3.8, 3.9, 3.10 and 3.14.
