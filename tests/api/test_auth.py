import base64

import pytest

from api.auth import (
    AuthSettings,
    SessionStore,
    auth_settings_from_env,
    credentials_match,
    parse_basic_auth_header,
    verify_credentials,
)


def _basic_header(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def test_auth_settings_from_env_is_none_when_unset():
    assert auth_settings_from_env({}) is None


def test_auth_settings_from_env_reads_both_fields():
    settings = auth_settings_from_env({"MOREOPENREPEATER_AUTH_USER": "admin", "MOREOPENREPEATER_AUTH_PASSWORD": "hunter2"})
    assert settings == AuthSettings(username="admin", password="hunter2")


def test_auth_settings_from_env_rejects_partial_config():
    with pytest.raises(RuntimeError):
        auth_settings_from_env({"MOREOPENREPEATER_AUTH_USER": "admin"})
    with pytest.raises(RuntimeError):
        auth_settings_from_env({"MOREOPENREPEATER_AUTH_PASSWORD": "hunter2"})


def test_parse_basic_auth_header_decodes_valid_header():
    assert parse_basic_auth_header(_basic_header("admin", "hunter2")) == ("admin", "hunter2")


@pytest.mark.parametrize(
    "header_value",
    [None, "", "Bearer sometoken", "Basic", "Basic not-valid-base64!!!", "Basic " + base64.b64encode(b"no-colon").decode()],
)
def test_parse_basic_auth_header_rejects_malformed_input(header_value):
    assert parse_basic_auth_header(header_value) is None


def test_credentials_match_accepts_correct_credentials():
    settings = AuthSettings(username="admin", password="hunter2")
    assert credentials_match(_basic_header("admin", "hunter2"), settings) is True


def test_credentials_match_rejects_wrong_credentials():
    settings = AuthSettings(username="admin", password="hunter2")
    assert credentials_match(_basic_header("admin", "wrong"), settings) is False
    assert credentials_match(_basic_header("someoneelse", "hunter2"), settings) is False
    assert credentials_match(None, settings) is False


def test_verify_credentials_handles_non_ascii_passwords():
    settings = AuthSettings(username="admin", password="pässwörd")
    assert verify_credentials("admin", "pässwörd", settings) is True
    assert verify_credentials("admin", "passwort", settings) is False


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_session_store_round_trips_a_token():
    store = SessionStore(ttl_seconds=60, clock=FakeClock())
    token = store.create("admin")
    assert store.username_for(token) == "admin"


def test_session_store_rejects_unknown_and_missing_tokens():
    store = SessionStore(ttl_seconds=60, clock=FakeClock())
    store.create("admin")
    assert store.username_for("not-a-real-token") is None
    assert store.username_for(None) is None
    assert store.username_for("") is None


def test_session_store_expires_tokens_after_ttl():
    clock = FakeClock()
    store = SessionStore(ttl_seconds=60, clock=clock)
    token = store.create("admin")
    clock.now += 59
    assert store.username_for(token) == "admin"
    clock.now += 1
    assert store.username_for(token) is None


def test_session_store_revoke_invalidates_token():
    store = SessionStore(ttl_seconds=60, clock=FakeClock())
    token = store.create("admin")
    store.revoke(token)
    assert store.username_for(token) is None
