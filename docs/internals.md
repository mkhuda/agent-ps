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

Old Claude Code releases are said to have kept their logs one level deeper, at
`projects/<project>/sessions/<session>.jsonl`, which the path above would not
find. No install seen so far has that layout, so nothing reads it. It is written
down here because a report of old sessions missing from the table is the symptom
it would produce.

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

## The last error, and why only the newest one counts

Claude Code, Codex CLI and OpenCode each mark a turn that failed rather than
finished, in their own shape:

| Agent | Written at | Shape |
|---|---|---|
| Claude Code | `isApiErrorMessage` plus a taxonomy in `error`, on the turn itself | per turn |
| Codex CLI | `payload.type == "error"`, or nested in the `task_complete` that follows it | per turn |
| OpenCode | `error.name` and `error.data.message` on the message row | per turn |

Measured against real transcripts, a session that ever logs one of these keeps
going and succeeds afterward far more often than not: every sampled Claude Code
session with a recorded API error had thousands of lines of ordinary work
after it. So the question worth answering is never "did this ever fail",
only "is the newest turn a failure with nothing after it", which is read the
same way busy and idle are: from the end of the log, stopping at the first
turn that counts. A recovered error is not reported at all.

Codex writes the failure as two adjacent lines rather than one, a standalone
`error` event immediately followed by the `task_complete` that closes the
turn, with the error only sometimes carried inside the second. Reading the
newest `task_complete` and, when it has no error of its own, the line
immediately before it, covers both shapes: measured across the sessions this
was built against, the two are always adjacent, with nothing else written
between them.

Claude Code also marks a subagent turn as its own, separate entry
(`isSidechain: true`). An error there belongs to the Task tool call that ran
it, not to the conversation, and is skipped when looking for the newest turn.

**What this cannot see.** All three shapes belong to the agent's own request
path. A session pointed at a custom `ANTHROPIC_BASE_URL` or an equivalent
proxy can fail in a way the agent never wraps in its usual error format at
all: verified against a live session routed through a local gateway that had
just hit a rate limit, the transcript recorded nothing whatsoever, not even a
malformed attempt, and the session simply reported itself idle. Where the
failure happens outside the agent's own view of the request, it leaves nothing
to read.

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

### What can be removed, and what never is

A session leaves more behind than it needs to keep. `agent-ps prune` removes
the parts that are not the conversation, which each backend names for itself:

| Agent | Removable | Kept |
|---|---|---|
| Claude Code | subagent transcripts, file history, tasks, session env | transcript |
| CommandCode | checkpoints, file history | transcript, metadata |
| Codex CLI | shell snapshot | transcript, writer lock |
| Hermes | request dumps | the conversation, which is rows |
| Pi | nothing, it writes only a transcript | transcript |
| OpenCode, Copilot, Antigravity | nothing | everything |

The last row is the rule that shapes the feature. Their sessions are rows in a
database the agent may have open, so removing one means writing to it, and
reading everything read only is worth more than a cleaner. A backend that names
nothing is never even walked.

A session with a running process is never touched, whatever its age. What it
left is still in use: for Claude Code the file history is what `/rewind`
reaches for. The writer lock is left for the same reason, being how Codex knows
whether something else holds the session.

The header shows both the total for live sessions and everything on disk, and the
gap between them is usually large. Logs outlive the process that wrote them, so a
machine with a few hundred megabytes of live sessions can be holding several
gigabytes of finished ones. Press `e` to see which sessions those bytes belong
to, then sort by DISK.

Sizes are re-read at most every five seconds, and the totals on their own slower
schedule, since walking the directories is the one expensive thing here.
