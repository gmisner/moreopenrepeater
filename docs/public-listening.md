# Public listening

Two ways to let people hear the repeater without the dashboard, both on the
dashboard's **Public listening** page and both off until you turn them on.

## The listening page

The page at `http://<controller>:8000/listen` shows the callsign, your text (the
frequency and tone, say), whether the repeater is on the air, and whether a net is
in session. With **Visitors can listen live** on, it has a Listen button that
plays what's transmitted, the same audio as the dashboard's Listen live.

**Listening page** sets who can open it:

- **Off**: nobody; `/listen` is "not found".
- **Signed-in accounts only**: `/listen` asks for a sign-in first, then comes back.
  Any dashboard account works. For people who should only listen (club members,
  say), add a **Listener** account on the Users page: it signs in straight to
  `/listen` and is refused the dashboard and the rest of the API. Delete the
  account to take it away.
- **Anyone, without signing in**: open to whoever can reach the controller.

Switching it off, or from Anyone to Signed-in only, takes effect at once: the page
says it's off (or asks for a sign-in), and listeners who no longer qualify are cut
off.

The page can't change anything. It reads `/api/public/status` and
`/ws/public/audio`, which follow the setting above; the rest of the API still
needs a dashboard account.

Each listener takes about 256 kbps of the controller's upload (uncompressed
16 kHz audio), so the number at once is capped (20 by default). Listeners who
aren't signed in are also limited to 3 from one address. For a bigger audience, use a Broadcastify feed instead: Broadcastify
serves the listeners, and the controller sends one 16 kbps stream.

### Putting it on the internet

The page is on the same port as the dashboard. If you forward that port, the
dashboard's sign-in page is on the internet too. It's better to put a reverse
proxy in front that passes only what the public page needs. With Caddy:

```
listen.example.org {
	@public path /listen /api/public/* /ws/public/* /style.css /theme.js /icon.svg /js/public_listen.js
	handle @public {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		redir /listen
	}
}
```

Behind a proxy, the per-address limit uses the `X-Forwarded-For` header, which
Caddy and nginx set. For **Signed-in accounts only**, also pass `/login`,
`/js/login.js`, `/api/login` and `/api/logout`, so listeners can sign in and out.

## Broadcastify or another Icecast server

The **Broadcastify / Icecast feed** card (admins only) sends what's on the air to
an Icecast server as a 16 kbps mono MP3 stream at 22.05 kHz, the format
Broadcastify asks for. It needs ffmpeg:

```bash
sudo apt install ffmpeg
```

1. Apply for a feed at [Broadcastify](https://www.broadcastify.com/). Once it's
   approved, the feed's technical details list a server, port, mount and password.
2. Enter them on the card, turn on **Stream** and save.
3. The tag on the card goes from Connecting to Streaming after a few seconds. A
   wrong password or mount shows the server's answer, and the controller tries
   again after 5 seconds, then less often (up to every 5 minutes).

When nothing is on the air the stream carries silence, since Icecast servers drop
a source that stops sending. The feed settings and password are kept in
`data/stream.json` on the controller, out of the settings API and backups. ffmpeg
gets the password on its command line, so other accounts on the controller could
see it in the process list; on a single-user Raspberry Pi that's only you.

Use **Older server** only for Icecast before 2.4, which needs the old SOURCE
method instead of HTTP PUT.
