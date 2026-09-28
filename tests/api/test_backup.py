import importlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from controller.announcements import Announcement
from controller.macros import Macro
from controller.state_machine import RepeaterConfig

from api.activity import ActivityRow, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.audit import AuditLog
from api.auth import AuthSettings
from api.backup import BackupFolder, BackupSources, backup_name
from api.persistence import StateStore
from api.recordings import RecordingStore
from api.service import RepeaterService
from api.users import UserStore

WAV = b"RIFF$\x00\x00\x00WAVEfmt fake clip"


@pytest.fixture(autouse=True)
def no_login_failure_delay(monkeypatch):
    monkeypatch.setattr(importlib.import_module("api.app"), "LOGIN_FAILURE_DELAY_SECONDS", 0)


class Repeater:
    """An app with every store on disk in its own directory."""

    def __init__(self, auth: bool = False, backup_dir: bool = False) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.service = RepeaterService(state_store=StateStore(self.dir / "state.json"))
        self.users = UserStore(StateStore(self.dir / "users.json"))
        self.assets = AudioAssetStore(self.dir / "audio")
        self.recordings = RecordingStore(self.dir / "recordings")
        self.activity = ActivityStore(self.dir / "activity.db")
        self.audit = AuditLog(self.dir / "audit.db")
        self.folder = BackupFolder(self.dir / "backups" if backup_dir else None)
        app = create_app(
            service=self.service,
            start_background_tick=False,
            assets_store=self.assets,
            log_path=self.dir / "t.log",
            auth_settings=AuthSettings("owner", "hunter2") if auth else None,
            users=self.users,
            audit=self.audit,
            activity_store=self.activity,
            recordings=self.recordings,
            backups=self.folder,
        )
        self.client = TestClient(app)
        if auth:
            self.client.post("/api/login", json={"username": "owner", "password": "hunter2"})

    @property
    def sources(self) -> BackupSources:
        return BackupSources(self.service, self.users, self.assets, self.recordings, self.activity, self.audit)


def login_alice(r: Repeater) -> None:
    assert r.client.post("/api/login", json={"username": "alice", "password": "correct horse"}).status_code == 200


def populated() -> Repeater:
    r = Repeater()
    clip = r.assets.save_asset("courtesy_tone", "boop.wav", WAV)
    r.service.update_config(callsign="W1AW", hang_time=4.5, courtesy_tone_asset_id=clip.id)
    r.service.add_macro(Macro(pattern="*81", description="net", command="disconnect_all", node_id=""))
    r.service.save_announcement(Announcement(id="a1", name="Net", message="Net tonight", kind="interval", every_minutes=60))
    r.users.add("alice", "correct horse", "admin")  # which turns sign-in on
    login_alice(r)
    r.activity.add(ActivityRow("rx", 1_700_000_000.0, 12.5))
    r.audit.record(1_700_000_000.0, "alice", "PUT /api/config", "hang_time")
    r.recordings.save(np.zeros(1600, dtype=np.float32), 1_700_000_000.0)
    return r


def restore(target: Repeater, archive: bytes, filename="backup.zip"):
    return target.client.post("/api/backup/restore", files={"file": (filename, archive, "application/zip")})


def test_full_backup_restores_everything_onto_a_fresh_install():
    source = populated()
    response = source.client.get("/api/backup", params={"recordings": True})
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="moreopenrepeater-w1aw-')
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(response.content)).read("manifest.json"))
    assert manifest["contents"] == {"macros": 1, "announcements": 1, "users": 1, "audio_clips": 1, "recordings": 1}

    target = Repeater()
    result = restore(target, response.content)

    assert result.status_code == 200, result.text
    assert result.json() == {"macros": 1, "announcements": 1, "users": 1, "audio_clips": 1, "history": True, "recordings": 1}
    login_alice(target)
    config = target.client.get("/api/config").json()
    assert (config["callsign"], config["hang_time"]) == ("W1AW", 4.5)
    assert target.assets.path_for(config["courtesy_tone_asset_id"]).read_bytes() == WAV
    assert [m.pattern for m in target.service.list_macros()] == ["*81"]
    assert target.client.get("/api/announcements").json()[0]["name"] == "Net"
    assert target.users.authenticate("alice", "correct horse") is not None
    assert [r.duration for r in target.activity.rows(0, 2e9)] == [12.5]
    assert ("alice", "PUT /api/config") in [(e.actor, e.action) for e in target.audit.recent()]
    assert [r.started_at for r in target.recordings.list()] == [1_700_000_000.0]
    # Saved to disk, not just in memory.
    assert json.loads((target.dir / "state.json").read_text())["config"]["callsign"] == "W1AW"


def test_recordings_are_left_out_unless_asked_for():
    source = populated()
    archive = zipfile.ZipFile(io.BytesIO(source.client.get("/api/backup").content))
    assert not [n for n in archive.namelist() if n.startswith("recordings/")]
    assert json.loads(archive.read("manifest.json"))["contents"]["recordings"] == 0


def test_restore_replaces_clips_but_adds_recordings():
    source = populated()
    archive = source.client.get("/api/backup", params={"recordings": True}).content
    target = Repeater()
    stale = target.assets.save_asset("id", "old-id.wav", WAV)
    target.recordings.save(np.zeros(160, dtype=np.float32), 1_800_000_000.0)

    restore(target, archive)

    assert stale.id not in {a.id for a in target.assets.list_assets()}
    assert not target.assets.path_for(stale.id).exists()
    assert len(target.recordings.list()) == 2


def test_a_backup_without_accounts_keeps_the_current_ones():
    archive = Repeater().client.get("/api/backup").content
    target = Repeater()
    target.users.add("bob", "hunter22", "admin")
    target.client.post("/api/login", json={"username": "bob", "password": "hunter22"})

    result = restore(target, archive).json()

    assert result["users"] is None
    assert target.users.get("bob") is not None


def _rewrite(archive: bytes, **replacements) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as src, zipfile.ZipFile(out, "w") as dest:
        for name in src.namelist():
            data = replacements.get(name, src.read(name))
            if data is not None:
                dest.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize(
    "change, message",
    [
        ({"manifest.json": json.dumps({"format": "something-else"})}, "isn't a moreopenrepeater backup"),
        ({"manifest.json": json.dumps({"format": "moreopenrepeater-backup", "version": 99})}, "newer version"),
        ({"settings.json": json.dumps({"config": {"id_mode": "semaphore"}})}, "settings in the backup"),
        ({"history/audit.db": b"not a database"}, "audit log in the backup is damaged"),
        ({"users.json": json.dumps({"users": [{"username": "x", "role": "viewer", "password_hash": "scrypt$1"}]})}, "admin"),
    ],
)
def test_a_bad_backup_changes_nothing(change, message):
    source = populated()
    archive = _rewrite(source.client.get("/api/backup").content, **change)
    target = Repeater()
    target.service.update_config(callsign="KEEP")
    kept = target.assets.save_asset("id", "mine.wav", WAV)

    response = restore(target, archive)

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert target.service.config.callsign == "KEEP"
    assert [a.id for a in target.assets.list_assets()] == [kept.id]


def test_a_missing_clip_is_caught_before_anything_changes():
    source = populated()
    clip_id = source.assets.list_assets()[0].id
    archive = _rewrite(source.client.get("/api/backup").content, **{f"audio/{clip_id}.wav": None})
    target = Repeater()

    response = restore(target, archive)

    assert response.status_code == 400
    assert "boop.wav" in response.json()["detail"]
    assert target.service.config.callsign == ""


def test_something_that_isnt_a_zip_is_refused():
    response = restore(Repeater(), b'{"config": {}}', "settings.json")
    assert response.status_code == 400
    assert "not a zip file" in response.json()["detail"]


def test_full_backups_are_admin_only_and_downloads_are_audited():
    r = Repeater(auth=True)
    r.users.add("op", "operator pass", "operator")
    assert r.client.get("/api/backup").status_code == 200
    assert r.audit.recent()[0].action == "download backup"

    operator = TestClient(r.client.app)
    operator.post("/api/login", json={"username": "op", "password": "operator pass"})
    assert operator.get("/api/backup").status_code == 403
    assert operator.post("/api/backup/restore", files={"file": ("b.zip", b"x")}).status_code == 403
    assert operator.get("/api/backups").status_code == 403


def test_saved_backups_can_be_made_listed_downloaded_restored_and_deleted():
    r = Repeater(backup_dir=True)
    r.service.update_config(callsign="W1AW")
    listing = r.client.post("/api/backups").json()
    [saved] = listing["backups"]
    assert saved["name"].startswith("moreopenrepeater-w1aw-")
    assert saved["contents"]["macros"] == 0
    assert listing["directory"] == str(r.dir / "backups")

    download = r.client.get(f"/api/backups/{saved['name']}")
    assert download.status_code == 200 and download.content[:2] == b"PK"

    r.service.update_config(callsign="CHANGED")
    assert r.client.post(f"/api/backups/{saved['name']}/restore").status_code == 200
    assert r.service.config.callsign == "W1AW"

    assert r.client.delete(f"/api/backups/{saved['name']}").json()["backups"] == []
    assert r.client.get("/api/backups/../state.json").status_code == 404
    assert r.client.get("/api/backups/moreopenrepeater-x-20260101-000000.zip").status_code == 404


def test_without_a_backup_folder_only_downloads_work():
    r = Repeater()
    assert r.client.get("/api/backups").json() == {"enabled": False, "directory": None, "last_error": None, "backups": []}
    assert r.client.post("/api/backups").status_code == 409


def test_scheduled_backups_run_when_due_and_keep_the_newest():
    r = Repeater(backup_dir=True)
    config = RepeaterConfig(backup_enabled=True, backup_interval_hours=24, backup_keep=2)
    day = 86400.0
    start = 1_800_000_000.0

    assert r.folder.run_schedule(r.sources, RepeaterConfig(), start) is None  # off
    assert r.folder.run_schedule(r.sources, config, start) is not None
    assert r.folder.run_schedule(r.sources, config, start + day / 2) is None  # not due yet
    for n in (1, 2, 3):
        assert r.folder.run_schedule(r.sources, config, start + n * day) is not None

    names = [b.name for b in r.folder.list()]
    assert names == [backup_name("", start + 3 * day), backup_name("", start + 2 * day)]
    assert not list((r.dir / "backups").glob(".*"))


def test_a_failed_scheduled_backup_is_reported_and_retried():
    r = Repeater(backup_dir=True)
    (r.dir / "backups").write_text("a file where the folder should be")
    config = RepeaterConfig(backup_enabled=True)

    assert r.folder.run_schedule(r.sources, config, 1_800_000_000.0) is None
    assert r.folder.last_error

    (r.dir / "backups").unlink()
    assert r.folder.run_schedule(r.sources, config, 1_800_000_060.0) is not None
    assert r.folder.last_error is None


def test_backup_settings_are_part_of_the_config():
    r = Repeater()
    response = r.client.put("/api/config", json={"backup_enabled": True, "backup_keep": 14, "backup_interval_hours": 6})
    assert response.status_code == 200
    assert (response.json()["backup_keep"], response.json()["backup_interval_hours"]) == (14, 6)
    assert r.client.put("/api/config", json={"backup_keep": 0}).status_code == 422
