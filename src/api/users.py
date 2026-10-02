"""Dashboard user accounts with roles, stored in a JSON file.

Roles:
  - admin: everything, including managing users and reading the audit log;
  - operator: changes settings, macros, announcements, audio;
  - viewer: read-only (status, settings, activity, listening);
  - listener: only the listening page at /listen, nothing of the dashboard.

The account from `MOREOPENREPEATER_AUTH_USER`/`_PASSWORD` (see `api.auth`)
is always an admin and lives outside this store, so a lost users file can
never lock the owner out. Passwords are hashed with scrypt from the
standard library -- no extra dependency to build on a Pi.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from typing import Literal, Optional

from .persistence import StateStore

Role = Literal["admin", "operator", "viewer", "listener"]
ROLES: tuple[Role, ...] = ("admin", "operator", "viewer", "listener")

# 16 MiB of memory and roughly 50 ms per check on a Raspberry Pi 4.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return secrets.compare_digest(actual, bytes.fromhex(digest))


# Checked against when the username doesn't exist, so a login for an
# unknown user takes as long as one with a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class UserError(ValueError):
    pass


@dataclass(frozen=True)
class User:
    username: str
    role: Role
    password_hash: str


class UserStore:
    """`store=None` keeps users in memory (tests)."""

    def __init__(self, store: Optional[StateStore] = None, reserved_username: Optional[str] = None) -> None:
        self._store = store
        self.reserved_username = reserved_username
        self._lock = threading.Lock()
        data = store.load() if store else None
        self._users: dict[str, User] = {
            u["username"]: User(u["username"], u["role"], u["password_hash"]) for u in (data or {}).get("users", [])
        }

    def __bool__(self) -> bool:
        return bool(self._users)

    def list(self) -> list[User]:
        return sorted(self._users.values(), key=lambda u: u.username.lower())

    def get(self, username: str) -> Optional[User]:
        return self._users.get(username)

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self._users.get(username)
        ok = check_password(password, user.password_hash if user else _DUMMY_HASH)
        return user if user and ok else None

    def add(self, username: str, password: str, role: Role) -> User:
        username = username.strip()
        if not username:
            raise UserError("Username is required")
        if username == self.reserved_username:
            raise UserError(f"{username!r} is the built-in admin account")
        with self._lock:
            if username in self._users:
                raise UserError(f"User {username!r} already exists")
            if not self._users and role != "admin" and self.reserved_username is None:
                raise UserError("The first user must be an admin, or nobody could manage users")
            user = User(username, role, hash_password(password))
            self._users[username] = user
            self._save()
        return user

    def update(self, username: str, password: Optional[str] = None, role: Optional[Role] = None) -> User:
        with self._lock:
            user = self._users.get(username)
            if user is None:
                raise KeyError(username)
            updated = User(
                username,
                role or user.role,
                hash_password(password) if password else user.password_hash,
            )
            self._check_admin_remains({**self._users, username: updated})
            self._users[username] = updated
            self._save()
        return updated

    def delete(self, username: str) -> None:
        with self._lock:
            if username not in self._users:
                raise KeyError(username)
            remaining = {k: v for k, v in self._users.items() if k != username}
            self._check_admin_remains(remaining)
            self._users = remaining
            self._save()

    def _check_admin_remains(self, users: dict[str, User]) -> None:
        if self.reserved_username is None and users and not any(u.role == "admin" for u in users.values()):
            raise UserError("At least one admin is needed to manage users")

    def export(self) -> dict:
        return {"users": [{"username": u.username, "role": u.role, "password_hash": u.password_hash} for u in self.list()]}

    def parse(self, data: dict) -> dict[str, User]:
        """Checks users from `export()` (e.g. a backup) without applying them.
        The built-in admin's name is skipped: that account lives outside the store."""
        users: dict[str, User] = {}
        entries = data.get("users") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise UserError("The user list is malformed")
        for entry in entries:
            try:
                user = User(str(entry["username"]).strip(), entry["role"], str(entry["password_hash"]))
            except (KeyError, TypeError):
                raise UserError("A user entry is malformed") from None
            if not user.username or user.role not in ROLES or not user.password_hash.startswith("scrypt$"):
                raise UserError(f"User {user.username!r} is malformed")
            if user.username != self.reserved_username:
                users[user.username] = user
        self._check_admin_remains(users)
        return users

    def replace(self, users: dict[str, User]) -> None:
        with self._lock:
            self._users = dict(users)
            self._save()

    def _save(self) -> None:
        if self._store is not None:
            self._store.save(self.export())
