"""On-disk index of the bundled set-break CSV, queried a show at a time."""

import csv
import json
import os
import sqlite3
from contextlib import closing


class SetBreakDB:
    def __init__(self, path, source):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        stat = os.stat(source)
        stamp = json.dumps([os.path.abspath(source), stat.st_mtime_ns, stat.st_size])
        with closing(self.connect()) as con, con:
            # Serialize rebuilds across processes, and commit the rows and stamp together.
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE IF NOT EXISTS source (stamp TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS breaks (artist TEXT, date TEXT, raw TEXT)")
            con.execute("CREATE INDEX IF NOT EXISTS breaks_date ON breaks (artist, date)")
            if con.execute("SELECT stamp FROM source").fetchone() == (stamp,):
                return
            con.execute("DELETE FROM breaks")
            with open(source, encoding="utf-8", newline="") as f:
                con.executemany(
                    "INSERT INTO breaks VALUES (?, ?, ?)",
                    ((row["artist"], row["date"], json.dumps(row)) for row in csv.DictReader(f)),
                )
            con.execute("DELETE FROM source")
            con.execute("INSERT INTO source VALUES (?)", (stamp,))

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def rows(self, artist, date=None):
        query = "SELECT raw FROM breaks WHERE artist = ?"
        args = [artist]
        if date is not None:
            query += " AND date = ?"
            args.append(date)
        # CSV order matters for shows with more than one location.
        query += " ORDER BY rowid"
        with closing(self.connect()) as con:
            return [json.loads(row[0]) for row in con.execute(query, args)]
