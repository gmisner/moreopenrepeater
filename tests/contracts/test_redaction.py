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
