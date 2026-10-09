import sys
import types
from types import SimpleNamespace

from timemachine import Archivary


def test_plugin_collections(monkeypatch):
    fake = types.ModuleType("timemachine.archive_fake")
    fake.make_archive = lambda collections, dbpath=None: None
    monkeypatch.setitem(sys.modules, "timemachine.archive_fake", fake)
    plugins = Archivary.plugin_collections(["GratefulDead", "Local_GratefulDead", "Plex_home_Music", "Fake_GooseBand", "Nope_X"])
    assert plugins == {fake: ["Fake_GooseBand"]}  # no module for Nope_: stays an archive.org collection


def test_plugin_tapes_between_local_and_archive():
    a = Archivary.Archivary.__new__(Archivary.Archivary)
    a.collection_list = ["Local_GooseBand", "Fake_GooseBand", "GooseBand"]
    ia = SimpleNamespace(collection=["GooseBand"], name="ia", source_tier=lambda: 2)
    plugin = SimpleNamespace(collection=["Fake_GooseBand"], name="plugin", source_tier=lambda: 1)
    local = SimpleNamespace(collection="GooseBand", name="local", source_tier=lambda: 0)
    assert [t.name for t in a.sort_across_collection([ia, plugin, local])] == ["local", "plugin", "ia"]
