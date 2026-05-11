from __future__ import annotations

import os
import queue
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

VAD_AGGRESSIVENESS = int(os.environ.get("JARVIS_VAD_AGGRESSIVENESS", "2"))
VAD_FRAME_MS = 30
VAD_FRAME_SAMPLES = (SAMPLE_RATE * VAD_FRAME_MS) // 1000
SILENCE_HANG_MS = int(os.environ.get("JARVIS_SILENCE_HANG_MS", "800"))
MAX_UTTERANCE_S = int(os.environ.get("JARVIS_MAX_UTTERANCE_S", "30"))
MIN_UTTERANCE_S = float(os.environ.get("JARVIS_MIN_UTTERANCE_S", "0.4"))


def record_until_silence() -> np.ndarray:
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

    frames: list[np.ndarray] = []
    silence_ms = 0
    voice_seen = False
    elapsed_ms = 0

    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def _cb(indata, n, time_info, status):
        q.put(indata.copy().flatten())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=VAD_FRAME_SAMPLES,
        callback=_cb,
    ):
        while True:
            frame = q.get()
            if frame.size != VAD_FRAME_SAMPLES:
                if frame.size < VAD_FRAME_SAMPLES:
                    frame = np.pad(frame, (0, VAD_FRAME_SAMPLES - frame.size))
                else:
                    frame = frame[:VAD_FRAME_SAMPLES]

            frames.append(frame)
            elapsed_ms += VAD_FRAME_MS

            is_speech = vad.is_speech(frame.tobytes(), SAMPLE_RATE)
            if is_speech:
                voice_seen = True
                silence_ms = 0
            else:
                silence_ms += VAD_FRAME_MS

            if voice_seen and silence_ms >= SILENCE_HANG_MS:
                if elapsed_ms / 1000 >= MIN_UTTERANCE_S:
                    break
            if elapsed_ms / 1000 >= MAX_UTTERANCE_S:
                break

    if not frames:
        return np.zeros(0, dtype=DTYPE)
    return np.concatenate(frames).flatten()
