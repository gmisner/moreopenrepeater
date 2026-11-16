import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    PttCommanded, PttEvidence, PttFeedback, SpeakDecision, SpeakRequest, TransmissionEnded, core_event_adapter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("name,model", [
    ("speak_request.json", SpeakRequest),
    ("speak_decision_accepted.json", SpeakDecision),
    ("ptt_commanded.json", PttCommanded),
    ("ptt_feedback_gpio_only.json", PttFeedback),
])
def test_golden_fixtures_parse_and_round_trip(name, model):
    message = model.model_validate(load(name))
    again = model.model_validate_json(message.model_dump_json())
    assert again == message


def test_core_events_parse_by_kind():
    assert isinstance(core_event_adapter.validate_python(load("ptt_commanded.json")), PttCommanded)
    assert isinstance(core_event_adapter.validate_python(load("ptt_feedback_gpio_only.json")), PttFeedback)
    with pytest.raises(ValidationError):
        core_event_adapter.validate_python({**load("ptt_commanded.json"), "kind": "ptt.mystery"})


def test_gpio_readback_does_not_prove_rf():
    feedback = PttFeedback.model_validate(load("ptt_feedback_gpio_only.json"))
    assert feedback.observed_keyed is True
    assert not feedback.proves_rf


def test_only_independent_evidence_proves_rf():
    base = load("ptt_feedback_gpio_only.json")
    for evidence, proves in [("current_sense", True), ("rf_detect", True), ("gpio_readback", False),
                             ("audio_loopback", False), ("none", False)]:
        assert PttFeedback.model_validate({**base, "evidence": evidence}).proves_rf is proves
    assert not PttFeedback.model_validate({**base, "evidence": "rf_detect", "observed_keyed": False}).proves_rf
    assert not PttFeedback.model_validate({**base, "evidence": "rf_detect", "observed_keyed": None}).proves_rf


def test_unknown_observation_is_not_the_same_as_unkeyed():
    base = load("ptt_feedback_gpio_only.json")
    unknown = PttFeedback.model_validate({**base, "evidence": "none", "observed_keyed": None})
    assert unknown.observed_keyed is None
    with pytest.raises(ValidationError):
        PttFeedback.model_validate({k: v for k, v in base.items() if k != "observed_keyed"})


def test_evidence_independence_flag():
    assert [e.independent for e in PttEvidence] == [False, False, False, True, True]


def test_tx_end_needs_a_reason_and_non_negative_duration():
    base = {
        "kind": "tx.ended", "header": load("ptt_commanded.json")["header"],
        "transmission_id": "7e7e7e7e8f8f8f8f9a9a9a9ab0b0b0b0", "reason": "timeout", "duration_s": 180.0,
    }
    assert TransmissionEnded.model_validate(base).reason.value == "timeout"
    for bad in ({"reason": "because"}, {"duration_s": -1}):
        with pytest.raises(ValidationError):
            TransmissionEnded.model_validate({**base, **bad})
