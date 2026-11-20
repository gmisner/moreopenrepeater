"""The backups AsteriskFiles keeps before a write (link.asterisk_files)."""
import time

from link.asterisk_files import KEEP_BACKUPS, AsteriskFiles


def make(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "rpt.conf").write_text("original\n")
    (etc / "modules.conf").write_text("modules\n")
    return AsteriskFiles(str(etc), backup_dir=tmp_path / "backups"), etc


def test_two_writes_in_the_same_second_keep_the_original_copy(tmp_path, monkeypatch):
    files, etc = make(tmp_path)
    monkeypatch.setattr(time, "strftime", lambda fmt: "20261120-101010")
    files.write("rpt.conf", "first change\n")
    files.write("rpt.conf", "second change\n")
    files.write("rpt.conf", "third change\n")
    kept = sorted((tmp_path / "backups").glob("rpt.conf.*"))
    assert [p.read_text() for p in kept] == ["original\n", "first change\n", "second change\n"]
    assert [p.name for p in kept] == ["rpt.conf.20261120-101010", "rpt.conf.20261120-101010-01", "rpt.conf.20261120-101010-02"]


def test_only_the_latest_backups_of_a_file_are_kept(tmp_path, monkeypatch):
    files, etc = make(tmp_path)
    stamps = iter(f"20261120-1010{n:02d}" for n in range(30))
    monkeypatch.setattr(time, "strftime", lambda fmt: next(stamps))
    for n in range(15):
        files.write("rpt.conf", f"change {n}\n")
    kept = sorted((tmp_path / "backups").glob("rpt.conf.*"))
    assert len(kept) == KEEP_BACKUPS and kept[-1].read_text() == "change 13\n"


def test_a_snapshot_is_one_folder_with_every_file_that_exists(tmp_path):
    files, etc = make(tmp_path)
    folder = files.snapshot(["rpt.conf", "modules.conf", "not-there.conf"], "import")
    assert folder.parent == tmp_path / "backups" and folder.name.startswith("import-")
    assert sorted(p.name for p in folder.iterdir()) == ["modules.conf", "rpt.conf"]
    assert (folder / "rpt.conf").read_text() == "original\n"


def test_snapshots_keep_the_first_and_the_latest_nine(tmp_path, monkeypatch):
    files, etc = make(tmp_path)
    stamps = iter(f"20261120-1010{n:02d}" for n in range(40))
    monkeypatch.setattr(time, "strftime", lambda fmt: next(stamps))
    (etc / "rpt.conf").write_text("the very first state\n")
    for n in range(14):
        files.snapshot(["rpt.conf"], "import")
        (etc / "rpt.conf").write_text(f"state {n}\n")
    kept = sorted((tmp_path / "backups").glob("import-*"))
    assert len(kept) == 10
    assert (kept[0] / "rpt.conf").read_text() == "the very first state\n"


def test_without_a_backup_folder_nothing_is_kept(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "rpt.conf").write_text("x\n")
    assert AsteriskFiles(str(etc)).snapshot(["rpt.conf"], "import") is None
