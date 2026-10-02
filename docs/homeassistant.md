# Home Assistant

A DTMF macro can call a Home Assistant automation: turn on the porch light at the
repeater site, start a generator test, or anything else an automation can do. The
repeater only sends the request; Home Assistant's automations decide what happens.

Anyone who hears the digits can key them again. Give these macros a
[one-time code](../README.md#beyond-openrepeater) (or at least a PIN in the
pattern), and don't use them for anything safety-related, like locks or garage
doors. Home Assistant's own docs give the same advice for webhooks.

## Set up

1. On the dashboard's **Macros** page, enter the **Home Assistant address** as the
   controller reaches it (for example `http://homeassistant.local:8123`) and save.
2. Add a macro with the action **Home Assistant** and either a webhook ID or
   `event:` and an event type.
3. Use **Send a test** on the Home Assistant card to check it gets through.

After the request, the repeater says "Done." or "Failed." (a switch on the card
turns that off). The same target is called at most once every 10 seconds, so a
repeated macro doesn't flap a light.

## Webhooks (no token needed)

Make an automation with a webhook trigger. Its webhook ID works like a password,
so make it long and random:

```yaml
triggers:
  - trigger: webhook
    webhook_id: "repeater-porch-7f3c9a1e"
    allowed_methods: [POST]
    local_only: true
actions:
  - action: light.turn_on
    target:
      entity_id: light.porch
```

Then use `repeater-porch-7f3c9a1e` as the macro's argument. Keep `local_only`
on if the controller is on the same network as Home Assistant.

## Events (needs a token)

`event:repeater_alarm` fires the `repeater_alarm` event through Home Assistant's
REST API. That needs a long-lived access token (your profile in Home Assistant,
**Security** tab, **Long-lived access tokens**). Put it in the service's
environment, not the dashboard, so it stays out of settings and backups:

```bash
echo 'MOREOPENREPEATER_HOMEASSISTANT_TOKEN=<token>' | sudo tee -a /etc/moreopenrepeater/env
sudo systemctl restart moreopenrepeater
```

An automation then triggers on it:

```yaml
triggers:
  - trigger: event
    event_type: repeater_alarm
```

## What the repeater sends

Both kinds get the same JSON (`trigger.json` in a webhook automation,
`trigger.event.data` for an event):

```json
{"pattern": "*7", "source": "DTMF (alice)", "repeater": "W1AW", "time": "2026-10-01T19:30:00-05:00"}
```

`source` says who ran it: `DTMF`, or `DTMF (<user>)` when a one-time code was
keyed, or `schedule` and the like for macros run some other way. A test from the
dashboard adds `"test": true`.
