"""Command line entry point."""

import argparse
import curses
import json
import sys

from . import VERSION, backends
from .collect import Snapshot, idle_seconds, is_live
from .procs import stop_tree, terminate, table
from .resume import open_in_terminal, resume_command
from .ui import Tui, matches_filter, print_table
from .util import FAILURES, human_bytes


class UnknownAgent(Exception):
    pass


def build(args):
    known = backends.names()
    only = None
    if getattr(args, "agent", ""):
        only = set(args.agent.split(","))
        unknown = sorted(only - set(known))
        if unknown:
            raise UnknownAgent(f"Unknown agent(s): {', '.join(unknown)}. "
                               f"Known agents: {', '.join(known)}.")
    chosen = backends.available(only)
    if not chosen:
        print(f"No agent data found. Known agents: {', '.join(known)}.",
              file=sys.stderr)
    return Snapshot(chosen)


def select(snapshot, args, show_ended):
    rows = snapshot.rows(show_ended=show_ended, limit=getattr(args, "limit", 40))
    needle = (getattr(args, "filter", "") or "").lower()
    return [r for r in rows if matches_filter(r, needle)] if needle else rows


def cmd_list(args):
    snapshot = build(args)
    rows = select(snapshot, args, getattr(args, "all", False))

    if args.json:
        for row in rows:
            row["idle"] = idle_seconds(row)
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        if FAILURES:
            print(f"Could not run {', '.join(sorted(FAILURES))}, so the table "
                  f"could not be built.", file=sys.stderr)
            return 1
        print("No coding agent sessions running.")
        return 0

    print_table(rows)
    live = [r for r in rows if is_live(r)]
    disk = sum(r.get("disk", 0) for r in rows)
    history = snapshot.history(force=True)
    agents = sorted({r["agent"] for r in live})
    print(f"\n{len(live)} running ({', '.join(agents) or 'none'}), "
          f"{human_bytes(disk)} on disk. "
          f"{history['ended']} session{'' if history['ended'] == 1 else 's'} "
          f"on record, "
          f"{human_bytes(max(0, history['bytes'] - disk))} in history.")
    return 0


def cmd_stop(args):
    snapshot = build(args)
    # one process table for both the check and the stop, so the tree that gets
    # signalled is the one that was just validated
    procs = table()
    row = next((r for r in snapshot.rows() if r["pid"] == args.pid), None)
    if not row:
        print(f"PID {args.pid} is not a coding agent process.", file=sys.stderr)
        return 1
    order, stopped = stop_tree(args.pid, procs, args.dry_run)
    if args.dry_run:
        print("Would stop:", ", ".join(str(p) for p in order))
        return 0
    print(f"Stopped {len(stopped)} of {len(order)} processes.")
    return 0 if len(stopped) == len(order) else 1


def cmd_resume(args):
    snapshot = build(args)
    rows = snapshot.rows(show_ended=True, limit=1000)
    match = next((r for r in rows
                  if not r["pid"] and r["session_id"].startswith(args.session)), None)
    if not match:
        print(f"No ended session starts with {args.session}.", file=sys.stderr)
        return 1
    backend = snapshot.find_backend(match["agent"])
    if not backend or not backend.resume_binary:
        print(f"{match['agent']} sessions cannot be reopened from a terminal.",
              file=sys.stderr)
        return 1
    command = resume_command(backend, match["session_id"], match["cwd"])
    if args.print_only:
        print(command)
        return 0
    ok, note = open_in_terminal(command)
    print(note if ok else f"{note}\nRun: {command}")
    return 0 if ok else 1


def cmd_stop_background(args):
    snapshot = build(args)
    targets = [r for r in snapshot.rows() if r["background"]]
    if not targets:
        print("No background processes running.")
        return 0
    if args.dry_run:
        print("Would stop:", ", ".join(str(r["pid"]) for r in targets))
        return 0
    stopped = [r for r in targets if terminate(r["pid"])]
    print(f"Stopped {len(stopped)} of {len(targets)} background processes.")
    return 0 if len(stopped) == len(targets) else 1


def cmd_agents(args):
    for cls in backends.ALL:
        backend = cls()
        if not backend.available():
            print(f"  {backend.name:<13} present=no   sessions=-      {backend.root}")
            continue
        count, _ = backend.history_counts(0)
        print(f"  {backend.name:<13} present=yes  sessions={count:<6} {backend.root}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="agent-ps", description="List and stop coding agent sessions.")
    parser.add_argument("--version", action="version", version=f"agent-ps {VERSION}")
    parser.add_argument("--agent", default="",
                        help="limit to these agents, comma separated")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="print the table and exit")
    p_list.add_argument("--json", action="store_true", help="output as JSON")
    p_list.add_argument("--all", action="store_true",
                        help="include sessions that have ended")
    p_list.add_argument("--filter", metavar="TEXT",
                        help="match session, title, agent, model, directory, or PID")
    p_list.add_argument("--limit", type=int, default=40,
                        help="how many ended sessions to show (default 40)")
    p_list.set_defaults(func=cmd_list)

    p_stop = sub.add_parser("stop", help="stop a process and its children")
    p_stop.add_argument("pid", type=int)
    p_stop.add_argument("--dry-run", action="store_true")
    p_stop.set_defaults(func=cmd_stop)

    p_resume = sub.add_parser("resume", help="reopen an ended session")
    p_resume.add_argument("session", help="session id, or a unique prefix")
    p_resume.add_argument("--print", dest="print_only", action="store_true",
                          help="print the command instead of running it")
    p_resume.set_defaults(func=cmd_resume)

    p_bg = sub.add_parser("stop-background", help="stop daemons and warm spares")
    p_bg.add_argument("--dry-run", action="store_true")
    p_bg.set_defaults(func=cmd_stop_background)

    p_agents = sub.add_parser("agents", help="show which agents were found")
    p_agents.set_defaults(func=cmd_agents)

    args = parser.parse_args()
    try:
        if args.command:
            return args.func(args)
    except UnknownAgent as error:
        print(error, file=sys.stderr)
        return 2

    if not sys.stdout.isatty():
        args.json = args.all = False
        args.filter, args.limit = "", 40
        return cmd_list(args)
    try:
        snapshot = build(args)
    except UnknownAgent as error:
        print(error, file=sys.stderr)
        return 2
    try:
        curses.wrapper(lambda screen: Tui(screen, snapshot).loop())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
