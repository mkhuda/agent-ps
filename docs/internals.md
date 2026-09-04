# How agent-ps works

Reference for the parts of the table that are not obvious. The
[README](../README.md) covers everything needed to use it.

## Where each agent keeps its sessions

| Agent | Sessions | Overridable with |
|---|---|---|
| Claude Code | `~/.claude/projects/<project>/<session>.jsonl` | `CLAUDE_CONFIG_DIR` |
| CommandCode | `~/.commandcode/projects/<project>/<session>.jsonl` | - |
| Pi | `~/.pi/agent/sessions/<project>/<time>_<session>.jsonl` | - |
| Codex CLI | `~/.codex/sessions/<y>/<m>/<d>/rollout-*.jsonl` | - |
| OpenCode | `~/.local/share/opencode/opencode.db` | `OPENCODE_DATA` |
| Hermes | `~/.hermes/state.db` | `HERMES_HOME` |
| GitHub Copilot | `~/Library/Application Support/Code/User/workspaceStorage/` | `VSCODE_USER_DIR` |

`agent-ps agents` prints which of these exist on your machine and how many
sessions each holds.

Pi and CommandCode write the same log format, so they share a parser. OpenCode
and Hermes keep everything in SQLite, where a session row already holds the
directory, title and model, so their rows cost one query and no parsing at all.
OpenCode versions before the migration wrote a tree of JSON files under
`storage/` instead, and that is read when the database is absent, so an install
that never migrated still works.

## How busy and idle are decided

Whether a session is mid-turn is answered differently by each agent, and two of
them answer it outright:

| Agent | Where the answer comes from |
|---|---|
| Claude Code | `status` in `<config dir>/sessions/<pid>.json`, written by the agent |
| Codex CLI | its own `task_started` and `task_complete` markers in the log |
| Pi, CommandCode | the last message: a user turn with no reply yet means busy |
| OpenCode | the newest message, which carries a `finish` reason once its turn is over |
| Hermes | the newest message, which carries a finish reason the same way |
| GitHub Copilot | the last turn's `modelState`, which records when it completed |

The inferred ones can get stuck. A turn keeps appending as it works, so a
session that still looks busy after fifteen minutes of silence is reported idle
instead: the turn ended without a closing entry, or the agent is gone. Claude
Code is exempt, since it reports its own state and stops reporting when it exits.

A process with no session paired to it gets a dash, because there is no turn to
report.

## Background helpers

The AGENT column names the agent, and appends what a process is when it is not a
plain session:

| Label | What it is | Stopped by `b` |
|---|---|---|
| `claude:daemon` | `claude daemon run`, hosting background sessions | yes |
| `claude:bg-spare` | a pre-warmed worker kept ready for dispatch | yes |
| `claude:bg-pty` | terminal host for background sessions | yes |
| `opencode:serve` | the server that hosts sessions for editors | yes |
| `hermes:gateway` | the messaging gateway, started on its own and detached | yes |
| `hermes:supervisor` | the wrapper that runs the gateway and timestamps its log | yes |
| `claude:gateway` | a session pointed at a custom `ANTHROPIC_BASE_URL` | no |

`claude:gateway` is a normal session that happens to answer somewhere else, so it
is labelled and left alone. The rest are helpers: invisible in normal use, and
still running after the session that caused them is closed. Hermes is the
clearest case, since opening one chat starts a gateway a few seconds beforehand,
detached from your shell, and closing the chat leaves it behind.

The gateway and its supervisor are two processes, not two gateways. Both carry
the gateway command, since the supervisor takes it after a `--`, so they are
labelled by which one they actually are.

## One session, one row

Version managers put a shim on PATH that spawns the real interpreter under the
same name, so an agent installed through Volta or nvm would appear twice. A
process is dropped when one of its direct children carries an identical command
line, which tells a shim apart from a supervisor: a daemon spawns helpers with
different arguments, so it and its workers are both kept.

## Disk usage

A session writes more than its log, and the log is often the smaller half. The
DISK column adds up everything one session left behind:

| Agent | Counted per session |
|---|---|
| Claude Code | transcript, subagent transcripts, file history, tasks, session env |
| CommandCode | transcript, checkpoints, metadata, file history |
| Pi | transcript |
| Codex CLI | transcript, shell snapshot, writer lock |
| OpenCode | its messages and parts in the database |
| Hermes | its messages in the database, and one request dump per turn |
| GitHub Copilot | the chat journal and its editing session |

The last few are worth knowing about. A Codex shell snapshot runs to a few
hundred kilobytes and is routinely larger than the transcript it belongs to, and
Hermes writes a full request dump on every turn, which came to 121 KB for a six
message conversation whose text was 399 bytes.

The header shows both the total for live sessions and everything on disk, and the
gap between them is usually large. Logs outlive the process that wrote them, so a
machine with a few hundred megabytes of live sessions can be holding several
gigabytes of finished ones. Press `e` to see which sessions those bytes belong
to, then sort by DISK.

Sizes are re-read at most every five seconds, and the totals on their own slower
schedule, since walking the directories is the one expensive thing here.
