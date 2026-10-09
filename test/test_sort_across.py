from types import SimpleNamespace

from timemachine import Archivary


def tape(collection, name, tier=1):
    return SimpleNamespace(collection=collection, name=name, source_tier=lambda: tier)


def make_archivary(collection_list):
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = collection_list
    return a


# GDTape.collection is a list of archive.org collections; LocalTape.collection is the folder name.
ia1 = tape(["GratefulDead", "etree"], "ia1")
ia2 = tape(["GratefulDead", "etree"], "ia2")
loc = tape("GratefulDead", "loc")


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
    ia = tape(["GooseBand"], "ia")
    local = tape("Goose", "local")
    assert names(a.sort_across_collection([ia, local])) == ["ia", "local"]


def test_unmatched_tapes_kept_at_end():
    a = make_archivary(["GratefulDead"])
    stray = tape(["SomethingElse"], "stray")
    assert names(a.sort_across_collection([stray, ia1])) == ["ia1", "stray"]


def test_plex_and_phish():
    a = make_archivary(["Phish", "Plex_home_Live Music"])
    phish = tape(["Phish"], "phish")
    plex = tape(["Plex_home_Live Music"], "plex")
    assert names(a.sort_across_collection([phish, plex])) == ["phish", "plex"]
