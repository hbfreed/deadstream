from types import SimpleNamespace

from timemachine import Archivary


def tape(collection, name):
    return SimpleNamespace(collection=collection, name=name, filler=lambda: False)


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
    # one artist: the order of the collections is the order of preference
    a = make_archivary(["GratefulDead", "Local_GratefulDead"])
    assert names(a.sort_across_collection([ia1, ia2, loc])) == ["ia1", "ia2", "loc"]


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


def test_artists_alternate():
    a = make_archivary(["GratefulDead", "JerryGarcia"])
    jg1, jg2 = tape(["JerryGarcia"], "jg1"), tape(["JerryGarcia"], "jg2")
    assert names(a.sort_across_collection([ia1, ia2, jg1, jg2])) == ["ia1", "jg1", "ia2", "jg2"]


def test_collection_artist():
    assert Archivary.collection_artist("Local_GratefulDead") == "GratefulDead"
    assert Archivary.collection_artist("Nugs_GooseBand") == "GooseBand"
    assert Archivary.collection_artist("GooseBand") == "GooseBand"
    assert Archivary.collection_artist("Plex_home_Live Music") == "Plex_home_Live Music"


def test_tape_filed_under_another_band():
    """archive.org files Orebolo shows in the GooseBand collection; the identifier names the band"""
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = ["GooseBand", "Local_Orebolo", "Nugs_Orebolo"]
    ia_orebolo = SimpleNamespace(collection=["GooseBand"], identifier="orebolo2022-09-07.akg451.flac16", name="ia-orebolo", filler=lambda: False)
    ia_goose = SimpleNamespace(collection=["GooseBand"], identifier="goose2022-09-07.sbd", name="ia-goose", filler=lambda: False)
    local = SimpleNamespace(collection="Orebolo", identifier="/archive/Orebolo/shows/2022-09-07-Levitt", name="local", filler=lambda: False)
    nugs = SimpleNamespace(collection=["Nugs_Orebolo"], identifier="nugs-29978 9-7-2022", name="nugs", filler=lambda: False)
    names = [t.name for t in a.sort_across_collection([ia_orebolo, nugs, local, ia_goose])]
    assert names == ["ia-goose", "local", "nugs", "ia-orebolo"]
