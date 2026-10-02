# Public listening

Two ways to let people hear the repeater without a dashboard account, both on the
dashboard's **Public listening** page and both off until you turn them on.

## The public page

Turn on **Public page** and anyone who can reach the controller can open
`http://<controller>:8000/listen` without signing in. It shows the callsign, your
text (the frequency and tone, say), whether the repeater is on the air, and
whether a net is in session. With **Visitors can listen live** on, it has a
Listen button that plays what's transmitted, the same audio as the dashboard's
Listen live.

The page can't change anything. It reads `/api/public/status` and
`/ws/public/audio`, which answer only while the page is on; the rest of the API
still needs a sign-in.

Each listener takes about 256 kbps of the controller's upload (uncompressed
16 kHz audio), so the number at once is capped (20 by default, at most 3 from one
address). For a bigger audience, use a Broadcastify feed instead: Broadcastify
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
Caddy and nginx set.

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
