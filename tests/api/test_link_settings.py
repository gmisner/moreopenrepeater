from api.app import link_settings_from_env


def test_link_settings_from_env_is_none_when_host_unset(monkeypatch):
    monkeypatch.delenv("MOREOPENREPEATER_AMI_HOST", raising=False)
    assert link_settings_from_env() is None


def test_link_settings_from_env_reads_all_fields_with_defaults(monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_AMI_HOST", "127.0.0.1")
    monkeypatch.delenv("MOREOPENREPEATER_AMI_PORT", raising=False)
    monkeypatch.delenv("MOREOPENREPEATER_AMI_USER", raising=False)
    monkeypatch.setenv("MOREOPENREPEATER_AMI_SECRET", "s3cret")
    monkeypatch.setenv("MOREOPENREPEATER_AMI_NODE", "1999")

    settings = link_settings_from_env()

    assert settings is not None
    assert settings.host == "127.0.0.1"
    assert settings.port == 5038
    assert settings.username == "admin"
    assert settings.secret == "s3cret"
    assert settings.local_node_id == "1999"
