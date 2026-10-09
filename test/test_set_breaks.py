from types import SimpleNamespace

import pytest

from timemachine import Archivary, config


def tape_with_breaks():
    tape = Archivary.GDTape.__new__(Archivary.GDTape)
    tape.meta_loaded = True
    tape._breaks_added = False
    tape._tracks = [SimpleNamespace(title=t) for t in ["Bertha", "Loser", "Playin'", "Morning Dew", "U.S. Blues"]]
    tape._compute_breaks = lambda: {"long": [2], "short": [4]}
    return tape


@pytest.mark.parametrize("set_breaks, titles", [
    (True, ["Bertha", "Loser", "Set Break", "Playin'", "Morning Dew", "Encore Break", "U.S. Blues"]),
    (False, ["Bertha", "Loser", "Playin'", "Morning Dew", "Encore Break", "U.S. Blues"]),
])
def test_set_breaks_option(monkeypatch, set_breaks, titles):
    monkeypatch.setattr(config, "optd", {**config.default_options(), "SET_BREAKS": set_breaks})
    tape = tape_with_breaks()
    tape.insert_breaks()
    assert [t.title for t in tape._tracks] == titles
