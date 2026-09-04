"""Shared behaviour for agents that keep their sessions in SQLite.

Two of them do, and both are read while the agent is writing, so the connection
is opened read only and a locked or half written database costs a row of dashes
rather than an error.
"""

import os
import sqlite3

from .base import Backend, blank_row


class SqliteBackend(Backend):
    #: File inside `root` holding the sessions.
    db_filename = ""

    @property
    def database(self):
        path = os.path.join(self.root, self.db_filename)
        return path if os.path.exists(path) else ""

    def query(self, sql, args=()):
        if not self.database:
            return []
        connection = None
        try:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro",
                                         uri=True, timeout=1.0)
            connection.row_factory = sqlite3.Row
            return connection.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []
        finally:
            if connection is not None:
                connection.close()

    def rows_from(self, sql, limit, build):
        """Run a session query and turn each record into a row.

        `build` fills in the fields that differ between agents; everything a row
        always has is set here so the two backends cannot drift apart.
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = []
        for record in self.query(sql):
            row = blank_row(self.name)
            row["path"] = self.database
            fields = build(record)
            cwd = fields.get("cwd") or ""
            row.update(fields)
            row["name"] = os.path.basename(cwd) if cwd else ""
            rows.append(row)
        return rows
