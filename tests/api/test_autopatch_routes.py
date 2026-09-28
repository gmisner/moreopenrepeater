import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.autopatch import Autopatch
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig


def make_client(**config):
    service = RepeaterService(config=RepeaterConfig(**config), clock=lambda: 0.0)
    tmp_dir = Path(tempfile.mkdtemp())
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp_dir / "audio"),
        log_path=tmp_dir / "test.log",
        autopatch=Autopatch(service, None),
    )
    return TestClient(app), service


def test_status_without_asterisk():
    client, _ = make_client(autopatch_enabled=True)
    body = client.get("/api/autopatch").json()
    assert body == {
        "available": False, "configured": False, "error": None, "enabled": True, "call": None, "last_call": None,
    }


def test_dial_explains_why_it_cant():
    client, _ = make_client()
    response = client.post("/api/autopatch/dial", json={"number": "911"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Autopatch is turned off."
    assert client.post("/api/autopatch/dial", json={"number": "9-1-1"}).status_code == 422


def test_hangup_with_no_call_is_harmless():
    client, _ = make_client()
    assert client.post("/api/autopatch/hangup").status_code == 200


def test_config_round_trip():
    client, service = make_client()
    response = client.put(
        "/api/config",
        json={
            "autopatch_enabled": True,
            "autopatch_access_code": "*7",
            "autopatch_hangup_code": "##",
            "autopatch_dial_string": "PJSIP/+1{number}@voipms",
            "autopatch_caller_id": '"W1AW Repeater" <8605550100>',
            "autopatch_allowed": "911, NXXNXXXXXX, 1NXXNXXXXXX",
            "autopatch_max_call_seconds": 300,
        },
    )
    assert response.status_code == 200
    assert service.config.autopatch_dial_string == "PJSIP/+1{number}@voipms"
    assert response.json()["autopatch_hangup_code"] == "##"


def test_config_rejects_values_that_could_break_the_call_setup():
    client, _ = make_client()
    for bad in (
        {"autopatch_access_code": "*6#"},  # "#" ends the number
        {"autopatch_access_code": ""},
        {"autopatch_dial_string": "PJSIP/trunk"},  # no {number}
        {"autopatch_dial_string": "PJSIP/{number}@trunk\r\nAction: Command"},
        {"autopatch_caller_id": "x\ny"},
        {"autopatch_allowed": "911 1-800"},
        {"autopatch_max_call_seconds": 5},
    ):
        assert client.put("/api/config", json=bad).status_code == 422, bad
