# Voice mailbox

Stations can leave each other short voice messages over the air. It needs live
audio, and it's off until you turn it on on the **Mailbox** page.

## Setting up

1. On the Mailbox page, turn on the mailbox and save.
2. Add a mailbox for each station that wants one: a number (1 to 6 digits), whose it
   is, and a PIN (4 to 8 digits). The PIN is never shown again; leave it blank when
   editing to keep it.

## Over the air

With the default codes, for mailbox 12 with PIN 1234:

| To                 | Key             | Then                                    |
| ------------------ | --------------- | --------------------------------------- |
| Leave a message    | `*7 12 #`       | Unkey, wait for the prompt, key up and talk |
| Play the messages  | `*8 12 * 1234 #` | Listen: oldest first, each with when it was left |
| Delete them        | `*9 12 * 1234 #` |                                         |

Unkeying works in place of the final `#`. The transmission after "Key up and leave
your message" is recorded instead of repeated, up to the longest message setting;
nobody keying up within a minute cancels it.

A mailbox holds 10 messages. Every so often (an hour by default, never during a net)
the repeater says which mailboxes have messages waiting. Messages are deleted after
14 days by default.

## Keep in mind

- A PIN keyed over the air can be heard by anyone listening. Five wrong PINs lock a
  mailbox for ten minutes, but the mailbox is for convenience, not secrets.
- Admins can listen to and delete any message on the dashboard. Everything that
  happens to a mailbox (left, played, deleted, wrong PIN) is in the audit log.
- Mailboxes and messages are kept in `data/mailbox/`, apart from the settings, and
  aren't in backups.
