import os
import stat

import pytest

from timemachine import control


def test_commands_over_the_socket(tmp_path):
    path = str(tmp_path / "control.sock")
    played = []

    def play(date, recording=None):
        played.append((date, recording))
        return {"date": date}

    def boom():
        raise ValueError("no recordings on 1999-01-01")

    server = control.serve({"play": play, "boom": boom, "status": lambda: {"play_state": "idle"}}, path)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600  # only the Time Machine's user
        assert control.request("status", path) == {"play_state": "idle"}
        assert control.request("play", path, date="1977-05-08", recording=1) == {"date": "1977-05-08"}
        assert played == [("1977-05-08", 1)]
        with pytest.raises(RuntimeError, match="no recordings"):
            control.request("boom", path)
        with pytest.raises(RuntimeError, match="unknown command"):
            control.request("format_disk", path)
        with pytest.raises(RuntimeError, match="TypeError"):  # bad arguments
            control.request("play", path, when="tomorrow")
    finally:
        server.shutdown()
        server.server_close()
