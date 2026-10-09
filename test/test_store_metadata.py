import errno
import json
import os

from timemachine import Archivary


def cross_device_replace(real_replace):
    """os.replace that fails like a rename between filesystems (eg /tmp on a tmpfs) unless src and dst share a directory"""

    def replace(src, dst):
        if os.path.dirname(os.path.abspath(src)) != os.path.dirname(os.path.abspath(dst)):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst)

    return replace


def test_store_metadata_with_tmp_on_another_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", cross_device_replace(os.replace))
    monkeypatch.setattr(os, "rename", cross_device_replace(os.rename))
    iddir = tmp_path / "GratefulDead_ids"
    tapes = [
        {"identifier": "gd73-12-19.sbd", "date": "1973-12-19"},
        {"identifier": "gd77-05-08.sbd", "date": "1977-05-08"},
        {"identifier": "gd90-03-29.sbd", "date": "1990-03-29"},
    ]
    downloader = Archivary.IATapeDownloader()

    assert downloader.store_metadata(str(iddir), tapes) == 3
    assert sorted(os.listdir(iddir)) == ["ids_1970.json", "ids_1990.json"]
    assert len(json.load(open(iddir / "ids_1970.json"))) == 2

    # storing the same tapes again adds nothing, because they are already on disk
    assert downloader.store_metadata(str(iddir), tapes) == 0
