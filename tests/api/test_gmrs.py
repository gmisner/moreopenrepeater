import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.activity import ActivityRecorder, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.links import LinkControl, LinkError
from api.node_directory import NodeDirectory
from api.service import RepeaterService
from controller.events import SendLinkCommand
from controller.modes import GMRS_MAX_ID_INTERVAL, effective_config
from controller.state_machine import RepeaterConfig

HAM = RepeaterConfig(
    callsign="WRXX123", id_interval=1200, autopatch_enabled=True, aprs_enabled=True, aprs_map_enabled=True,
)


def test_gmrs_rules_over_the_saved_settings():
    config = effective_config(RepeaterConfig(**{**HAM.__dict__, "gmrs_mode": True}))
    assert config.id_interval == GMRS_MAX_ID_INTERVAL
    assert config.autopatch_enabled is False
    assert config.aprs_enabled is False and config.aprs_map_enabled is False

    shorter = effective_config(RepeaterConfig(id_interval=600, gmrs_mode=True))
    assert shorter.id_interval == 600  # already often enough
    assert effective_config(HAM) is HAM


def test_a_simplex_node_holds_the_phone_patch():
    assert effective_config(RepeaterConfig(**{**HAM.__dict__, "node_mode": "simplex"})).autopatch_enabled is False
    service, _sent = make_service(node_mode="simplex")
    assert service.held_reason("autopatch") == "The phone patch is off on a simplex node."
    assert service.held_reason("links") == ""


def make_service(**overrides):
    sent = []
    service = RepeaterService(
        config=RepeaterConfig(**{**HAM.__dict__, **overrides}),
        clock=lambda: 0.0,
        link_command_sink=sent.append,
        activity=ActivityRecorder(ActivityStore()),
    )
    return service, sent


def test_gmrs_mode_holds_linking_and_the_phone_patch():
    service, sent = make_service(gmrs_mode=True)
    assert service.held_reason("links") == "Linking is off in GMRS mode."
    assert service.held_reason("autopatch") == "The phone patch is off in GMRS mode."
    assert service.snapshot().gmrs_mode is True

    service._apply_commands([SendLinkCommand(node_id="2000", command="*32000")])
    assert sent == []

    with pytest.raises(LinkError, match="GMRS"):
        asyncio.run(LinkControl(service, NodeDirectory(None)).connect("2000", False))


def test_turning_gmrs_off_restores_everything():
    service, sent = make_service(gmrs_mode=True)
    service.update_config(gmrs_mode=False)
    assert service.config == RepeaterConfig(**HAM.__dict__)
    service._apply_commands([SendLinkCommand(node_id="2000", command="*32000")])
    assert len(sent) == 1


def test_gmrs_api():
    service, _ = make_service()
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(
        service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "test.log"
    ))
    assert client.put("/api/config", json={"gmrs_mode": True}).json()["gmrs_mode"] is True
    assert client.get("/api/config").json()["autopatch_enabled"] is True  # still saved as switched on
    assert client.get("/api/status").json()["gmrs_mode"] is True
    detail = client.post("/api/autopatch/dial", json={"number": "5551234567"}).json()["detail"]
    assert "GMRS" in detail
