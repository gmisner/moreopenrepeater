import asyncio

import pytest

from link.ami_client import AMIClient


def test_ami_client_connects_logs_in_sends_actions_and_reads_events():
    asyncio.run(_run())


async def _run():
    server = await asyncio.start_server(_fake_asterisk, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        client = AMIClient(host, port, username="admin", secret="secret")
        await client.connect()

        response = await client.send_action({"Action": "Ping"})
        assert response["Response"] == "Success"
        assert response["Ping"] == "Pong"

        event = await client.events().__anext__()
        assert event["Event"] == "TestEvent"

        await client.close()


def test_ami_client_matches_response_by_actionid_despite_interleaved_events():
    """Regression test for a real bug found against live Asterisk: it pushes
    an unsolicited Event (e.g. FullyBooted) immediately after login, ahead of
    the next action's Response. A client that assumes "the next message read
    is my response" gets the event instead -- responses must be matched by
    ActionID."""
    asyncio.run(_run_interleaving())


async def _run_interleaving():
    server = await asyncio.start_server(_interleaving_fake_asterisk, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        client = AMIClient(host, port, username="admin", secret="secret")
        await client.connect()  # server fires an unsolicited event right after login

        response = await client.send_action({"Action": "Ping"})
        assert response["Response"] == "Success"
        assert response["Ping"] == "Pong"

        event = await client.events().__anext__()
        assert event["Event"] == "FullyBooted"

        await client.close()


async def _fake_asterisk(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"Asterisk Call Manager/9.0.0\r\n")
    await writer.drain()

    login = await _read_block(reader)
    assert login["Action"] == "Login"
    assert login["Username"] == "admin"
    assert login["Secret"] == "secret"
    _write_block(writer, {"Response": "Success", "Message": "Authentication accepted", "ActionID": login["ActionID"]})
    await writer.drain()

    ping = await _read_block(reader)
    assert ping["Action"] == "Ping"
    _write_block(writer, {"Response": "Success", "Ping": "Pong", "ActionID": ping["ActionID"]})
    await writer.drain()

    _write_block(writer, {"Event": "TestEvent", "Privilege": "call,all"})
    await writer.drain()

    writer.close()
    await writer.wait_closed()


async def _interleaving_fake_asterisk(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"Asterisk Call Manager/9.0.0\r\n")
    await writer.drain()

    login = await _read_block(reader)
    _write_block(writer, {"Response": "Success", "Message": "Authentication accepted", "ActionID": login["ActionID"]})
    # An unsolicited event lands on the wire before the client sends its next action.
    _write_block(writer, {"Event": "FullyBooted", "Privilege": "system,all", "Status": "Fully Booted"})
    await writer.drain()

    ping = await _read_block(reader)
    _write_block(writer, {"Response": "Success", "Ping": "Pong", "ActionID": ping["ActionID"]})
    await writer.drain()

    writer.close()
    await writer.wait_closed()


def test_ami_client_collects_repeated_output_lines_into_a_list():
    """Regression test for a real bug found against live Asterisk: a Command
    action's response repeats the "Output" header once per line of console
    output (e.g. `rpt stats` is dozens of lines) -- a plain dict assignment
    silently kept only the last line."""
    asyncio.run(_run_multiline_output())


async def _run_multiline_output():
    server = await asyncio.start_server(_multiline_output_fake_asterisk, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()

    async with server:
        client = AMIClient(host, port, username="admin", secret="secret")
        await client.connect()

        response = await client.send_action({"Action": "Command", "Command": "rpt stats 1999"})
        assert response["Output"] == ["line one", "line two", "line three"]

        await client.close()


async def _multiline_output_fake_asterisk(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"Asterisk Call Manager/9.0.0\r\n")
    await writer.drain()

    login = await _read_block(reader)
    _write_block(writer, {"Response": "Success", "Message": "Authentication accepted", "ActionID": login["ActionID"]})
    await writer.drain()

    command = await _read_block(reader)
    writer.write(f"Response: Success\r\nActionID: {command['ActionID']}\r\n".encode())
    for line in ("line one", "line two", "line three"):
        writer.write(f"Output: {line}\r\n".encode())
    writer.write(b"\r\n")
    await writer.drain()

    writer.close()
    await writer.wait_closed()


def _write_block(writer: asyncio.StreamWriter, fields: dict[str, str]) -> None:
    for key, value in fields.items():
        writer.write(f"{key}: {value}\r\n".encode())
    writer.write(b"\r\n")


async def _read_block(reader: asyncio.StreamReader) -> dict[str, str]:
    message: dict[str, str] = {}
    while True:
        line = (await reader.readline()).decode().rstrip("\r\n")
        if line == "":
            return message
        key, _, value = line.partition(": ")
        message[key] = value


def test_ami_client_refuses_header_values_with_line_breaks():
    """A line break would end the header early and let the rest of the
    value inject a second action."""
    async def run():
        client = AMIClient("127.0.0.1", 0, username="admin", secret="secret")
        client._writer = object()  # never reached
        with pytest.raises(ValueError):
            await client.send_action({"Action": "Originate", "Channel": "PJSIP/1@t\r\nAction: Command"})

    asyncio.run(run())
