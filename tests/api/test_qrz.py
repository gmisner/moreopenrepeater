import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.activity import ActivityRecorder, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.persistence import StateStore
from api.qrz import QrzBook
from api.service import RepeaterService
from controller.macros import Macro
from controller.state_machine import RepeaterConfig


LOGIN_XML = """<?xml version="1.0"?>
<QRZDatabase>
  <Session>
    <Key>session-abc</Key>
  </Session>
</QRZDatabase>
"""

LOOKUP_XML = """<?xml version="1.0"?>
<QRZDatabase>
  <Callsign>
    <call>W1AW</call>
    <fname>Hiram</fname>
    <name>Maxim</name>
    <addr2>Newington</addr2>
    <state>CT</state>
    <class>E</class>
  </Callsign>
  <Session>
    <Key>session-abc</Key>
  </Session>
</QRZDatabase>
"""

EXPIRED_XML = """<?xml version="1.0"?>
<QRZDatabase>
  <Session>
    <Error>Session Timeout</Error>
  </Session>
</QRZDatabase>
"""


def test_lookup_fills_name_location_and_class():
    calls = []

    def fetch(url: str) -> str:
        calls.append(url)
        if "username=" in url:
            return LOGIN_XML
        return LOOKUP_XML

    book = QrzBook(fetch=fetch)
    book.update(username="w1aw", password="secret")
    result = book.lookup("w1aw")
    assert result.found is True
    assert result.callsign == "W1AW"
    assert result.name == "Hiram Maxim"
    assert result.location == "Newington, CT"
    assert result.license_class == "Extra"
    assert any("agent=moreopenrepeater" in url for url in calls)


def test_lookup_soft_fails_when_unconfigured_or_unreachable():
    book = QrzBook()
    assert book.lookup("W1AW").found is False
    assert book.lookup("W1AW").configured is False

    def boom(_url: str) -> str:
        raise TimeoutError("slow")

    book = QrzBook(fetch=boom)
    book.update(username="w1aw", password="secret")
    result = book.lookup("W1AW")
    assert result.found is False and result.configured is True


def test_lookup_retries_after_session_timeout():
    responses = [LOGIN_XML, EXPIRED_XML, LOGIN_XML, LOOKUP_XML]

    def fetch(_url: str) -> str:
        return responses.pop(0)

    book = QrzBook(fetch=fetch)
    book.update(username="w1aw", password="secret")
    assert book.lookup("W1AW").found is True
    assert responses == []


def test_password_is_kept_when_omitted(tmp_path):
    store = StateStore(tmp_path / "qrz.json")
    book = QrzBook(store)
    book.update(username="w1aw", password="secret")
    book.update(username="w1aw", password=None)
    assert book.credentials.password == "secret"
    assert store.load()["password"] == "secret"


def test_qrz_api_endpoints():
    responses = {"xml": LOGIN_XML}

    def fetch(url: str) -> str:
        if "callsign=" in url:
            return LOOKUP_XML
        return responses["xml"]

    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW"),
        macros=[Macro(pattern="*71", description="net", action="net_start")],
        clock=lambda: 0.0,
        activity=ActivityRecorder(ActivityStore()),
    )
    tmp = Path(tempfile.mkdtemp())
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp / "audio"),
        log_path=tmp / "test.log",
        qrz=QrzBook(fetch=fetch),
    )
    client = TestClient(app)
    with client:
        assert client.get("/api/net/qrz").json() == {"username": "", "password_set": False, "configured": False}
        empty = client.get("/api/net/lookup/W1AW").json()
        assert empty["found"] is False and empty["configured"] is False

        saved = client.put("/api/net/qrz", json={"username": "w1aw", "password": "secret"}).json()
        assert saved["configured"] is True and saved["password_set"] is True
        kept = client.put("/api/net/qrz", json={"username": "w1aw"}).json()
        assert kept["configured"] is True

        found = client.get("/api/net/lookup/w1aw").json()
        assert found["found"] is True
        assert found["name"] == "Hiram Maxim"
        assert found["location"] == "Newington, CT"
        assert found["license_class"] == "Extra"
        assert client.get("/api/net/lookup/not a call!").status_code == 422
