import json
import os
from types import SimpleNamespace

from timemachine import Archivary, config

config.optd = config.default_options()
SET_DATA = Archivary.GDSetBreaks(["GratefulDead"])


def local_tape(tmp_path, name, release=None, date="1973-12-19"):
    folder = tmp_path / "GratefulDead" / "official" / name
    os.makedirs(folder)
    meta = {"data": {"venue": {"venue_name": "Curtis Hixon Hall", "venue_location": "Tampa, FL"}, "tracks": []}}
    if release is not None:
        meta["release"] = release
    with open(folder / "metadata.json", "w") as f:
        json.dump(meta, f)
    tape = Archivary.LocalTape(str(tmp_path), {"date": date, "identifier": str(folder), "collection": "GratefulDead"}, SET_DATA)
    tape.name = name
    return tape


def ia_tape(name):
    return SimpleNamespace(collection=["GratefulDead", "etree"], name=name, filler=lambda: False)


def release(role, primary, complete):
    return {"title": "Dick's Picks Vol. 1", "short": "DP01", "role": role, "primary": primary, "complete": complete}


def test_official_and_partial_markers(tmp_path):
    assert local_tape(tmp_path, "a", release("show", True, True)).official() == "complete"
    assert local_tape(tmp_path, "b", release("show", True, False)).official() == "partial"
    assert local_tape(tmp_path, "c", release("show", True, None)).official() == "complete"  # archive.org has none of it
    assert local_tape(tmp_path, "d", release("whole", True, True)).official() == "complete"
    assert local_tape(tmp_path, "e").official() is None  # a plain local tape
    assert Archivary.BaseTape.official(ia_tape("x")) is None


def test_order_on_a_date(tmp_path):
    show = local_tape(tmp_path, "1973-12-19 Curtis Hixon Hall [DP01]", release("show", True, False))
    whole = local_tape(tmp_path, "1973-12-19 Curtis Hixon Hall [DP01 complete]", release("whole", True, True))
    fragment = local_tape(tmp_path, "1973-12-19 Curtis Hixon Hall [DaP99]", release("show", False, False))
    plain = local_tape(tmp_path, "1973-12-19 my own tape")
    ia1, ia2 = ia_tape("ia1"), ia_tape("ia2")

    assert show.compute_score() > whole.compute_score()  # the night's tracks before the whole release
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = ["Local_GratefulDead", "GratefulDead"]
    # tapes arrive grouped by archive, each archive in its own order (archive.org first, as in Archivary)
    ordered = a.sort_across_collection([ia1, ia2, show, whole, plain, fragment])
    assert [t.name for t in ordered] == [show.name, whole.name, plain.name, "ia1", "ia2", fragment.name]


def test_archive_only_date_unchanged(tmp_path):
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = ["Local_GratefulDead", "GratefulDead"]
    ia1, ia2 = ia_tape("ia1"), ia_tape("ia2")
    assert [t.name for t in a.sort_across_collection([ia1, ia2])] == ["ia1", "ia2"]
