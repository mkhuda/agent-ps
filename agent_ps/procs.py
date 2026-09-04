"""The process table, and stopping things in it."""

import os
import re
import signal
import sys
import time

from .util import parse_elapsed, run

SELF_PIDS = {os.getpid(), os.getppid()}

#: Programs that are never a session. Matched on the executable only, so a
#: session that merely mentions one of these in a prompt survives.
SKIP = ("agent-ps", "grep", "lsof")

#: Helpers launched through an interpreter, where the executable is `node` or
#: `python` and the name to look for is an argument. Matched against path
#: arguments as well, which is a looser test and so kept to what needs it.
SKIP_SCRIPTS = ("statusline",)


def table():
    """Every process on the machine, as plain dicts. One ps call."""
    out = run(["ps", "-axo", "pid=,ppid=,etime=,pcpu=,rss=,command="])
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, etime, pcpu, rss, cmd = parts
        if int(pid) in SELF_PIDS or is_tool(cmd, SKIP, SKIP_SCRIPTS):
            continue
        procs.append({
            "pid": int(pid),
            "ppid": int(ppid),
            "cmd": cmd.strip(),
            "uptime": parse_elapsed(etime),
            "cpu": float(pcpu),
            "rss": int(rss),
        })
    return procs


def drop_shims(procs):
    """Remove launcher processes that only exist to exec the real one.

    Version managers put a shim on PATH which spawns the actual interpreter
    under the same name, so one session shows up twice. The shim is always the
    parent and always carries an identical command line, which is what separates
    it from a supervisor: those spawn helpers with different arguments, so a
    daemon and its workers are left alone.
    """
    by_parent = {}
    for proc in procs:
        by_parent.setdefault(proc["ppid"], []).append(proc)
    return [p for p in procs
            if not any(c["cmd"] == p["cmd"] for c in by_parent.get(p["pid"], []))]


def is_tool(cmd, programs, scripts=()):
    """Whether a command line runs one of these.

    `programs` is checked against the executable alone, so a session whose
    prompt happens to contain the word `grep` is not mistaken for grep itself.
    `scripts` is also checked against path arguments, since a helper run as
    `node .../statusline.js` names itself there rather than in the executable.
    """
    tokens = cmd.split()
    if not tokens:
        return False
    if _stem(tokens[0]) in programs or _stem(tokens[0]) in scripts:
        return True
    return any(_stem(t) in scripts for t in tokens[1:] if "/" in t)


def _stem(token):
    return os.path.basename(token).split(".")[0]


def environment(pid, prefixes):
    """Environment variables of one process, filtered by prefix."""
    env = {}
    for token in run(["ps", "eww", "-p", str(pid)]).split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key.startswith(prefixes):
            env[key] = value
    return env


def working_dirs(pids):
    """Working directory of each PID, as one lookup rather than one per process.

    This is how a session is matched to a process when the agent does not record
    the pairing itself. Linux exposes it in /proc; macOS needs lsof, which is
    slow enough that asking once for every PID matters.
    """
    if not pids:
        return {}
    if sys.platform.startswith("linux"):
        found = {}
        for pid in pids:
            try:
                found[pid] = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                pass
        return found

    joined = ",".join(str(p) for p in pids)
    out = run(["lsof", "-a", "-d", "cwd", "-p", joined, "-Fpn"], timeout=10)
    found = {}
    current = None
    for line in out.splitlines():
        if line.startswith("p"):
            current = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and current is not None:
            found[current] = line[1:]
            current = None
    return found


def matches(cmd, patterns):
    """Whether a command line belongs to an agent.

    Matched on the executable rather than anywhere in the string, because short
    names like `pi` otherwise match any path that happens to contain them.
    """
    for pattern in patterns:
        if re.search(r"(^|[/\s])" + re.escape(pattern) + r"(\s|$)", cmd):
            return True
    return False


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def children_of(procs):
    tree = {}
    for proc in procs:
        tree.setdefault(proc["ppid"], []).append(proc)
    return tree


def collect_tree(pid, tree, seen=None):
    """PIDs of a process and its descendants, deepest first."""
    if seen is None:
        seen = []
    for child in tree.get(pid, []):
        collect_tree(child["pid"], tree, seen)
    if pid not in seen:
        seen.append(pid)
    return seen


def terminate(pid, grace=1.0):
    """SIGTERM, a moment to exit cleanly, then SIGKILL."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.time() + grace
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return not alive(pid)


def stop_tree(pid, procs, dry_run=False):
    order = collect_tree(pid, children_of(procs))
    if dry_run:
        return order, []
    return order, [p for p in order if terminate(p)]
