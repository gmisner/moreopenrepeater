# Security policy

moreopenrepeater runs a transmitter, and its dashboard can be reachable from the
internet, so security problems matter: someone who gets in could key up the
repeater, change its settings, place phone calls through the autopatch, or run
an update.

## Supported versions

Fixes go to `main` and are promoted to the `beta` and `stable` release channels.
Only the newest commit on each channel is supported; update from the dashboard's
Updates page to get fixes.

## Reporting a problem

Please report security problems privately through
[GitHub's private vulnerability reporting](https://github.com/gmisner/moreopenrepeater/security/advisories/new),
not in a public issue. Include what's affected, how to reproduce it, and what
someone could do with it. You should get a reply within a week.

Things worth reporting include:

- getting past the dashboard login or a user's role;
- reaching the API, the public listening page or recordings without permission;
- control over the air you shouldn't have: one-time DTMF codes reused, codes
  heard on air, or the autopatch dialing numbers it should refuse;
- anything that runs code or changes files on the Pi, including the updater;
- passwords, tokens or keys leaking into logs, backups or responses.

Problems in AllStarLink, Asterisk or other software moreopenrepeater talks to
are best reported to those projects, though a note here helps if
moreopenrepeater's setup makes them worse.
