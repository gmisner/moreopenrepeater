import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from controller.state_machine import RepeaterConfig

from api.activity import ActivityRecorder, ActivityRow, ActivityStore, summarize
from api.app import create_app
from api.service import RepeaterService

START = datetime(2026, 9, 28, 9, 0)


def ts(dt: datetime) -> float:
    return dt.timestamp()


def make_client(store=None):
    tmp = Path(tempfile.mkdtemp())
    clock = {"wall": START}
    service = RepeaterService(
        config=RepeaterConfig(courtesy_tone_duration=0.2, hang_time=1.0, tot_duration=10.0, id_interval=10_000),
        clock=lambda: (clock["wall"] - START).total_seconds(),
        wall_clock=lambda: clock["wall"],
        activity=ActivityRecorder(store or ActivityStore()),
    )
    app = create_app(service=service, start_background_tick=False, log_path=tmp / "t.log")
    return TestClient(app), service, clock


def key(service, clock, seconds):
    """Simulate a user transmission of `seconds`, then let hang time expire."""
    service.simulate_cos(True)
    clock["wall"] += timedelta(seconds=seconds)
    service.tick()
    service.simulate_cos(False)
    clock["wall"] += timedelta(seconds=0.2)  # courtesy tone
    service.tick()
    clock["wall"] += timedelta(seconds=1.0)  # hang time
    service.tick()


def test_store_round_trip_and_prune():
    store = ActivityStore()
    store.add(ActivityRow("rx", ts(START), 4.0))
    store.add(ActivityRow("rx", ts(START + timedelta(days=1)), 2.0, timed_out=True))

    assert [r.duration for r in store.rows(ts(START), ts(START + timedelta(days=2)))] == [4.0, 2.0]
    assert store.recent("rx", 1)[0].timed_out
    assert store.prune(ts(START + timedelta(hours=1))) == 1
    assert len(store.rows(0, ts(START + timedelta(days=2)))) == 1


def test_store_persists_to_a_file():
    path = Path(tempfile.mkdtemp()) / "activity.db"
    ActivityStore(path).add(ActivityRow("rx", ts(START), 4.0))

    assert len(ActivityStore(path).rows(0, ts(START) + 1)) == 1


def test_recorder_records_user_transmissions_and_transmitter_time():
    client, service, clock = make_client()

    key(service, clock, 5)

    rows = service.activity.store.rows(0, ts(START + timedelta(days=1)))
    rx = [r for r in rows if r.kind == "rx"]
    tx = [r for r in rows if r.kind == "tx"]
    assert [round(r.duration, 3) for r in rx] == [5.0]
    assert len(tx) == 1 and round(tx[0].duration, 3) == 6.2  # 5 s repeat + courtesy tone + hang time


def test_recorder_flags_timeouts():
    client, service, clock = make_client()
    service.simulate_cos(True)
    clock["wall"] += timedelta(seconds=11)
    service.tick()

    (rx,) = service.activity.store.rows(0, ts(START + timedelta(days=1)), kind="rx")
    assert rx.timed_out


def test_recorder_counts_ids_and_announcements_but_not_tones():
    store = ActivityStore()
    recorder = ActivityRecorder(store)
    for clip in ["id", "courtesy_tone", "tts:hello", "asset:abc", "timeout_tone"]:
        recorder.clip_played(clip, ts(START))

    assert [r.kind for r in store.rows(0, ts(START) + 1)] == ["id", "announcement", "announcement"]


def test_summarize_totals_and_buckets():
    rows = [
        ActivityRow("rx", ts(START), 30.0),
        ActivityRow("rx", ts(START + timedelta(minutes=5)), 0.5),  # kerchunk
        ActivityRow("rx", ts(START + timedelta(days=1, hours=5)), 180.0, timed_out=True),
        ActivityRow("tx", ts(START), 35.0),
        ActivityRow("id", ts(START), 0.0),
        ActivityRow("announcement", ts(START), 0.0),
    ]

    summary = summarize(rows, START - timedelta(hours=1), START + timedelta(days=1, hours=6))

    assert summary["rx_count"] == 3
    assert summary["rx_seconds"] == 210.5
    assert summary["kerchunks"] == 1
    assert summary["timeouts"] == 1
    assert summary["longest_rx_seconds"] == 180.0
    assert summary["tx_seconds"] == 35.0
    assert (summary["ids"], summary["announcements"]) == (1, 1)
    assert summary["by_hour"][9] == 30.5
    assert summary["by_hour"][14] == 180.0
    assert [d["date"] for d in summary["by_day"]] == ["2026-09-28", "2026-09-29"]
    assert summary["by_day"][0]["rx_count"] == 2
    assert summary["by_day"][1]["rx_seconds"] == 180.0


def test_summary_endpoint_reflects_simulated_traffic():
    client, service, clock = make_client()
    key(service, clock, 5)
    key(service, clock, 1)

    summary = client.get("/api/activity/summary?days=1").json()

    assert summary["rx_count"] == 2
    assert summary["kerchunks"] == 1
    assert len(summary["by_hour"]) == 24


def test_transmissions_endpoint_lists_most_recent_first():
    client, service, clock = make_client()
    key(service, clock, 5)
    key(service, clock, 3)

    transmissions = client.get("/api/activity/transmissions?limit=10").json()

    assert [round(t["duration"]) for t in transmissions] == [3, 5]
    assert client.get("/api/activity/summary?days=0").status_code == 422


def test_filtered_kerchunks_are_counted_separately():
    client, service, clock = make_client()
    client.put("/api/config", json={"kerchunk_delay": 0.5})

    service.simulate_cos(True)
    clock["wall"] += timedelta(seconds=0.2)
    service.tick()
    service.simulate_cos(False)
    key(service, clock, 3.0)  # a real transmission still goes through

    summary = client.get("/api/activity/summary?days=1").json()
    assert summary["kerchunks_filtered"] == 1
    assert summary["rx_count"] == 1
