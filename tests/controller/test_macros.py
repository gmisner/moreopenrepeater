from controller.events import SendLinkCommand
from controller.macros import DTMFCommandDecoder, Macro


def test_macro_build_command_produces_send_link_command():
    macro = Macro(pattern="*81", description="disconnect all", command="disconnect_all", node_id="*")

    assert macro.build_command() == SendLinkCommand(node_id="*", command="disconnect_all")


def test_macro_node_id_defaults_to_empty_string():
    macro = Macro(pattern="*70", description="status", command="status")

    assert macro.build_command() == SendLinkCommand(node_id="", command="status")


def test_set_macros_replaces_the_active_macro_list():
    decoder = DTMFCommandDecoder([Macro(pattern="*81", description="old", command="old_command")])

    decoder.set_macros([Macro(pattern="*99", description="new", command="new_command")])

    assert decoder.handle_digit("*", now=0.0) is None
    assert decoder.handle_digit("8", now=0.1) is None
    assert decoder.handle_digit("1", now=0.2) is None  # old macro no longer registered

    assert decoder.handle_digit("*", now=1.0) is None
    assert decoder.handle_digit("9", now=1.1) is None
    command = decoder.handle_digit("9", now=1.2)
    assert command == SendLinkCommand(node_id="", command="new_command")


def test_list_macros_returns_a_copy_not_the_live_list():
    decoder = DTMFCommandDecoder([Macro(pattern="*81", description="a", command="a")])

    macros = decoder.list_macros()
    macros.append(Macro(pattern="*82", description="b", command="b"))

    assert len(decoder.list_macros()) == 1
