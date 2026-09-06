"""Turning backends into one table.

Every backend answers about its own agent. This is where those answers are put
side by side, and it is the only place that knows more than one agent exists.
"""

import time

from . import procs as proc_table
from .backends.base import KIND_ENDED

#: How many recent sessions per agent are considered when pairing processes.
#: A session with a live process was active moments ago, so the newest few
#: hundred always cover it, and older logs are never parsed to find one.
ATTACH_WINDOW = 200

HISTORY_POLL_SECONDS = 30.0


class Snapshot:
    """One pass over everything, with per-backend caches kept between passes."""

    def __init__(self, backends):
        self.backends = backends
        self._history = {"bytes": 0, "ended": 0, "recent": 0, "spare": 0,
                         "spare_sessions": 0}
        self._history_at = 0.0
        #: Sessions seen with a process on the last pass, so the reclaimable
        #: total can leave out what prune would refuse to touch.
        self._alive = set()

    def rows(self, show_ended=False, limit=40):
        table = proc_table.table()
        taken = set()
        running = []
        ended = []
        alive = set()

        for backend in self.backends:
            mine = proc_table.drop_shims(
                [p for p in table if p["pid"] not in taken and backend.owns(p)])
            taken.update(p["pid"] for p in mine)

            recent = backend.sessions(limit=ATTACH_WINDOW)
            index = {}
            for session in recent:
                index.setdefault(session["cwd"], []).append(session)

            attached = backend.attach(mine, index) if mine else []
            hosted = backend.live_sessions()
            running.extend(attached)
            running.extend(hosted)

            live = {r["session_id"] for r in attached + hosted if r["session_id"]}
            # remembered even when ended rows are not asked for, because the
            # reclaimable total has to leave out whatever prune will refuse
            alive.update((backend.name, sid) for sid in live)

            if show_ended:
                for session in recent:
                    if session["session_id"] in live:
                        continue
                    session["kind"] = KIND_ENDED
                    ended.append(session)

        self._alive = alive
        ended.sort(key=lambda r: r["last_active"], reverse=True)
        return running + ended[:limit]

    def history(self, force=False):
        """What finished sessions are costing on disk, refreshed slowly.

        Walking the log directories is the one expensive call here, and the
        answer moves by megabytes an hour, so it runs on its own schedule.
        """
        now = time.time()
        if not force and now - self._history_at < HISTORY_POLL_SECONDS:
            return self._history
        week_ago = now - 7 * 86400
        total = ended = recent = 0
        for backend in self.backends:
            total += backend.history_bytes()
            count, fresh = backend.history_counts(week_ago)
            ended += count
            recent += fresh
        spare, spare_sessions = self.reclaimable(week_ago)
        self._history = {"bytes": total, "ended": ended, "recent": recent,
                         "spare": spare, "spare_sessions": spare_sessions}
        self._history_at = now
        return self._history

    def reclaimable(self, before):
        """What ended sessions are holding that is not their conversation.

        Per session rather than per directory, because the age and whether a
        process is running are session facts. That costs a tenth of a second
        against the four hundredths the rest of this takes, which is why it
        rides the slow poll rather than the refresh.
        """
        total = sessions = 0
        for backend in self.backends:
            if not backend.prunable:
                continue
            for row in backend.sessions():
                if not row["session_id"] or row["last_active"] > before:
                    continue
                if (backend.name, row["session_id"]) in self._alive:
                    continue  # prune leaves a running session alone
                parts = backend.prune_paths(row["session_id"], row.get("path", ""))
                if parts:
                    sessions += 1
                    total += sum(size for _, _, size in parts)
        return total, sessions

    def find_backend(self, name):
        return next((b for b in self.backends if b.name == name), None)


def is_live(row):
    """Whether a session is still open.

    Not the same as having a process. An agent embedded in an editor is running
    without one, so a PID is evidence of life rather than the definition of it.
    """
    return row["kind"] != KIND_ENDED


def idle_seconds(row):
    """How long since the session last wrote to its log.

    Every turn appends, so the modification time is when the session was last
    used. That is a different question from uptime: a process can be five days
    old and have answered a minute ago.
    """
    last = row.get("last_active", 0)
    return int(time.time() - last) if last else 0
