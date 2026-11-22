import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    ConfigApplied, ConfigResult, ConfigSnapshot, CoreStatus, DeviceNeed, HealthCheck, HealthReport, HealthState,
    Header, ModuleAnnounce, ModuleManifest, PttEvidence, SiteLocation, Source, StructuredError,
    find_device_conflicts, find_sensitive_keys, new_id, utc_now,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def header(module="core", **overrides):
    values = dict(
        schema_version=1, message_id=new_id(), emitted_at=utc_now(), boot_id=new_id(), seq=0,
        source=Source(module=module, version="0.1.0"),
    )
    return Header(**{**values, **overrides})


# --- health -----------------------------------------------------------------

def check(name, state):
    return HealthCheck(name=name, state=state)


def report(state, *checks):
    return HealthReport(header=header(), state=state, uptime_s=12.0, checks=list(checks))


def test_summary_cannot_be_rosier_than_the_worst_check():
    report(HealthState.OK, check("device.gps", HealthState.OK))
    report(HealthState.DEGRADED, check("device.gps", HealthState.DEGRADED))
    report(HealthState.FAILED, check("device.gps", HealthState.OK))
    with pytest.raises(ValidationError):
        report(HealthState.OK, check("device.gps", HealthState.DEGRADED))
    with pytest.raises(ValidationError):
        report(HealthState.DEGRADED, check("device.gps", HealthState.FAILED))


def test_starting_module_may_have_failing_checks():
    report(HealthState.STARTING, check("device.gps", HealthState.FAILED))


def test_a_check_cannot_itself_be_starting():
    with pytest.raises(ValidationError):
        check("device.gps", HealthState.STARTING)


def test_check_error_details_are_redacted():
    failing = HealthCheck(
        name="upstream.nws", state=HealthState.FAILED,
        error=StructuredError(code="http.timeout", message="No answer.", details={"api_key": "SECRET"}),
    )
    assert "SECRET" not in failing.model_dump_json()


# --- manifest ---------------------------------------------------------------

def test_golden_gps_announce_parses_and_round_trips():
    announce = ModuleAnnounce.model_validate(load("module_announce_gps.json"))
    assert ModuleAnnounce.model_validate_json(announce.model_dump_json()) == announce
    assert announce.manifest.works_with_core((1, 0))
    assert not announce.manifest.works_with_core((2, 0))


def manifest(module_id, *devices, **overrides):
    values = dict(
        id=module_id, version="0.1.0", contract_version="1.0", devices=list(devices),
        resources={"memory_mb": 64, "cpu_millicores": 100}, health_port=9100,
    )
    return ModuleManifest(**{**values, **overrides})


def device(name="gps_serial", match="/dev/serial/by-id/usb-gps", **overrides):
    return DeviceNeed(**{**dict(name=name, match=match, access="read"), **overrides})


@pytest.mark.parametrize("match", ["/dev/ttyUSB0", "/dev/ttyACM1", "/dev/video0", "/dev/serial/../tty"])
def test_numbered_device_nodes_are_rejected(match):
    with pytest.raises(ValidationError):
        device(match=match)


def test_manifest_rejects_bad_contract_version_capability_and_duplicate_device():
    with pytest.raises(ValidationError):
        manifest("gps", contract_version="one")
    with pytest.raises(ValidationError):
        manifest("gps", capabilities=["NotDotted"])
    with pytest.raises(ValidationError):
        manifest("gps", device(), device())
    with pytest.raises(ValidationError):
        manifest("gps", health_port=80)


def test_exclusive_device_wanted_by_two_modules_is_a_conflict():
    conflicts = find_device_conflicts([manifest("gps", device()), manifest("geo", device(name="g2"))])
    assert len(conflicts) == 1 and "geo, gps" in conflicts[0]


def test_shared_readers_and_different_addresses_do_not_conflict():
    shared = dict(exclusive=False)
    assert find_device_conflicts([
        manifest("a", device(**shared)), manifest("b", device(name="other", **shared)),
        manifest("c", device(name="rtc", match="i2c:1:0x52")), manifest("d", device(name="adc", match="i2c:1:0x48")),
    ]) == []


def test_one_exclusive_and_one_shared_still_conflict():
    assert find_device_conflicts([manifest("a", device()), manifest("b", device(name="x", exclusive=False))])


# --- configuration ----------------------------------------------------------

def snapshot(values, **overrides):
    return ConfigSnapshot(header=header(), module="geo", config_version=1, values=values, **overrides)


def test_config_carries_plain_values_and_secret_names():
    cfg = snapshot({"poll_seconds": 60, "zones": ["AZZ540"]}, secret_refs=["nws_user_agent_contact"])
    assert cfg.secret_refs == ["nws_user_agent_contact"]


def test_config_with_a_secret_is_rejected_not_silently_altered():
    with pytest.raises(ValidationError, match="nested.api_key"):
        snapshot({"ok": 1, "nested": {"api_key": "abc"}})
    snapshot({"api_key": ""})  # unset is fine


def test_config_may_carry_site_location_unaltered():
    assert snapshot({"latitude": 33.448377}).values["latitude"] == 33.448377


def test_find_sensitive_keys_reports_paths_and_ignores_lookalikes():
    assert find_sensitive_keys({"a": {"pin": "1"}, "b": [{"token": "t"}], "mapping": "x"}) == ["a.pin", "b[0].token"]


def applied(result, request_id="req0000001", **overrides):
    return ConfigApplied(header=header("geo", request_id=request_id), config_version=1, result=result, **overrides)


def test_config_ack_rules():
    applied(ConfigResult.APPLIED)
    applied(ConfigResult.RESTART_REQUIRED)
    applied(ConfigResult.REJECTED, error=StructuredError(code="config.invalid", message="Bad zone."))
    with pytest.raises(ValidationError):
        applied(ConfigResult.REJECTED)
    with pytest.raises(ValidationError):
        applied(ConfigResult.APPLIED, request_id=None)


# --- status and location ----------------------------------------------------

def core_status(**overrides):
    values = dict(
        header=header(), state="idle", transmitter_enabled=True, locked_out=False, cos_active=False,
        ptt_commanded=False,
    )
    return CoreStatus(**{**values, **overrides})


def test_status_keeps_commanded_and_observed_ptt_apart():
    status = core_status(ptt_commanded=True)
    assert status.ptt_commanded and status.ptt_observed is None and status.ptt_evidence is PttEvidence.NONE
    assert core_status(state="hang_time", tot_remaining_s=12.5).tot_remaining_s == 12.5
    with pytest.raises(ValidationError):
        core_status(state="Idle Now")


def location(**overrides):
    return SiteLocation.model_validate({**load("site_location_gps.json"), **overrides})


def test_golden_gps_location_round_trips_and_is_usable_while_fresh():
    loc = location()
    assert SiteLocation.model_validate_json(loc.model_dump_json()) == loc
    observed = loc.freshness.observed_at
    assert loc.usable(observed + timedelta(seconds=30))
    assert not loc.usable(observed + timedelta(seconds=31))


def test_gps_without_a_fix_is_not_usable_even_if_fresh():
    loc = location(fix="none")
    assert not loc.usable(loc.freshness.observed_at)


def test_fixed_location_never_goes_stale():
    fixed = SiteLocation.model_validate({
        **{k: v for k, v in load("site_location_gps.json").items() if k not in ("fix", "freshness")}, "source": "fixed",
    })
    assert fixed.usable(datetime(2040, 1, 1, tzinfo=timezone.utc))


def test_location_source_rules_and_ranges():
    with pytest.raises(ValidationError):
        SiteLocation.model_validate({k: v for k, v in load("site_location_gps.json").items() if k != "freshness"})
    with pytest.raises(ValidationError):
        location(source="fixed")
    with pytest.raises(ValidationError):
        location(latitude=91)


def test_a_capability_name_with_a_trailing_newline_is_refused():
    import pytest

    with pytest.raises(ValueError):
        manifest("a", capabilities=["gps.position\n"])
