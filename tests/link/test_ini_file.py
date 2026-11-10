from link import ini_file


def test_section_values_skips_comments():
    text = """
[GENERAL]
; comment
useEmulator = true ; trailing
logLevel = 2

[USRP]
txPort = 34011
"""
    assert ini_file.section_values(text, "GENERAL") == {"useEmulator": "true", "logLevel": "2"}
    assert ini_file.section_values(text, "USRP")["txPort"] == "34011"


def test_set_values_updates_and_preserves_other_keys():
    text = "[AMBE_AUDIO]\ngatewayDmrId = 0\ntxTg = 9\n\n[USRP]\ntxPort = 1\n"
    updated = ini_file.set_values(text, "AMBE_AUDIO", {"gatewayDmrId": "3120001", "txTg": "3100"})
    assert ini_file.section_values(updated, "AMBE_AUDIO") == {"gatewayDmrId": "3120001", "txTg": "3100"}
    assert ini_file.section_values(updated, "USRP")["txPort"] == "1"


def test_set_values_appends_missing_section():
    text = "[GENERAL]\nlogLevel = 2\n"
    updated = ini_file.set_values(text, "USRP", {"txPort": "34011", "rxPort": "34012"})
    assert ini_file.section_values(updated, "USRP") == {"txPort": "34011", "rxPort": "34012"}
    assert ini_file.section_values(updated, "GENERAL")["logLevel"] == "2"
