from __future__ import annotations

from dataclasses import dataclass
"""MailboxConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class MailboxConfig:
    # Voice mailbox (controller.mailbox, api.mailbox).
    mailbox_enabled: bool = False
    mailbox_leave_code: str = '*7'
    mailbox_play_code: str = '*8'
    mailbox_delete_code: str = '*9'
    mailbox_max_seconds: float = 60.0
    mailbox_retention_days: float = 14.0
    # "messages waiting for mailbox 12"; 0 = never
    mailbox_reminder_minutes: float = 60.0  # "messages waiting for mailbox 12"; 0 = never
