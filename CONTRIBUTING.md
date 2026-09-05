# Contributing

Issues and pull requests welcome. A new agent is one class and one line
in the registry, and nothing else has to change.

## Adding an agent

A backend answers questions about one agent and returns plain data. It never
draws anything.

```
agent_ps/
  __main__.py      the command line
  collect.py       merges backends into one table
  ui.py            curses view and columns
  procs.py         the process table, and stopping things in it
  jsonl.py         cached reading of session logs
  backends/
    base.py        the interface, and what most agents get for free
    claude.py      its own schema, and the only direct PID pairing
    pilike.py      Pi and CommandCode, one parser and two configurations
    codex.py       every record wrapped in a payload
    opencode.py    SQLite, with the old JSON tree as a fallback
    hermes.py      SQLite, and a gateway that outlives its sessions
    copilot.py     no process at all, and a journal that must be replayed
    antigravity.py protobuf blobs inside a database, read field by field
```

Most agents keep one JSONL log per session, so that is the default. A backend
sets a few attributes and overrides only what differs:

```python
class CodexBackend(Backend):
    name = "codex"
    root = os.path.expanduser("~/.codex")
    session_glob = "sessions/*/*/*/*.jsonl"
    process_patterns = ("codex",)
    resume_binary = "codex"
    resume_flag = "resume"

    def extract(self, reader):
        ...  # title, model, working directory
```

`extract` gets a reader that hands back the head and the tail of the file, both
parsed once and cached against modification time. Logs run to megabytes and the
interesting fields sit at the ends, so no log is ever read whole.

Returning a partial result is fine. Anything missing shows as a dash rather than
becoming a special case, which is why Pi keeping no titles and Copilot having no
process cost no code anywhere else.

Add the class to `backends/__init__.py` and it appears everywhere, colour
included, since the palette is assigned by position rather than by name.
