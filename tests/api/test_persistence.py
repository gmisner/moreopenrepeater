import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from controller.macros import Macro

from api.app import create_app
from api.assets import AudioAssetStore
from api.persistence import StateStore
from api.service import RepeaterService


def _tmp_path() -> Path:
    return Path(tempfile.mkdtemp())


def test_state_store_load_returns_none_when_file_missing():
    assert StateStore(_tmp_path() / "state.json").load() is None


def test_state_store_round_trips_data():
    store = StateStore(_tmp_path() / "nested" / "state.json")
    store.save({"config": {"hang_time": 4.0}, "macros": []})
    assert store.load() == {"config": {"hang_time": 4.0}, "macros": []}


def test_state_store_leaves_no_temp_files_behind():
    tmp = _tmp_path()
    store = StateStore(tmp / "state.json")
    store.save({"a": 1})
    store.save({"a": 2})
    assert sorted(p.name for p in tmp.iterdir()) == ["state.json"]


def test_state_store_moves_a_corrupt_file_aside_instead_of_losing_it():
    tmp = _tmp_path()
    (tmp / "state.json").write_text("{not json")
    store = StateStore(tmp / "state.json")

    assert store.load() is None
    assert (tmp / "state.json.corrupt").read_text() == "{not json"


def test_service_persists_config_and_macro_changes_across_restart():
    path = _tmp_path() / "state.json"
    service = RepeaterService(state_store=StateStore(path))
    service.update_config(hang_time=7.5, callsign="W1AW")
    service.add_macro(Macro(pattern="*81", description="", command="disconnect_all", node_id=""))

    restarted = RepeaterService(state_store=StateStore(path))

    assert restarted.config.hang_time == 7.5
    assert restarted.config.callsign == "W1AW"
    assert [m.pattern for m in restarted.list_macros()] == ["*81"]


def test_service_persists_macro_deletion():
    path = _tmp_path() / "state.json"
    service = RepeaterService(state_store=StateStore(path))
    service.add_macro(Macro(pattern="*81", description="", command="x", node_id=""))
    service.delete_macro("*81")

    assert RepeaterService(state_store=StateStore(path)).list_macros() == []


def test_service_tolerates_unknown_and_missing_fields_in_saved_state():
    path = _tmp_path() / "state.json"
    path.write_text(json.dumps({"config": {"hang_time": 9.0, "from_the_future": True}, "macros": []}))

    service = RepeaterService(state_store=StateStore(path))

    assert service.config.hang_time == 9.0
    assert service.config.tot_duration == 180.0  # default for a field the file didn't have


def test_service_without_a_store_writes_nothing():
    service = RepeaterService()
    service.update_config(hang_time=2.0)  # must not raise or touch disk


def _client_with_store(path: Path) -> TestClient:
    tmp = _tmp_path()
    return TestClient(
        create_app(
            start_background_tick=False,
            assets_store=AudioAssetStore(tmp / "audio"),
            log_path=tmp / "test.log",
            state_store=StateStore(path),
        )
    )


def test_config_saved_through_the_api_survives_an_app_restart():
    path = _tmp_path() / "state.json"
    _client_with_store(path).put("/api/config", json={"callsign": "K1ABC", "hang_time": 5.0})

    restarted = _client_with_store(path)

    body = restarted.get("/api/config").json()
    assert body["callsign"] == "K1ABC"
    assert body["hang_time"] == 5.0


def test_restoring_a_backup_from_an_older_version_fills_in_defaults():
    client = _client_with_store(_tmp_path() / "state.json")
    client.put("/api/config", json={"tot_duration": 99.0})

    response = client.post("/api/snapshot", json={"config": {"callsign": "OLD1"}, "macros": []})

    assert response.status_code == 200
    assert response.json()["config"]["callsign"] == "OLD1"
    assert response.json()["config"]["tot_duration"] == 180.0  # replaced wholesale, not merged


def test_restoring_a_backup_with_an_invalid_value_is_rejected():
    client = _client_with_store(_tmp_path() / "state.json")

    response = client.post("/api/snapshot", json={"config": {"id_mode": "semaphore"}, "macros": []})

    assert response.status_code == 422


def test_databases_use_a_write_ahead_log_but_copies_are_single_files(tmp_path):
    import sqlite3

    from api.audit import AuditLog
    from api.persistence import database_has_table

    log = AuditLog(tmp_path / "audit.db")
    log.record(1.0, "admin", "PUT /api/config")
    probe = sqlite3.connect(tmp_path / "audit.db")
    assert probe.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    probe.close()

    copy = tmp_path / "copy" / "audit.db"
    copy.parent.mkdir()
    log.copy_to(copy)

    copy_conn = sqlite3.connect(copy)
    assert copy_conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    copy_conn.close()
    assert database_has_table(copy, "audit")
    assert sorted(p.name for p in copy.parent.iterdir()) == ["audit.db"]
