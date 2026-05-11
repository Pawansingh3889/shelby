from __future__ import annotations

from typing import Optional

import numpy as np
import pyttsx3
import sounddevice as sd
from faster_whisper import WhisperModel


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
DEFAULT_STT_MODEL = "tiny.en"


_stt: Optional[WhisperModel] = None


def get_stt(model_size: str = DEFAULT_STT_MODEL) -> WhisperModel:
    global _stt
    if _stt is None:
        print(f"[loading whisper {model_size} (first run downloads ~75MB)]", flush=True)
        _stt = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _stt


def transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    model = get_stt()
    audio_f32 = audio.astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio_f32, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def speak(text: str, rate: int = 185) -> None:
    if not text.strip():
        return
    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.say(text)
    engine.runAndWait()


class Recorder:
    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time, status):
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=DTYPE)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._frames:
            return np.zeros(0, dtype=DTYPE)
        return np.concatenate(self._frames).flatten()
