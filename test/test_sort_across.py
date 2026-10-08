from types import SimpleNamespace

from timemachine import Archivary


def make_archivary(collection_list):
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = collection_list
    return a


# GDTape.collection is a list of archive.org collections; LocalTape.collection is the folder name.
ia1 = SimpleNamespace(collection=["GratefulDead", "etree"], name="ia1")
ia2 = SimpleNamespace(collection=["GratefulDead", "etree"], name="ia2")
loc = SimpleNamespace(collection="GratefulDead", name="loc")


def names(tapes):
    return [t.name for t in tapes]


def test_same_name_local_and_ia_not_duplicated():
    a = make_archivary(["GratefulDead", "Local_GratefulDead"])
    assert names(a.sort_across_collection([ia1, ia2, loc])) == ["ia1", "loc", "ia2"]


def test_collection_order_respected():
    a = make_archivary(["Local_GratefulDead", "GratefulDead"])
    assert names(a.sort_across_collection([ia1, ia2, loc])) == ["loc", "ia1", "ia2"]


def test_local_name_substring_of_ia_name():
    a = make_archivary(["GooseBand", "Local_Goose"])
    ia = SimpleNamespace(collection=["GooseBand"], name="ia")
    local = SimpleNamespace(collection="Goose", name="local")
    assert names(a.sort_across_collection([ia, local])) == ["ia", "local"]


def test_unmatched_tapes_kept_at_end():
    a = make_archivary(["GratefulDead"])
    stray = SimpleNamespace(collection=["SomethingElse"], name="stray")
    assert names(a.sort_across_collection([stray, ia1])) == ["ia1", "stray"]


def test_plex_and_phish():
    a = make_archivary(["Phish", "Plex_home_Live Music"])
    phish = SimpleNamespace(collection=["Phish"], name="phish")
    plex = SimpleNamespace(collection=["Plex_home_Live Music"], name="plex")
    assert names(a.sort_across_collection([phish, plex])) == ["phish", "plex"]
