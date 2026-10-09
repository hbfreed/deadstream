import base64
import os
import threading
import time

from timemachine import airplay


def item(type_, code, data=None):
    hexed = type_.encode().hex() + "</type><code>" + code.encode().hex()
    if data is None:
        return f"<item><type>{hexed}</code><length>0</length></item>\n"
    b64 = base64.b64encode(data.encode()).decode()
    return f'<item><type>{hexed}</code><length>{len(data)}</length>\n<data encoding="base64">\n{b64}</data></item>\n'


def session_text(title="Ripple", artist="Grateful Dead", album="American Beauty"):
    return (
        item("ssnc", "pbeg") + item("ssnc", "mdst") + item("core", "minm", title) + item("core", "asar", artist)
        + item("core", "asal", album) + item("ssnc", "mden")
    )


def test_parse_items_keeps_partial_item():
    text = session_text()
    items, rest = airplay.parse_items(text[:-30])
    assert items[0] == ("ssnc", "pbeg", b"")
    assert ("core", "minm", b"Ripple") in items
    assert rest != ""
    items2, rest2 = airplay.parse_items(rest + text[-30:])
    assert items2 == [("ssnc", "mden", b"")] and rest2.strip() == ""


def test_session_callbacks():
    events = []
    ap = airplay.AirPlay(
        on_start=lambda: events.append("start"),
        on_stop=lambda: events.append("stop"),
        on_track=lambda t, a, al: events.append(("track", t, a, al)),
    )
    items, _ = airplay.parse_items(session_text() + item("ssnc", "pend"))
    for i in items:
        ap.handle(*i)
    assert events == ["start", ("track", "Ripple", "Grateful Dead", "American Beauty"), "stop"]
    assert not ap.active


def test_drop_session_ends_at_once(monkeypatch):
    calls = []
    monkeypatch.setattr(airplay, "drop_session", lambda: calls.append("dbus"))
    events = []
    ap = airplay.AirPlay(on_stop=lambda: events.append("stop"))
    ap.handle("ssnc", "pbeg", b"")
    ap.drop_session()
    time.sleep(0.1)
    assert not ap.active and events == ["stop"] and calls == ["dbus"]
    ap.handle("ssnc", "pend", b"")  # shairport-sync's own end of session afterwards: no second stop
    assert events == ["stop"]


def test_reads_pipe(tmp_path):
    pipe = str(tmp_path / "metadata")
    os.mkfifo(pipe)
    got = []
    done = threading.Event()

    def on_track(t, a, al):
        got.append((t, a))
        done.set()

    ap = airplay.AirPlay(on_track=on_track, pipe_path=pipe)
    ap.start()
    with open(pipe, "w") as f:
        f.write(session_text(title="Box of Rain"))
    assert done.wait(5)
    assert got == [("Box of Rain", "Grateful Dead")] and ap.active
