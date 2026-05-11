from __future__ import annotations

import os
import queue
import threading
from typing import Optional

import numpy as np
import openwakeword
import sounddevice as sd
import webrtcvad
from openwakeword.model import Model as WakeModel


# Set by an HTTP /wake POST (from the web UI's click-to-wake button) to
# short-circuit the wake-word listener. wait_for_wake polls this event
# between mic chunks and returns immediately when set.
MANUAL_WAKE = threading.Event()


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

WAKE_MODEL = os.environ.get("SHELBY_WAKE_MODEL", "hey_jarvis")
WAKE_THRESHOLD = float(os.environ.get("SHELBY_WAKE_THRESHOLD", "0.5"))

VAD_AGGRESSIVENESS = int(os.environ.get("SHELBY_VAD_AGGRESSIVENESS", "2"))
VAD_FRAME_MS = 30
VAD_FRAME_SAMPLES = (SAMPLE_RATE * VAD_FRAME_MS) // 1000
SILENCE_HANG_MS = int(os.environ.get("SHELBY_SILENCE_HANG_MS", "1500"))
MAX_UTTERANCE_S = int(os.environ.get("SHELBY_MAX_UTTERANCE_S", "30"))
MIN_UTTERANCE_S = float(os.environ.get("SHELBY_MIN_UTTERANCE_S", "0.4"))


_wake_model: Optional[WakeModel] = None


def get_wake_model() -> WakeModel:
    global _wake_model
    if _wake_model is None:
        print(f"[loading wake-word model '{WAKE_MODEL}' (first run downloads ~30MB)]", flush=True)
        openwakeword.utils.download_models(model_names=[WAKE_MODEL])
        _wake_model = WakeModel(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
    return _wake_model


def wait_for_wake() -> None:
    model = get_wake_model()
    chunk_samples = 1280

    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(indata.copy().flatten())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=chunk_samples,
        callback=_cb,
    ):
        while True:
            if MANUAL_WAKE.is_set():
                MANUAL_WAKE.clear()
                return
            try:
                chunk = q.get(timeout=0.1)
            except queue.Empty:
                continue
            scores = model.predict(chunk)
            for name, score in scores.items():
                if score >= WAKE_THRESHOLD:
                    return


def record_until_silence(max_pre_speech_ms: Optional[int] = None) -> np.ndarray:
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    pre_speech_cap_ms = max_pre_speech_ms if max_pre_speech_ms is not None else MAX_UTTERANCE_S * 1000

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
            if not voice_seen and elapsed_ms >= pre_speech_cap_ms:
                return np.zeros(0, dtype=DTYPE)

    if not frames:
        return np.zeros(0, dtype=DTYPE)
    return np.concatenate(frames).flatten()
