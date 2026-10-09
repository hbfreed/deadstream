import csv
import json
import os
from collections import defaultdict
from types import SimpleNamespace

from timemachine import Archivary, config, utils
from timemachine.setbreakdb import SetBreakDB


def test_set_break_index_matches_every_bundled_show(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path))
    source = utils.resource_path("timemachine.metadata", "set_breaks.csv")
    expected = defaultdict(list)
    with open(source, encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            expected[raw["artist"], raw["date"]].append(Archivary.GDSet_row(raw))
    indexed = Archivary.GDSetBreaks(["GratefulDead"])
    for (artist, date), rows in expected.items():
        assert vars(indexed.get_date(artist, date)) == vars(Archivary.GDDate_info(rows))
    assert indexed.get_date("missing", "1900-01-01").n_sets == 0
    artist = "GratefulDead"
    assert set(indexed.get_artist_set_dict(artist)) == {d for a, d in expected if a == artist}


def test_set_break_index_rebuilds_only_when_source_changes(tmp_path):
    source = tmp_path / "breaks.csv"
    source.write_text("artist,date,song\nBand,2000-01-01,First\nBand,2000-01-01,Second\n")
    path = tmp_path / "breaks.sqlite"
    db = SetBreakDB(str(path), str(source))
    assert [r["song"] for r in db.rows("Band", "2000-01-01")] == ["First", "Second"]
    before = path.stat().st_mtime_ns
    SetBreakDB(str(path), str(source))
    assert path.stat().st_mtime_ns == before
    source.write_text("artist,date,song\nOther,2001-01-01,Replacement\n")
    db = SetBreakDB(str(path), str(source))
    assert db.rows("Band") == []
    assert db.rows("Other")[0]["song"] == "Replacement"


def test_date_cache_evicts_oldest_but_preserves_active_reference():
    dates = [str(i) for i in range(20)]
    cache = Archivary.LazyTapeDates(dates, lambda date: [object()])
    active = cache["0"]
    for date in dates[1:]:
        cache[date]
    assert len(cache._cache) == 16
    assert cache["19"] is cache["19"]
    assert cache["0"] is not active
    assert len(active) == 1


def metadata_tape(tmp_path):
    tape = Archivary.GDTape.__new__(Archivary.GDTape)
    tape.meta_loaded = False
    tape.meta_path = str(tmp_path / "tape.json")
    tape.url_metadata = "https://example.invalid/metadata/tape"
    tape.reorder_tracks = lambda _: None
    tape.insert_breaks = lambda: None
    return tape


def test_cached_metadata_is_not_rewritten(tmp_path, monkeypatch):
    tape = metadata_tape(tmp_path)
    payload = {"files": [], "metadata": {"venue": "Venue", "coverage": "City"}}
    with open(tape.meta_path, "w") as f:
        json.dump(payload, f)
    os.utime(tape.meta_path, ns=(1, 1))
    monkeypatch.setattr(Archivary.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    tape.get_metadata(only_if_cached=True)
    assert tape.meta_loaded
    assert tape.venue_name == "Venue"
    assert os.stat(tape.meta_path).st_mtime_ns == 1


def test_corrupt_cache_is_repaired_only_when_selected(tmp_path, monkeypatch):
    tape = metadata_tape(tmp_path)
    with open(tape.meta_path, "w") as f:
        f.write("broken json")
    calls = []
    payload = {"files": [], "metadata": {"venue": "Venue", "coverage": "City"}}
    def get(url):
        calls.append(url)
        return SimpleNamespace(status_code=200, url=url, json=lambda: payload)
    monkeypatch.setattr(Archivary.requests, "get", get)
    tape.get_metadata(only_if_cached=True)
    assert calls == []
    assert not tape.meta_loaded
    tape.get_metadata()
    assert len(calls) == 1
    assert tape.meta_loaded
    with open(tape.meta_path) as f:
        assert json.load(f) == payload


def test_state_reads_playlist_once_and_handles_no_current_track():
    # Load this hardware-independent method without importing GPIO/display drivers.
    import ast
    from pathlib import Path
    tree = ast.parse(Path("timemachine/controls.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "state")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "get_current")
    module = ast.Module(body=[method], type_ignores=[])
    class Player:
        reads = 0
        tape = SimpleNamespace(identifier="tape", venue=lambda: "Venue", tracks=lambda: [SimpleNamespace(title="First")])
        def get_prop(self, name):
            return 80
        def _get_property(self, name):
            return -1
        @property
        def playlist(self):
            self.reads += 1
            return [{}]
    scope = {"config": SimpleNamespace(PLAY_STATE=1)}
    exec(compile(module, "controls.py", "exec"), scope)
    player = Player()
    state = SimpleNamespace(module_name="config", player=player,
                            date_reader=SimpleNamespace(_update=lambda: None, date="1977-05-08"))
    current = scope["get_current"](state)
    assert player.reads == 1
    assert current["TRACK_TITLE"] == ""
    assert current["NEXT_TRACK_TITLE"] == "First"
