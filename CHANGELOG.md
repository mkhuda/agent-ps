# Changelog

## 0.7.2

- DIR no longer cuts off the end of a long directory name when it is the
  distinguishing part (e.g. two similarly named git worktrees). The cut now
  happens at the front, keeping the tail, matching how every other path in
  this column is already shortened.

## 0.7.1

- SESSION column is wider, so a typical project directory name is no longer cut off
- Session name now shows in the detail panel, next to the session id
- The header title now shows the running version, e.g. `agent-ps 0.7.1`

## 0.7.0

Sessions now say when their last turn hit a rate limit, an auth failure, or a
server error.

- Claude Code, Codex CLI and OpenCode each mark a failed turn in their own
  shape, and a `last error` line appears in the detail panel when the newest
  turn failed and nothing has succeeded since. An error a session already
  recovered from is not shown at all: measured against real transcripts, a
  session that ever logs one almost always keeps going afterward, so only the
  newest turn's outcome is worth reporting.
- The advisory line points at a live session that just hit one, ahead of
  background helpers and stale sessions.
- Not covered: a session routed through a custom `ANTHROPIC_BASE_URL` or
  similar proxy can fail in a way the agent never logs at all. Verified
  against a live session that had just hit a rate limit through a local
  gateway: the transcript recorded nothing, and the session simply went idle.
- Enter on an ended session now asks before reopening it, since the new
  session can start spending the moment it opens and a resume could not be
  undone by closing the tab. Confirming still says so before the terminal
  actually opens, since that can take a few seconds and gave no sign anything
  had happened, and a stray key while it is opening no longer asks again or
  opens a second tab racing the first.

## 0.6.4

Terminals without colour now actually degrade instead of losing the cursor.

- A terminal with no colour used to lose the cursor, the header and every
  warning: they were all painted with a colour pair, and with none initialised
  that paint was invisible. Attributes now carry the same distinctions on
  every terminal, and colour is layered on top where it exists.
- A confirmation puts `[y]`/`[n]` first and the detail last, so the answer
  survives truncation instead of the context. `Stop 7 process(es) in
  ~/projects/…, session matched by directory? [y] confirm [n] cancel` used to
  lose its own answer keys past 80 columns.
- The key bar drops its least used keys as the terminal narrows rather than
  being cut off mid-word, and `q quit` is never one of the ones it drops.
  `?` opens a screen listing every key, including the ones that never fit.
- An eighth agent no longer wears the first one's colour on a plain terminal:
  the two now differ by weight as well as colour.
- The sorted column no longer shares a colour with the selected row; it is
  marked with the header's own colour, bold and underlined.
- An empty table says why: no rows match a filter, sessions are hidden behind
  `e`, or nothing was found at all, each with what to press next.

## 0.6.3

Two crashes, and a cursor that could move under your hand.

- Shrinking the terminal window no longer kills the program. Nothing guarded a
  write against a screen that changed size between the layout and the draw.
  Below six rows or forty columns it now says the terminal is too small rather
  than drawing a broken table.
- Terminals whose terminfo cannot hide the cursor no longer crash on the first
  line: `vt100`, `vt220`, `ansi` and `dumb` all failed at `curs_set`.
- The cursor follows the session it is on, not the position it was at. Sorted
  by cpu or by last turn the order changes every couple of seconds, so the
  highlighted row could become a different session between looking at it and
  pressing `k`.
- Filtering and sorting no longer read the machine again. Every typed character
  cost about four tenths of a second; nine characters now cost a tenth of a
  millisecond.
- Escape responds at once. ncurses waits a second after it by default, and
  escape leaves the filter, closes the panel and cancels a confirmation.
- The history figure no longer moves when a filter is typed, and the session
  count says how many rows the filter kept.

## 0.6.2

A stop that worked is no longer reported as a failure.

- Killing a session's children leaves them waiting to be collected by a parent
  that is itself about to be killed, and `ps` still lists them. They were
  counted as survivors, so stopping a Claude session with six MCP servers under
  it reported one of seven stopped when all seven had gone.

## 0.6.1

Stopping a session now stops what it started.

- `k` in the table stops the whole process tree, as `agent-ps stop` already
  did. It was reading the agent rows rather than the process table, so a
  session's MCP servers and helpers were left orphaned.
- A stop is no longer reported as failed when it worked. SIGKILL is delivered
  after `os.kill` returns, and the check ran before the kernel had finished.
- When something does survive, the message names the PIDs instead of only
  counting them.

## 0.6.0

Reclaiming what ended sessions leave behind.

- `agent-ps prune` removes the parts of an ended session that are not the
  conversation: subagent transcripts, file history, task records, shell
  snapshots, request dumps. On the machine it was written on that is 96M across
  43 sessions, against 33M for deleting every session older than a month,
  because the sessions holding the space are recent rather than old.
- It reports by default and removes only with `--apply`. Transcripts are never
  removed, a session with a running process is never touched whatever its age,
  and agents that keep sessions in a database are left alone entirely.
- `p` in the table does the same for the selected row, after a confirmation
  naming what goes. On a running session it refuses and says why.
- The detail panel marks which parts of a session can be pruned, and the line
  above the keys reports the total when there is one.
- One confirmation path now serves every destructive key, rather than each
  growing its own.
- Release pages take their title and notes from the changelog instead of the
  commit list.

## 0.5.0

The token counts four agents were already writing.

- Token counts for Claude Code, Codex, Pi and CommandCode, which all record
  what a turn spent and were simply never read. Seven of the eight agents now
  report what a session cost.
- The counts are summed over a whole log, which is the one thing here that
  reads a file end to end, so it happens only for the session whose detail
  panel is open. Logs are append only, so a session that grew costs the bytes
  it just wrote rather than all of it again.
- `agent-ps list` now says when `ps` or `lsof` failed even where the table came
  back with rows, rather than only when it came back empty.
- The tests run in CI, across every supported interpreter.

## 0.4.0

Antigravity, and a legend that was lying about one of the colours.

- Google's `agy` is the eighth agent. Each conversation is its own SQLite file,
  but the fields worth showing sit inside protobuf blobs rather than columns, so
  they are read out one at a time: the workspace by its length prefix, the
  opening request from its tags, and the model from the small metadata blob
  rather than the large one, which also holds whatever the session was reading
  and will happily hand back another agent's name.
- Its steps carry the same status whether a turn is running or finished, so it
  is the one agent whose busy and idle cannot be told apart. That column stays a
  dash rather than guessing at it.
- The legend coloured the wrong letters. It found each agent by searching the
  line it had just built, and `copilot` contains `pi`, so two letters in the
  middle of one name were painted in another's colour while the real `pi` was
  left with none. Positions are recorded while the line is composed now.
- An eighth agent had nowhere to go: a basic terminal has seven usable colours
  and the new one would have worn the first one's. Terminals offering 256 now
  draw from a wider palette without disturbing the seven already learned.

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
