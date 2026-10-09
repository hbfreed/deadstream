#!/usr/bin/python3
"""
Grateful Dead Time Machine -- copyright 2021 Steve Eichblatt

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import json
import logging
import os
import sqlite3
from contextlib import closing

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tapes (
    identifier TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tapes_date ON tapes (date);
CREATE TABLE IF NOT EXISTS tape_collections (
    collection TEXT NOT NULL,
    identifier TEXT NOT NULL,
    PRIMARY KEY (collection, identifier)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS tape_collections_identifier ON tape_collections (identifier);
CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    max_addeddate TEXT
);
"""


def tape_date(raw):
    """The YYYY-MM-DD date of a tape, as GDTape computes it"""
    date = raw["date"]
    if isinstance(date, list):
        date = date[0]
    return date[:10]


class TapeDB:
    """SQLite index of archive.org tape metadata.

    The downloaders write the metadata of all tapes to ids_<period>.json files. Holding every tape
    of a big collection in memory takes far more RAM than a small device has, so the archive keeps
    only this index, and builds tape objects for a date when it is asked for them.

    sync() indexes the json files of a collection that changed since they were last indexed.
    """

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with closing(self.connect()) as con:
            con.executescript(SCHEMA)

    def connect(self):
        # A connection per call: the archive updater thread syncs while the main thread reads.
        return sqlite3.connect(self.path, timeout=30)

    def reset(self):
        """Delete the index. It is rebuilt from the json files on the next sync"""
        for suffix in ["", "-journal"]:
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)
        with closing(self.connect()) as con:
            con.executescript(SCHEMA)

    def _json_files(self, iddir):
        if not os.path.isdir(iddir):
            return {}
        files = {}
        for name in sorted(os.listdir(iddir)):
            if name.endswith(".json"):
                path = os.path.join(iddir, name)
                files[path] = os.stat(path)
        return files

    def sync(self, iddir):
        """Index the json files in iddir that are new or changed. Returns the number of files indexed"""
        files = self._json_files(iddir)
        n_indexed = 0
        with closing(self.connect()) as con:
            known = {
                path: (mtime_ns, size)
                for path, mtime_ns, size in con.execute("SELECT path, mtime_ns, size FROM sources")
                if os.path.dirname(path) == iddir
            }
            for path in set(known) - set(files):
                with con:
                    con.execute("DELETE FROM sources WHERE path = ?", (path, ))
            for path, st in files.items():
                if known.get(path) == (st.st_mtime_ns, st.st_size):
                    continue
                logger.info(f"Indexing tapes in {path}")
                with open(path, "r") as f:
                    tapes = json.load(f)
                with con:  # one transaction per file
                    self._insert(con, tapes)
                    max_addeddate = max([t["addeddate"] for t in tapes]) if len(tapes) > 0 else None
                    con.execute(
                        "INSERT OR REPLACE INTO sources (path, mtime_ns, size, max_addeddate) VALUES (?, ?, ?, ?)",
                        (path, st.st_mtime_ns, st.st_size, max_addeddate),
                    )
                del tapes
                n_indexed += 1
        return n_indexed

    def _insert(self, con, tapes):
        identifiers = [(t["identifier"], ) for t in tapes]
        con.executemany("DELETE FROM tape_collections WHERE identifier = ?", identifiers)
        con.executemany(
            "INSERT OR REPLACE INTO tapes (identifier, date, raw) VALUES (?, ?, ?)",
            ((t["identifier"], tape_date(t), json.dumps(t, separators=(",", ":"))) for t in tapes),
        )
        con.executemany(
            "INSERT OR IGNORE INTO tape_collections (collection, identifier) VALUES (?, ?)",
            ((c, t["identifier"]) for t in tapes for c in t["collection"] if not c.startswith("fav-")),
        )

    def max_addeddate(self, iddir):
        """The latest addeddate of the tapes in the json files of iddir, or None"""
        with closing(self.connect()) as con:
            rows = con.execute("SELECT path, max_addeddate FROM sources WHERE max_addeddate IS NOT NULL").fetchall()
        dates = [d for path, d in rows if os.path.dirname(path) == iddir]
        return max(dates) if len(dates) > 0 else None

    @staticmethod
    def _in_collections(collections):
        marks = ", ".join("?" * len(collections))
        return f"EXISTS (SELECT 1 FROM tape_collections c WHERE c.identifier = t.identifier AND c.collection IN ({marks}))"

    def dates(self, collections, years=None):
        """Sorted dates that have a tape in any of the collections, optionally only in the given years"""
        query = f"SELECT DISTINCT t.date FROM tapes t WHERE {self._in_collections(collections)}"
        params = list(collections)
        if years is not None:
            query += " AND t.date >= ? AND t.date < ?"
            params += [f"{min(years):04d}", f"{max(years) + 1:04d}"]
        query += " ORDER BY t.date"
        with closing(self.connect()) as con:
            dates = [row[0] for row in con.execute(query, params)]
        if years is not None:
            years = set(years)
            dates = [d for d in dates if int(d[:4]) in years]
        return dates

    def tapes_on(self, date, collections):
        """The raw metadata of the tapes on a date that are in any of the collections"""
        query = f"SELECT t.raw FROM tapes t WHERE t.date = ? AND {self._in_collections(collections)} ORDER BY t.identifier"
        with closing(self.connect()) as con:
            return [json.loads(row[0]) for row in con.execute(query, [date] + list(collections))]

    def count(self, collections):
        query = f"SELECT count(*) FROM tapes t WHERE {self._in_collections(collections)}"
        with closing(self.connect()) as con:
            return con.execute(query, list(collections)).fetchone()[0]
