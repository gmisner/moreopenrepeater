# Transcripts

The controller can turn recordings and mailbox messages into text. The callsigns heard
are picked out as tags, whether written out ("W1AW") or said phonetically ("whiskey
one alpha whiskey"), and the Activity page can search transcripts by word or callsign.

Transcripts are off until you pick an engine under **Activity → Transcripts**.
Recordings have to be turned on too (Activity → Recordings). Once an engine is picked,
new clips are transcribed in the background within about 15 seconds, one at a time,
newest first. The 50 most recent recordings without a transcript are picked up as well.

## Vosk, on the controller

Offline, free, and nothing leaves the controller. It's less accurate than Whisper,
especially with noisy signals.

```bash
sudo /opt/moreopenrepeater/.venv/bin/pip install vosk
cd /tmp
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
sudo mv vosk-model-small-en-us-0.15 /opt/moreopenrepeater/data/vosk-model
sudo chown -R moreopenrepeater: /opt/moreopenrepeater/data/vosk-model
sudo systemctl restart moreopenrepeater
```

The small English model (40 MB) needs about 300 MB of memory and keeps up on a
Raspberry Pi 4. To keep the model somewhere else, set `MOREOPENREPEATER_VOSK_MODEL` to
its folder in `/etc/moreopenrepeater/env`.

## A Whisper server

Any server with OpenAI's `/v1/audio/transcriptions` endpoint works. Set the full
address and the model name:

| Server | Address | Model |
| ------ | ------- | ----- |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `whisper-server --inference-path /v1/audio/transcriptions` | `http://host:8080/v1/audio/transcriptions` | anything (it uses the model it loaded) |
| [Speaches](https://github.com/speaches-ai/speaches) (faster-whisper) | `http://host:8000/v1/audio/transcriptions` | e.g. `Systran/faster-distil-whisper-small.en` |
| OpenAI | `https://api.openai.com/v1/audio/transcriptions` | `whisper-1` or `gpt-4o-mini-transcribe` |

A server on your network keeps the audio at home and is far more accurate than Vosk.
OpenAI sends every recording to OpenAI and is billed per minute. It needs an API key
in `/etc/moreopenrepeater/env`:

```bash
MOREOPENREPEATER_TRANSCRIPTION_API_KEY=sk-...
```

The key is used for any server that asks for one. Each request includes a short prompt
naming the repeater's callsign, which helps Whisper spell callsigns.

## Where transcripts live

Each transcript is a `.txt` file beside its recording or message, and is deleted with
it. An empty file means nothing was understood, so that clip isn't tried again. If a
clip fails, transcription pauses for 5 minutes and then retries; after 3 failures that
clip is skipped. Changing the settings retries everything.
