import json
import os
from types import SimpleNamespace

import pytest

from timemachine import Archivary, config
from timemachine.tapedb import TapeDB

config.optd = config.default_options()


def raw_tape(identifier, date, collection=("GratefulDead", "etree"), addeddate="2010-01-01T00:00:00Z", downloads=100):
    return {
        "identifier": identifier,
        "date": f"{date}T00:00:00Z",
        "collection": list(collection),
        "addeddate": addeddate,
        "downloads": downloads,
        "num_reviews": 1,
        "avg_rating": 3,
        "format": ["VBR MP3"],
    }


TAPES_1970 = [
    raw_tape("gd73-12-19.aud.smith", "1973-12-19", downloads=10),
    raw_tape("gd73-12-19.sbd.miller", "1973-12-19", downloads=10),  # FAVORED_TAPER miller wins
    raw_tape("gd77-05-08.sbd.hicks", "1977-05-08", addeddate="2015-06-01T00:00:00Z", collection=("GratefulDead", "fav-someone")),
    raw_tape("jgb77-05-09.sbd", "1977-05-09", collection=("JerryGarcia", )),
]


def write_ids(iddir, period, tapes):
    os.makedirs(iddir, exist_ok=True)
    path = os.path.join(iddir, f"ids_{period}.json")
    with open(path, "w") as f:
        json.dump(tapes, f)
    return path


def test_tapedb_sync_and_query(tmp_path):
    iddir = str(tmp_path / "GratefulDead_ids")
    write_ids(iddir, 1970, TAPES_1970)
    db = TapeDB(str(tmp_path / "tapes.sqlite"))

    assert db.sync(iddir) == 1
    assert db.sync(iddir) == 0  # unchanged files are not indexed again
    assert db.dates(["GratefulDead"]) == ["1973-12-19", "1977-05-08"]
    assert db.dates(["GratefulDead", "JerryGarcia"]) == ["1973-12-19", "1977-05-08", "1977-05-09"]
    assert db.dates(["GratefulDead"], years=range(1975, 1980)) == ["1977-05-08"]
    assert db.dates(["fav-someone"]) == []  # per-user favorites lists are not indexed
    assert [t["identifier"] for t in db.tapes_on("1973-12-19", ["GratefulDead"])] == [
        "gd73-12-19.aud.smith",
        "gd73-12-19.sbd.miller",
    ]
    assert db.tapes_on("1977-05-09", ["GratefulDead"]) == []
    assert db.count(["GratefulDead"]) == 3
    assert db.max_addeddate(iddir) == "2015-06-01T00:00:00Z"
    assert db.max_addeddate(str(tmp_path / "Other_ids")) is None


def test_tapedb_resync_changed_file(tmp_path):
    iddir = str(tmp_path / "GratefulDead_ids")
    path = write_ids(iddir, 1970, TAPES_1970)
    db = TapeDB(str(tmp_path / "tapes.sqlite"))
    db.sync(iddir)

    # a tape changes collections and a new one is added, as when the updater rewrites a period file
    moved = raw_tape("gd73-12-19.aud.smith", "1973-12-19", collection=("SomethingElse", ))
    new = raw_tape("gd74-02-24.sbd", "1974-02-24")
    write_ids(iddir, 1970, [moved, new] + TAPES_1970[1:])
    os.utime(path, ns=(1, 1))  # make sure the mtime changes even on coarse filesystems
    assert db.sync(iddir) == 1
    assert db.dates(["GratefulDead"]) == ["1973-12-19", "1974-02-24", "1977-05-08"]
    assert [t["identifier"] for t in db.tapes_on("1973-12-19", ["GratefulDead"])] == ["gd73-12-19.sbd.miller"]

    db.reset()
    assert db.dates(["GratefulDead"]) == []
    assert db.sync(iddir) == 1
    assert db.count(["GratefulDead"]) == 3


def make_gd_archive(tmp_path, tapes=TAPES_1970, **kwargs):
    write_ids(str(tmp_path / "GratefulDead_ids"), 1970, tapes)
    return Archivary.GDArchive(dbpath=str(tmp_path), collection_list=["GratefulDead"], **kwargs)


def test_gdarchive_from_index(tmp_path):
    a = make_gd_archive(tmp_path)
    assert a.dates == ["1973-12-19", "1977-05-08"]
    assert "1973-12-19" in a.tape_dates
    assert "1977-05-09" not in a.tape_dates.keys()
    assert [t.identifier for t in a.tape_dates["1973-12-19"]] == ["gd73-12-19.sbd.miller", "gd73-12-19.aud.smith"]
    assert a.best_tape("1973-12-19", resort=False).identifier == "gd73-12-19.sbd.miller"
    assert a.tape_dates["1973-12-19"] is a.tape_dates["1973-12-19"]  # same tape objects while the date is in use
    with pytest.raises(KeyError):
        a.tape_dates["1977-05-09"]
    assert a.n_tapes() == 3
    assert "3 tapes on 2 dates" in repr(a)
    assert a.tape_dates["1977-05-08"][0].artist == "GratefulDead"


def test_gdarchive_date_range(tmp_path):
    # the range must include 1970: the period files are named by decade, and with no file in the range
    # load_current_tapes downloads from archive.org
    a = make_gd_archive(tmp_path, date_range=[1970, 1975])
    assert a.dates == ["1973-12-19"]
    assert "1977-05-08" not in a.tape_dates


def test_archivary_single_and_merged(tmp_path):
    gd = make_gd_archive(tmp_path)
    local_tape = SimpleNamespace(
        collection="GratefulDead", identifier="local-73-12-19", artist="GratefulDead", filler=lambda: False
    )
    local = SimpleNamespace(
        tape_dates={"1973-12-19": [local_tape], "1980-01-01": [local_tape]},
        dates=["1973-12-19", "1980-01-01"],
        get_tape_dates=lambda: None,
    )

    single = Archivary.Archivary.__new__(Archivary.Archivary)
    single.collection_list = ["GratefulDead"]
    single.archives = [gd]
    assert single.get_tape_dates() is gd.tape_dates

    merged = Archivary.Archivary.__new__(Archivary.Archivary)
    merged.collection_list = ["Local_GratefulDead", "GratefulDead"]
    merged.archives = [gd, local]
    td = merged.get_tape_dates()
    assert list(td.keys()) == ["1973-12-19", "1977-05-08", "1980-01-01"]
    assert [t.identifier for t in td["1973-12-19"]] == ["local-73-12-19", "gd73-12-19.sbd.miller", "gd73-12-19.aud.smith"]
    assert len(gd.tape_dates["1973-12-19"]) == 2  # merging does not add other archives' tapes to the GD archive
    assert "1977-05-09" not in td
    with pytest.raises(KeyError):
        td["1977-05-09"]
