"""Morse/CW code generation for station identification.

Timing follows the standard "PARIS" convention: a dot is 1 unit, a dash is
3 units, the gap between symbols within a character is 1 unit, the gap
between characters is 3 units, and the gap between words is 7 units, where
unit duration in seconds is 1.2/wpm -- calibrated so that sending the word
"PARIS" followed by a word gap takes exactly 50 units.
"""
from __future__ import annotations

import math

import numpy as np

MORSE_CODE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    "/": "-..-.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "=": "-...-",
}


def morse_tone(
    text: str,
    wpm: float,
    tone_hz: float,
    sample_rate: int,
    amplitude: float = 0.3,
) -> np.ndarray:
    """Generate a keyed CW tone spelling out text (unknown characters skipped)."""
    dot_seconds = 1.2 / wpm
    segments: list[tuple[bool, float]] = []  # (is_tone, duration_units)
    started = False

    for word in text.upper().split(" "):
        word_started = False
        for char in word:
            code = MORSE_CODE.get(char)
            if code is None:
                continue
            if word_started:
                segments.append((False, 3))  # inter-character gap
            elif started:
                segments.append((False, 7))  # inter-word gap
            for i, symbol in enumerate(code):
                if i > 0:
                    segments.append((False, 1))  # intra-character gap
                segments.append((True, 1 if symbol == "." else 3))
            started = True
            word_started = True

    blocks = []
    for is_tone, units in segments:
        num_samples = round(units * dot_seconds * sample_rate)
        if is_tone:
            t = np.arange(num_samples) / sample_rate
            blocks.append(amplitude * np.sin(2 * math.pi * tone_hz * t))
        else:
            blocks.append(np.zeros(num_samples))

    if not blocks:
        return np.zeros(0)
    return np.concatenate(blocks)
