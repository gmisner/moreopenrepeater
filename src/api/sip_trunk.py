"""The autopatch's SIP trunk, set up in the Asterisk sidecar over AMI.

Everything goes through the same AMI login the calls use, so it works
without file access to /etc/asterisk (or with Asterisk on another host):

  * The modules autopatch needs are loaded with ModuleLoad, in dependency
    order (a runtime load doesn't pull in dependencies), and added to
    modules.conf with UpdateConfig so they load at startup too. ASL3 ships
    with `autoload = no` and PJSIP and RTP not loaded.
  * The trunk is a set of `mor-trunk*` sections in pjsip.conf, written with
    UpdateConfig and applied with a res_pjsip reload. Other sections are
    left alone. AMI has no way to add an `#include`, so the sections can't
    live in a file of their own, and Asterisk drops comments that aren't
    attached to a section when it saves -- which is all of ASL3's stock
    (settings-free) pjsip.conf.
  * Calls in land in the `mor-incoming` dialplan context, which answers and
    hands the call to our AudioSocket server under a UUID made from the
    channel's unique ID; the controller looks the channel up over AMI to
    tell it's a real call. The context goes in custom/extensions.conf, which
    ASL3's extensions.conf includes, so saving doesn't rewrite the node's
    own dialplan (Asterisk reformats the whole file it saves, includes and
    all). Other Asterisk installs get it in extensions.conf.

The SIP password is written to Asterisk and never read back out to the
dashboard.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional

from link.ami_client import AMIClient, AMIMessage

from .autopatch import AMI_TIMEOUT, INCOMING_CONTEXT, PatchSettings, ami_list

ENDPOINT = "mor-trunk"
DIAL_STRING = f"PJSIP/{{number}}@{ENDPOINT}"
_AUTH = "mor-trunk-auth"
_AOR = "mor-trunk-aor"
_REGISTRATION = "mor-trunk-reg"
_IDENTIFY = "mor-trunk-identify"
TRUNK_SECTIONS = (ENDPOINT, _AUTH, _AOR, _REGISTRATION, _IDENTIFY)
DIALPLAN_FILES = ("custom/extensions.conf", "extensions.conf")

# Where providers send calls from, when that isn't the server's own address
# (www.twilio.com/docs/sip-trunking/ip-addresses, "Regional signaling").
TWILIO_SIGNALING = (
    "54.172.60.0/30",
    "54.244.51.0/30",
    "54.171.127.192/30",
    "35.156.191.128/30",
    "54.65.63.192/30",
    "54.169.127.128/30",
    "54.252.254.64/30",
    "177.71.206.192/30",
)

# Load order matters: each after what it depends on.
SIP_MODULES = (
    "res_sorcery_config.so",
    "res_sorcery_memory.so",
    "res_sorcery_astdb.so",
    "codec_ulaw.so",
    "res_pjproject.so",
    "res_rtp_asterisk.so",
    "res_pjsip.so",
    "res_pjsip_session.so",
    "res_pjsip_pubsub.so",
    "res_pjsip_sdp_rtp.so",
    "res_pjsip_nat.so",
    "res_pjsip_caller_id.so",
    "res_pjsip_endpoint_identifier_ip.so",  # identify sections, for calls in
    "res_pjsip_outbound_authenticator_digest.so",
    "res_pjsip_outbound_registration.so",
    "chan_pjsip.so",
)
AUDIOSOCKET_MODULES = ("res_audiosocket.so", "app_audiosocket.so")
DIALPLAN_MODULES = ("pbx_config.so", "func_md5.so")  # the incoming call context
MODULES = SIP_MODULES + AUDIOSOCKET_MODULES + DIALPLAN_MODULES

_LOAD_KEYS = ("load", "require", "preload", "preload-require")
_URI = re.compile(r"^sip:(?:(?P<user>[^@]+)@)?(?P<host>[^:;]+)(?::(?P<port>\d+))?(?P<params>;.*)?$")

Section = tuple[str, list[tuple[str, str]]]


class AsteriskSetupError(Exception):
    pass


@dataclass
class TrunkSettings:
    server: str
    username: str
    password: Optional[str] = None  # None keeps the saved one
    port: int = 5060
    transport: str = "udp"  # udp | tcp
    auth_username: str = ""  # when the provider's login differs from the account
    registers: bool = True
    incoming_from: tuple[str, ...] = ()  # hosts or networks calls come from, besides the server


def parse_config(response: AMIMessage) -> list[Section]:
    """GetConfig's Category-000000 / Line-000000-000000 "key=value" fields."""
    names = {k.split("-", 1)[1]: v for k, v in response.items() if k.startswith("Category-") and isinstance(v, str)}
    lines: dict[str, list[tuple[str, str]]] = {index: [] for index in names}
    for key, value in response.items():
        if key.startswith("Line-") and isinstance(value, str):
            index = key.split("-")[1]
            name, _, setting = value.partition("=")
            lines.setdefault(index, []).append((name.strip(), setting.strip()))
    return [(names[index], lines[index]) for index in sorted(names)]


def _get(lines: list[tuple[str, str]], key: str, default: str = "") -> str:
    return next((value for name, value in lines if name == key), default)


def trunk_sections(trunk: TrunkSettings, transport: str, password: str) -> list[Section]:
    # Leaving out port 5060 lets Asterisk use the provider's DNS SRV records.
    host = trunk.server if trunk.port == 5060 else f"{trunk.server}:{trunk.port}"
    params = ";transport=tcp" if trunk.transport == "tcp" else ""
    sections: list[Section] = [
        (_AUTH, [
            ("type", "auth"),
            ("auth_type", "userpass"),
            ("username", trunk.auth_username or trunk.username),
            ("password", password),
        ]),
        (_AOR, [("type", "aor"), ("contact", f"sip:{host}{params}"), ("qualify_frequency", "60")]),
        (ENDPOINT, [
            ("type", "endpoint"),
            ("transport", transport),
            ("context", INCOMING_CONTEXT),
            ("disallow", "all"),
            ("allow", "ulaw"),
            ("outbound_auth", _AUTH),
            ("aors", _AOR),
            # The usual settings for Asterisk behind a home router.
            ("direct_media", "no"),
            ("rtp_symmetric", "yes"),
            ("force_rport", "yes"),
            ("rewrite_contact", "yes"),
        ]),
        # Without this a call in matches no endpoint, since the provider's
        # From user isn't "mor-trunk".
        (_IDENTIFY, [
            ("type", "identify"),
            ("endpoint", ENDPOINT),
            # SRV lookups need res_resolver_unbound, which ASL3 doesn't
            # load; plain address lookups work without it.
            ("srv_lookups", "no"),
            *(("match", source) for source in (trunk.server, *trunk.incoming_from)),
        ]),
    ]
    if trunk.registers:
        sections.append((_REGISTRATION, [
            ("type", "registration"),
            ("transport", transport),
            ("outbound_auth", _AUTH),
            ("server_uri", f"sip:{host}{params}"),
            ("client_uri", f"sip:{trunk.username}@{host}{params}"),
            ("contact_user", trunk.username),
            ("retry_interval", "60"),
            ("forbidden_retry_interval", "300"),
        ]))
    return sections


def incoming_dialplan(address: str) -> list[tuple[str, str]]:
    """Answer any number the provider sends, then connect to our AudioSocket
    server. AudioSocket needs a UUID, and Asterisk has no UUID function."""
    call_id = "${MOR_ID:0:8}-${MOR_ID:8:4}-${MOR_ID:12:4}-${MOR_ID:16:4}-${MOR_ID:20:12}"
    return [
        ("exten", "_+X.,1,Goto(s,1)"),
        ("exten", "_X.,1,Goto(s,1)"),
        ("exten", "s,1,Answer()"),
        ("same", "n,Set(MOR_ID=${MD5(${UNIQUEID})})"),
        ("same", f"n,AudioSocket({call_id},{address})"),
        ("same", "n,Hangup()"),
    ]


def read_trunk(sections: list[Section]) -> Optional[dict]:
    """The saved trunk, for the dashboard form (without the password)."""
    ours = {name: lines for name, lines in sections if name in TRUNK_SECTIONS}
    if ENDPOINT not in ours or _AOR not in ours:
        return None
    contact = _URI.match(_get(ours[_AOR], "contact"))
    if contact is None:
        return None
    auth = ours.get(_AUTH, [])
    auth_username = _get(auth, "username")
    username = auth_username
    if _REGISTRATION in ours:
        client = _URI.match(_get(ours[_REGISTRATION], "client_uri"))
        username = (client and client["user"]) or auth_username
    matches = [value for key, value in ours.get(_IDENTIFY, []) if key == "match"]
    return {
        "server": contact["host"],
        "port": int(contact["port"] or 5060),
        "transport": "tcp" if "transport=tcp" in (contact["params"] or "") else "udp",
        "username": username,
        "auth_username": auth_username if auth_username != username else "",
        "has_password": bool(_get(auth, "password")),
        "registers": _REGISTRATION in ours,
        "incoming_from": [source for source in matches if source != contact["host"]],
        "answers_calls": _get(ours[ENDPOINT], "context") == INCOMING_CONTEXT,
    }


class _Edits:
    """UpdateConfig's numbered Action-/Cat-/Var-/Value-/Match- fields.

    Asterisk silently drops an AMI message of more than 128 header lines
    (AST_MAX_MANHEADERS), which a whole trunk exceeds, so the edits go out
    in batches."""

    MAX_HEADERS = 100

    def __init__(self) -> None:
        self._edits: list[dict[str, str]] = []

    def add(self, action: str, category: str, var: Optional[str] = None, value: Optional[str] = None, match: Optional[str] = None) -> None:
        edit = {"Action": action, "Cat": category}
        for key, field in (("Var", var), ("Value", value), ("Match", match)):
            if field is not None:
                edit[key] = field
        self._edits.append(edit)

    def section(self, name: str, options: list[tuple[str, str]]) -> None:
        self.add("NewCat", name)
        for key, value in options:
            self.add("Append", name, key, value)

    def batches(self) -> list[AMIMessage]:
        batches: list[AMIMessage] = []
        fields: AMIMessage = {}
        count = 0
        for edit in self._edits:
            if fields and len(fields) + len(edit) > self.MAX_HEADERS:
                batches.append(fields)
                fields, count = {}, 0
            for key, value in edit.items():
                fields[f"{key}-{count:06d}"] = value
            count += 1
        if fields:
            batches.append(fields)
        return batches

    def __bool__(self) -> bool:
        return bool(self._edits)


class SipTrunk:
    def __init__(self, settings: Optional[PatchSettings], ami_factory: Callable[..., AMIClient] = AMIClient) -> None:
        self.settings = settings
        self._ami_factory = ami_factory
        self._lock = asyncio.Lock()

    async def status(self) -> dict:
        result: dict = {
            "configured": self.settings is not None,
            "error": None,
            "missing_modules": [],
            "trunk": None,
            "registration": None,
            "reachability": None,
        }
        if self.settings is None:
            return result
        try:
            async with self._ami() as ami:
                missing = await self._missing_modules(ami)
                result["missing_modules"] = missing
                trunk = read_trunk(await self._read(ami, "pjsip.conf"))
                result["trunk"] = trunk
                if trunk is not None and not set(SIP_MODULES) & set(missing):
                    if trunk["registers"]:
                        result["registration"] = await self._registration(ami)
                    result["reachability"] = await self._reachability(ami)
                    trunk["answers_calls"] = trunk["answers_calls"] and await self._dialplan_loaded(ami)
        except AsteriskSetupError as error:
            result["error"] = str(error)
        return result

    async def enable_modules(self) -> None:
        async with self._lock, self._ami() as ami:
            missing = await self._missing_modules(ami)
            await self._persist_modules(ami)
            failed = []
            for module in missing:
                response = await self._send(ami, {"Action": "ModuleLoad", "LoadType": "load", "Module": module})
                if response.get("Response") != "Success":
                    failed.append(module)
            if failed:
                raise AsteriskSetupError(
                    f"Asterisk couldn't load {', '.join(failed)}. Check that its PJSIP and AudioSocket modules are installed."
                )

    async def save(self, trunk: TrunkSettings) -> None:
        async with self._lock, self._ami() as ami:
            if set(SIP_MODULES) & set(await self._missing_modules(ami)):
                raise AsteriskSetupError("Turn on SIP in Asterisk first.")
            sections = await self._read(ami, "pjsip.conf", create=True)
            existing = {name: lines for name, lines in sections if name in TRUNK_SECTIONS}
            password = trunk.password or _get(existing.get(_AUTH, []), "password")
            if not password:
                raise AsteriskSetupError("Enter the SIP password.")
            edits = _Edits()
            for name in existing:
                edits.add("DelCat", name)
            transport = next(
                (
                    name
                    for name, lines in sections
                    if _get(lines, "type") == "transport" and _get(lines, "protocol", "udp") == trunk.transport
                ),
                None,
            )
            if transport is None:
                # A new transport starts on reload, but changing one needs an
                # Asterisk restart, so it's never rewritten once it exists.
                transport = f"mor-transport-{trunk.transport}"
                edits.section(transport, [("type", "transport"), ("protocol", trunk.transport), ("bind", "0.0.0.0")])
            for name, options in trunk_sections(trunk, transport, password):
                edits.section(name, options)
            await self._update(ami, "pjsip.conf", edits)
            await self._reload_pjsip(ami)
            if trunk.registers:
                # A rejected registration stops retrying, and a reload that
                # leaves its section unchanged doesn't restart it.
                await self._require(ami, {"Action": "PJSIPRegister", "Registration": _REGISTRATION})
            await self._write_dialplan(ami)

    async def remove(self) -> None:
        async with self._lock, self._ami() as ami:
            edits = _Edits()
            for name, _lines in await self._read(ami, "pjsip.conf"):
                if name in TRUNK_SECTIONS:
                    edits.add("DelCat", name)
            if edits:
                await self._update(ami, "pjsip.conf", edits)
                if "res_pjsip.so" not in await self._missing_modules(ami):
                    await self._reload_pjsip(ami)
            if await self._remove_dialplan(ami):
                await self._reload_dialplan(ami)

    async def _write_dialplan(self, ami: AMIClient) -> None:
        """Into the first file Asterisk's dialplan actually includes."""
        assert self.settings is not None
        await self._remove_dialplan(ami)
        lines = incoming_dialplan(self.settings.address)
        for filename in DIALPLAN_FILES:
            response = await self._send(ami, {"Action": "GetConfig", "Filename": filename})
            if response.get("Response") != "Success":
                created = await self._send(ami, {"Action": "CreateConfig", "Filename": filename})
                if created.get("Response") != "Success":
                    continue  # no custom/ directory: not ASL3
            edits = _Edits()
            edits.section(INCOMING_CONTEXT, lines)
            await self._update(ami, filename, edits)
            await self._reload_dialplan(ami)
            if await self._dialplan_loaded(ami):
                return
            await self._remove_dialplan(ami)
        raise AsteriskSetupError(
            f"Asterisk didn't load the {INCOMING_CONTEXT} dialplan for calls in. Check that extensions.conf is in use."
        )

    async def _remove_dialplan(self, ami: AMIClient) -> bool:
        removed = False
        for filename in DIALPLAN_FILES:
            response = await self._send(ami, {"Action": "GetConfig", "Filename": filename})
            if response.get("Response") != "Success":
                continue
            names = [name for name, _lines in parse_config(response)]
            if INCOMING_CONTEXT in names:
                edits = _Edits()
                for _ in range(names.count(INCOMING_CONTEXT)):
                    edits.add("DelCat", INCOMING_CONTEXT)
                await self._update(ami, filename, edits)
                removed = True
        return removed

    async def _reload_dialplan(self, ami: AMIClient) -> None:
        await self._require(ami, {"Action": "ModuleLoad", "LoadType": "reload", "Module": "pbx_config.so"})

    async def _dialplan_loaded(self, ami: AMIClient) -> bool:
        return await self._list(ami, {"Action": "ShowDialPlan", "Context": INCOMING_CONTEXT}) is not None

    # -- AMI -----------------------------------------------------------------

    @asynccontextmanager
    async def _ami(self) -> AsyncIterator[AMIClient]:
        if self.settings is None:
            raise AsteriskSetupError("Asterisk isn't connected.")
        s = self.settings
        ami = self._ami_factory(s.ami_host, s.ami_port, s.ami_username, s.ami_secret)
        try:
            try:
                await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
            except (OSError, ConnectionError, asyncio.TimeoutError) as error:
                raise AsteriskSetupError(f"Couldn't reach Asterisk at {s.ami_host}:{s.ami_port}: {error or 'timed out'}")
            yield ami
        except (OSError, ConnectionError, asyncio.TimeoutError) as error:
            raise AsteriskSetupError(f"Lost the connection to Asterisk: {error or 'timed out'}")
        finally:
            try:
                await ami.close()
            except OSError:
                pass

    async def _send(self, ami: AMIClient, fields: AMIMessage) -> AMIMessage:
        return await asyncio.wait_for(ami.send_action(fields), AMI_TIMEOUT)

    async def _require(self, ami: AMIClient, fields: AMIMessage) -> AMIMessage:
        response = await self._send(ami, fields)
        if response.get("Response") != "Success":
            message = str(response.get("Message") or "unknown error")
            if "ermission denied" in message:
                message += " (the AMI login needs config and system write permission)"
            raise AsteriskSetupError(f"Asterisk refused {fields['Action']}: {message}")
        return response

    async def _missing_modules(self, ami: AMIClient) -> list[str]:
        missing = []
        for module in MODULES:
            response = await self._send(ami, {"Action": "ModuleCheck", "Module": module})
            if response.get("Response") != "Success":
                missing.append(module)
        return missing

    async def _persist_modules(self, ami: AMIClient) -> None:
        """Make modules.conf load them at startup, unless it autoloads everything."""
        sections = await self._read(ami, "modules.conf")
        lines = next((lines for name, lines in sections if name == "modules"), None)
        if lines is None:
            return
        autoload = _get(lines, "autoload").lower() in ("yes", "true", "on", "1")
        loaded = {value for key, value in lines if key in _LOAD_KEYS}
        blocked = {value for key, value in lines if key == "noload"}
        edits = _Edits()
        for module in MODULES:
            if module in blocked:
                edits.add("Delete", "modules", "noload", match=module)
            if not autoload and module not in loaded:
                edits.add("Append", "modules", "load", module)
        await self._update(ami, "modules.conf", edits)

    async def _read(self, ami: AMIClient, filename: str, create: bool = False) -> list[Section]:
        response = await self._send(ami, {"Action": "GetConfig", "Filename": filename})
        if response.get("Response") == "Success":
            return parse_config(response)
        if "not found" not in str(response.get("Message", "")).lower():
            raise AsteriskSetupError(f"Asterisk refused GetConfig: {response.get('Message')}")
        if create:
            await self._require(ami, {"Action": "CreateConfig", "Filename": filename})
        return []

    async def _update(self, ami: AMIClient, filename: str, edits: _Edits) -> None:
        for fields in edits.batches():
            await self._require(ami, {
                "Action": "UpdateConfig",
                "SrcFilename": filename,
                "DstFilename": filename,
                "Reload": "no",
                **fields,
            })

    async def _reload_pjsip(self, ami: AMIClient) -> None:
        await self._require(ami, {"Action": "ModuleLoad", "LoadType": "reload", "Module": "res_pjsip.so"})

    async def _list(self, ami: AMIClient, fields: AMIMessage) -> Optional[list[AMIMessage]]:
        return await ami_list(ami, fields)

    async def _registration(self, ami: AMIClient) -> str:
        for event in await self._list(ami, {"Action": "PJSIPShowRegistrationsOutbound"}) or []:
            if event.get("ObjectName") == _REGISTRATION:
                return str(event.get("Status") or "Unknown")
        return "Unknown"

    async def _reachability(self, ami: AMIClient) -> Optional[str]:
        """The provider's answer to Asterisk's OPTIONS pings, once it's had one."""
        for event in await self._list(ami, {"Action": "PJSIPShowEndpoint", "Endpoint": ENDPOINT}) or []:
            if event.get("Event") == "ContactStatusDetail":
                return str(event.get("Status") or "Unknown")
        return None
