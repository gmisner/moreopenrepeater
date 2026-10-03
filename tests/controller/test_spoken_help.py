from controller.macros import Macro
from controller.spoken_help import PAGE_SIZE, help_text, spoken_digits
from controller.state_machine import RepeaterConfig


def test_spoken_digits():
    assert spoken_digits("*81") == "star eight one"
    assert spoken_digits("#0a") == "pound zero A"


def test_reads_descriptions_and_falls_back_to_the_action():
    macros = [
        Macro("*0", "", action="help"),
        Macro("*1", "Time of day.", action="time"),
        Macro("*2", "", action="weather"),
    ]
    assert help_text(macros, RepeaterConfig()) == "Repeater commands. star one, Time of day. star two, weather alerts."


def test_hides_sensitive_and_coded_macros_by_default():
    macros = [
        Macro("*1", "time", action="time"),
        Macro("*90", "transmitter off", action="tx_disable"),
        Macro("*91", "link radio on", action="link_radio_on"),
        Macro("*9", "gate", command="open", action="say", needs_code=True),
    ]
    assert help_text(macros, RepeaterConfig()) == "Repeater commands. star one, time."


def test_help_hidden_overrides_the_default():
    assert Macro("*1", "time", action="time", help_hidden=True).hidden_from_help
    assert not Macro("*90", "off", action="tx_disable", help_hidden=False).hidden_from_help
    macros = [Macro("*1", "time", action="time", help_hidden=True), Macro("*90", "off", action="tx_disable", help_hidden=False)]
    assert help_text(macros, RepeaterConfig()) == "Repeater commands. star nine zero, off."


def test_phone_patch_and_mailbox_codes():
    config = RepeaterConfig(autopatch_enabled=True, mailbox_enabled=True)
    text = help_text([], config)
    assert "star six and a phone number, phone patch." in text
    assert "star seven and a mailbox number, leave a message." in text
    assert "play your messages." in text


def test_held_phone_patch_is_left_out():
    config = RepeaterConfig(autopatch_enabled=True)
    assert help_text([], config, held=lambda feature: "net" if feature == "autopatch" else "") == "There are no commands to list."


def test_reads_everything_without_a_next_page_macro():
    macros = [Macro("*0", "", action="help")] + [Macro(f"*{i + 10}", f"cmd {i}", action="time") for i in range(PAGE_SIZE + 3)]
    text = help_text(macros, RepeaterConfig())
    assert text.count(", cmd") == PAGE_SIZE + 3
    assert "For more" not in text


def test_pages_point_to_the_next_help_macro():
    macros = [
        Macro("*0", "", action="help"),
        Macro("*02", "", command="2", action="help"),
    ] + [Macro(f"*{i + 10}", f"cmd {i}", action="time") for i in range(PAGE_SIZE + 3)]
    first = help_text(macros, RepeaterConfig())
    assert first.count(", cmd") == PAGE_SIZE
    assert first.endswith("For more, key star zero two.")
    second = help_text(macros, RepeaterConfig(), "2")
    assert second.startswith("Commands, page 2.")
    assert second.count(", cmd") == 3
    assert "For more" not in second
    assert help_text(macros, RepeaterConfig(), "3") == "There are no more commands."
