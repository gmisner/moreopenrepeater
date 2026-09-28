"""Turns the controller's `PlayAudio(clip=...)` commands into real audio.

`wav` reads/writes WAV files, `tones` generates the built-in courtesy and
timeout tones, `tts` wraps whichever local text-to-speech engine is
installed, and `renderer.ClipRenderer` combines them with the repeater
config and uploaded assets to produce the samples for a named clip.
"""
