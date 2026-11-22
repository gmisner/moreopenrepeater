from contracts import OMITTED, REDACTED, redact


def test_credential_keys_are_redacted_in_any_spelling():
    out = redact({
        "password": "hunter2", "apiKey": "abc", "api_key": "abc", "X-Auth-Token": "t",
        "incoming_pin": "1234", "Authorization": "x", "private_key": "k",
    })
    assert set(out.values()) == {REDACTED}


def test_keys_that_only_contain_a_secret_word_as_a_substring_are_kept():
    out = redact({"mapping": "grid", "spinner": 3, "tokens_per_second": 5})
    assert out == {"mapping": "grid", "spinner": 3, "tokens_per_second": 5}


def test_empty_secret_stays_empty_so_unset_is_distinguishable():
    assert redact({"password": "", "token": None}) == {"password": "", "token": None}


def test_message_and_dtmf_content_is_omitted():
    out = redact({"dtmf_digits": "1234", "transcript": "hello", "text": "hi", "digit_count": 4})
    assert out == {"dtmf_digits": OMITTED, "transcript": OMITTED, "text": OMITTED, "digit_count": 4}


def test_location_is_coarsened():
    out = redact({"latitude": 33.448377, "lon": -112.074037, "grid": "DM33uk", "site": "North"})
    assert out == {"latitude": 33.4, "lon": -112.1, "grid": "DM33", "site": "North"}


def test_bad_location_value_is_not_passed_through():
    assert redact({"latitude": "33.448377 N"}) == {"latitude": REDACTED}


def test_credentials_inside_strings_are_masked():
    out = redact({"header": "Bearer abcdef123456", "url": "https://user:pw@host/x"})
    assert "abcdef123456" not in out["header"]
    assert "pw" not in out["url"] and out["url"].endswith("@host/x")


def test_nested_values_are_redacted_and_input_is_not_modified():
    source = {"outer": {"token": "t", "items": [{"password": "p"}]}}
    out = redact(source)
    assert out == {"outer": {"token": REDACTED, "items": [{"password": REDACTED}]}}
    assert source["outer"]["token"] == "t"


def test_non_json_values_become_type_placeholders_not_repr():
    class Secretive:
        def __repr__(self):
            return "SECRET"

    out = redact({"obj": Secretive(), "raw": b"\x00\x01"})
    assert out == {"obj": "[Secretive]", "raw": "[bytes]"}


def test_depth_size_and_string_length_are_bounded():
    deep = value = {}
    for _ in range(20):
        value["x"] = {}
        value = value["x"]
    assert "[truncated]" in str(redact(deep))
    assert len(redact({"s": "a" * 10_000})["s"]) < 600
    assert len(redact({f"k{i}": i for i in range(500)})) <= 51


def test_never_raises_on_odd_keys():
    assert redact({1: "x", None: "y"}) == {"int": "x", "NoneType": "y"}


def test_every_secret_word_is_redacted_as_a_key():
    secrets = ["pin", "pwd", "pass", "psk", "password", "token", "key", "session", "authorization", "ssh_key", "session_id", "wifiPwd"]
    out = redact({name: "value" for name in secrets})
    assert set(out.values()) == {REDACTED}, out


def test_benign_keys_that_only_share_a_word_with_content_or_secret_keys_keep_their_values():
    benign = {
        "audio_device": "hw:1,0", "error_message": "no such device", "rx_gpio_pin": 17, "ptt_gpio_pin": 4,
        "content_type": "audio/wav", "text_length": 12, "dtmf_mute": True, "message_count": 3, "session_count": 4,
        "key_up_ms": 40, "fan_cm108_pin": 1, "audio_input_device": "usb",
    }
    assert redact(benign) == benign


def test_content_keys_stay_omitted_including_ones_with_a_qualifier_that_is_not_known_to_be_benign():
    out = redact({"message": "hi", "sms_message": "hi", "message_body": "hi", "text_to_speak": "hi", "audio": "x", "audio_data": "x", "rx_audio": "x"})
    assert set(out.values()) == {OMITTED}, out


def test_secrets_in_free_text_are_masked_like_bearer_is():
    from contracts.redaction import mask_text

    for text, secret in [
        ("login with password=hunter2 now", "hunter2"),
        ("password: hunter2", "hunter2"),
        ("sent token=abc123xyz to peer", "abc123xyz"),
        ("{'password': 'hunter2', 'user': 'bob'}", "hunter2"),
        ('{"api_key": "s3cr3t value"}', "s3cr3t"),
        ("X-Auth-Token: abcdef", "abcdef"),
        ("wifi psk=letmein pwd=letmein2", "letmein"),
        ("https://x/y?session=abcd1234&a=1", "abcd1234"),
    ]:
        assert secret not in mask_text(text), text
    assert "'user': 'bob'" in mask_text("{'password': 'hunter2', 'user': 'bob'}")
    benign = "ptt_gpio_pin=17 audio_device: hw:1 error_message=bad"
    assert mask_text(benign) == benign
    assert mask_text("password=[redacted]") == "password=[redacted]"
    assert mask_text("it took 12:30:01 to run") == "it took 12:30:01 to run"


def test_free_text_masking_applies_to_string_values_too():
    assert "hunter2" not in redact({"detail": "retry with password=hunter2"})["detail"]


def test_masking_free_text_stays_fast_on_long_and_odd_strings():
    """It runs on the thread that logs. These took 40 to 450 ms each when the scan was quadratic."""
    import time

    from contracts.redaction import mask_text

    started = time.perf_counter()
    for text in ["x" * 20000, "a:" * 10000, "k=" * 10000, "a=b" * 5000, "word " * 4000]:
        mask_text(text)
    assert time.perf_counter() - started < 0.5
