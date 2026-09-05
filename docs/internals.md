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
| Antigravity | `~/.gemini/antigravity-cli/conversations/` | `ANTIGRAVITY_HOME` |

`agent-ps agents` prints which of these exist on your machine and how many
sessions each holds.

Pi and CommandCode write the same log format, so they share a parser. OpenCode
and Hermes keep everything in SQLite, where a session row already holds the
directory, title and model, so their rows cost one query and no parsing at all.
OpenCode versions before the migration wrote a tree of JSON files under
`storage/` instead, and that is read when the database is absent, so an install
that never migrated still works.

## Antigravity keeps its fields in protobuf

Each conversation is its own SQLite file, but the workspace, the opening request
and the model sit inside protobuf blobs rather than columns, so each is read out
by hand. The model comes from the small metadata blob rather than the large one,
which also holds whatever the session was reading and will hand back another
agent's name if asked.

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
| Antigravity | its steps do not distinguish a running turn, so the column stays a dash |

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

## Where the token counts come from

Every agent but one records what a turn spent, in its own place and under its
own names:

| Agent | Written at | Shape |
|---|---|---|
| Claude Code | `message.usage` | per turn |
| Pi | `message.usage`, or `usage` at the root | per turn |
| CommandCode | `usage`, with the cost in dollars | per turn |
| Codex CLI | `payload.info.total_token_usage` | a running total |
| OpenCode, Hermes | columns in the database | per session |
| GitHub Copilot | the chat journal, as credits | per turn |
| Antigravity | inside the protobuf blobs | not read |

The shape column is what decides how it is read. A running total already holds
the answer, so the newest one wins and adding them up would count each turn once
for every turn that followed it. Per turn counts have to be summed, and that
means the whole log rather than an end of it.

So this is the one thing here that reads a file from beginning to end, and it
happens only for the session whose detail panel is open, never while the table
refreshes. A long transcript reaches a hundred megabytes, which takes about half
a second. That is paid once: logs are only ever appended to, so a session that
grew is its previous total plus whatever arrived after the last read, and a busy
session costs the few kilobytes it just wrote rather than all of it again. A log
that shrank or changed without growing was replaced, and is counted afresh.

Claude Code also writes a count inside tool results, which belongs to a subagent
that tool ran rather than to the turn holding it. It is left out, since it is
spend of its own and would otherwise land on whichever session happened to
launch it.

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
| Antigravity | the conversation database |

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
