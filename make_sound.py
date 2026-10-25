#!/usr/bin/env python3
"""Generate the original two-note timer chime without third-party assets."""

import math
import struct
import wave
from pathlib import Path


def make_sound(path):
    rate = 44100
    frames = bytearray()
    for index in range(int(rate * 2.2)):
        now = index / rate
        value = 0.0
        for start, fundamental in ((0.05, 1046.5), (0.7, 1318.51)):
            elapsed = now - start
            if elapsed >= 0:
                envelope = min(1, elapsed / 0.008) * math.exp(-3.8 * elapsed)
                tone = (
                    math.sin(2 * math.pi * fundamental * elapsed)
                    + 0.3 * math.sin(2 * math.pi * fundamental * 2.01 * elapsed)
                    + 0.12 * math.sin(2 * math.pi * fundamental * 3.97 * elapsed)
                )
                value += 0.25 * envelope * tone
        frames.extend(struct.pack("<h", int(max(-1, min(1, value)) * 32767)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


if __name__ == "__main__":
    make_sound(Path(__file__).resolve().parent / "assets/ding.wav")
