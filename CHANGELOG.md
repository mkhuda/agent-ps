# Changelog

## 0.3.0

The detail panel earns its screen.

- It is grouped now, in the order the questions arrive: what the session is,
  what it is doing to the machine, what it has cost, and the command line last.
  The agent name takes its colour from the table, a busy status is green, and an
  inferred pairing reads as a warning rather than another grey line.
- The disk total is broken down. A hundred megabytes of transcript and a hundred
  of file history call for different answers, and the column alone could not
  tell you which you had. Every backend names its own parts, including the two
  that keep sessions in a database and count rows rather than files.
- The panel keeps up. It redrew on every pass and so looked live, but it held
  the row it was opened with, which left cpu, memory, uptime and the time since
  the last turn frozen at the moment it appeared.

## 0.2.0

Ways to install it, and a release that publishes itself.

- `curl | sh` takes the latest release, checks it against the checksum
  published beside it, and makes sure it runs before keeping it.
- On PyPI as `agent-ps`, so `uvx agent-ps`, `pipx install agent-ps` and
  `pip install agent-ps` all work.
- On npm as `@mkhuda/agent-ps`, scoped because the plain name is too close to
  an existing package. The command it installs is still `agent-ps`.
- Releases are built and published from the tag on a clean runner, which
  refuses to start when the tag, the version in the package and this file
  disagree.

The tool itself is unchanged.

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
